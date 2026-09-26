import asyncio
from dataclasses import replace

import pytest

from app.config import CRITIC_MODEL, MODEL_LADDER, ORCHESTRATOR_MODEL, RunBudget
from app.interactions import AgentResult, ModelResult, SnapshotError
from app.orchestrator import TaskSpec, run_task
from app.schemas import Plan, PlanStep
from app.state_log import StateLog

CHEAP, MID, STRONG = MODEL_LADDER[0], MODEL_LADDER[1], MODEL_LADDER[-1]
HIDDEN_SOURCE = "class InventoryReportSpec: SECRET_HIDDEN_TEST_BODY"

SPEC = TaskSpec(
    name="t", task="fix the script", category="coding_python",
    workspace={"inventory_report.py": "broken", "SPEC.md": "spec text"},
    hidden_tests={"test_hidden.py": HIDDEN_SOURCE},
)


def step(title, modifies=True):
    return PlanStep(title=title, instruction=f"do {title} carefully", acceptance=f"{title} works", modifies_code=modifies)


PLAN3 = Plan(steps=[step("diagnose", False), step("fix", True), step("verify", False)])  # gate = step 2


def ok_coder(tokens=1000, env="env-1"):
    return AgentResult(status="completed", environment_id=env, output_text="done", tokens=tokens)


def verdict_result(passed, reasons=(), failing=()):
    args = {"passed": passed, "reasons": list(reasons), "failing_tests": list(failing), "evidence": "Ran 8 tests"}
    return AgentResult(status="completed", environment_id="crit", tokens=500,
                       tool_calls=[{"name": "submit_verdict", "arguments": args, "result": "verdict recorded"}])


class FakeGateway:
    """Scripted stand-in for Gateway: plan, coder attempts, critic verdicts, snapshots."""

    def __init__(self, plan=PLAN3, coders=None, critics=None, snapshot=None, plan_result=None, delay=0.0, reflections=None):
        self.plan = plan
        self.reflections = list(reflections or [])
        self.model_calls = []  # kwargs of every run_model call, in order
        self.plan_result = plan_result
        self.coders = list(coders or [])
        self.critics = list(critics or [])
        self.snapshot = snapshot if snapshot is not None else {"inventory_report.py": "fixed", "SPEC.md": "spec text"}
        self.delay = delay
        self.calls = []  # (role, model, prompt, environment)
        self.snapshots = 0

    async def run_model(self, model, prompt, schema, **kwargs):
        self.calls.append(("planner" if schema is Plan else "reflector", model, prompt, None))
        self.model_calls.append(kwargs)
        if schema is Plan:
            if self.plan_result is not None:
                return self.plan_result
            return ModelResult(ok=True, value=self.plan, interaction_id="plan-1", tokens=800)
        if self.reflections:  # a scripted reflection; otherwise none is available and the orchestrator must fall back
            return self.reflections.pop(0)
        return ModelResult(ok=False, error="no scripted reflection")

    async def run_agent(self, spec, prompt, *, system_instruction, tools=None, environment=None, on_event=None, **kwargs):
        self.calls.append((spec.role, spec.model, prompt, environment))
        if self.delay:
            await asyncio.sleep(self.delay)
        script = self.coders if spec.role == "coder" else self.critics
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if spec.role == "critic" and on_event:
            on_event("tool_called", {"tool": "submit_verdict", "arguments": {}})
            on_event("tool_returned", {"tool": "submit_verdict", "result": "verdict recorded"})
        return item

    async def download_workspace(self, environment_id, **limits):
        self.snapshots += 1
        if isinstance(self.snapshot, BaseException):
            raise self.snapshot
        return dict(self.snapshot)

    def by_role(self, role):
        return [c for c in self.calls if c[0] == role]


async def run(gw, budget=None, history=None, spec=SPEC):
    log = StateLog("run1", persist=False)
    result = await run_task(spec, log, gw, budget, history=history)
    return result, log


def kinds(log):
    return [e.kind for e in log.events]


def events(log, kind):
    return [e for e in log.events if e.kind == kind]


# ---- happy path -------------------------------------------------------------

async def test_happy_path_runs_every_step_and_ends_with_one_run_finished():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)
    result, log = await run(gw)

    assert result.succeeded and (result.steps_total, result.steps_succeeded, result.attempts_used) == (3, 3, 3)
    assert kinds(log)[0] == "run_started" and kinds(log)[-1] == "run_finished" and kinds(log).count("run_finished") == 1
    assert log.closed
    assert [e.step for e in events(log, "step_succeeded")] == [1, 2, 3]
    assert result.tokens == 800 + 3 * 1000 + 1 * 500  # plan + 3 coder calls + 1 critic review (the gate)
    finished = events(log, "run_finished")[0].payload
    assert finished["succeeded"] is True and finished["steps_succeeded"] == 3


async def test_planner_output_is_recorded_and_uses_the_orchestrator_model():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)
    _, log = await run(gw)
    planner_call = gw.by_role("planner")[0]
    assert planner_call[1] == ORCHESTRATOR_MODEL and "inventory_report.py" in planner_call[2]
    result_event = next(e for e in events(log, "agent_result") if e.agent == "orchestrator")
    assert result_event.payload["status"] == "completed" and "diagnose | fix | verify" in result_event.payload["output"]


def coder_workspaces(gw):
    """{path: content} the coder's sandbox was seeded with, for each coder call in order."""
    return [{s["target"].removeprefix("/workspace/"): s["content"] for s in c[3]["sources"]} for c in gw.by_role("coder")]


class SequencedSnapshots(FakeGateway):
    """A gateway whose workspace snapshots differ per coder call (what the coder 'left behind')."""

    def __init__(self, snapshots, **kwargs):
        super().__init__(**kwargs)
        self.snapshot_queue = list(snapshots)

    async def download_workspace(self, environment_id, **limits):
        self.snapshots += 1
        return dict(self.snapshot_queue.pop(0))


async def test_every_coder_attempt_gets_a_fresh_sandbox_seeded_from_the_latest_snapshot():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)  # snapshot: inventory_report.py = "fixed"
    await run(gw)
    envs = [c[3] for c in gw.by_role("coder")]
    assert all(isinstance(e, dict) and e["type"] == "remote" for e in envs)  # never an environment id: no sandbox reuse
    first, second, third = coder_workspaces(gw)
    assert set(first) == {"SPEC.md", "inventory_report.py"} and first["inventory_report.py"] == "broken"
    assert second["inventory_report.py"] == third["inventory_report.py"] == "fixed"  # continues from the coder's own work


async def test_a_retry_after_a_failed_verdict_continues_from_the_coders_last_snapshot():
    gw = SequencedSnapshots(
        [{"inventory_report.py": "attempt-1 code"}, {"inventory_report.py": "attempt-2 code"}, {"inventory_report.py": "s3 code"}],
        coders=[ok_coder()] * 3, critics=[verdict_result(False, ["bad"], ["t"]), verdict_result(True), verdict_result(True)],
        plan=Plan(steps=[step("a", True), step("b", False)]),
    )
    await run(gw)
    seeds = coder_workspaces(gw)
    assert seeds[0]["inventory_report.py"] == "broken"
    assert seeds[1]["inventory_report.py"] == "attempt-1 code"  # the failed attempt's files are the retry's starting point
    assert seeds[2]["inventory_report.py"] == "attempt-2 code"


async def test_a_coder_attempt_that_did_not_finish_leaves_the_workspace_untouched():
    incomplete = AgentResult(status="incomplete", environment_id="env-1", tokens=1000)
    gw = FakeGateway(plan=Plan(steps=[step("a", True), step("b", True)]),
                     coders=[incomplete, ok_coder(), ok_coder()], critics=[verdict_result(True), verdict_result(True)])
    await run(gw)
    seeds = coder_workspaces(gw)
    assert seeds[0] == seeds[1]  # nothing was snapshotted from the unfinished attempt
    assert seeds[1]["inventory_report.py"] == "broken" and seeds[2]["inventory_report.py"] == "fixed"


async def test_an_empty_snapshot_never_wipes_the_workspace():
    gw = SequencedSnapshots([{}, {"inventory_report.py": "later"}], plan=Plan(steps=[step("a", True), step("b", True)]),
                            coders=[ok_coder()] * 2, critics=[verdict_result(True)] * 2)
    await run(gw)
    assert coder_workspaces(gw)[1] == {"SPEC.md": "spec text", "inventory_report.py": "broken"}


# ---- isolation: the hidden suite ---------------------------------------------

async def test_hidden_suite_is_mounted_for_the_critic_only_and_only_on_the_gate_step():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)
    await run(gw)

    critic_targets = [{s["target"] for s in c[3]["sources"]} for c in gw.by_role("critic")]
    assert ["/workspace/test_hidden.py" in t for t in critic_targets] == [True]  # only the gate step is reviewed
    for role, _, prompt, env in gw.calls:
        if role == "coder":
            assert "SECRET_HIDDEN_TEST_BODY" not in prompt
            if isinstance(env, dict):
                assert all("test_hidden" not in s["target"] for s in env["sources"])
    gate_prompt = gw.by_role("critic")[0][2]
    assert "python -m unittest test_hidden -v" in gate_prompt


async def test_a_coder_file_cannot_shadow_the_hidden_suite():
    snapshot = {"inventory_report.py": "fixed", "test_hidden.py": "assert True  # coder tampering"}
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3, snapshot=snapshot)
    await run(gw)
    gate_sources = {s["target"]: s["content"] for s in gw.by_role("critic")[0][3]["sources"]}
    assert gate_sources["/workspace/test_hidden.py"] == HIDDEN_SOURCE  # the coder's file never shadows the suite


async def test_critic_gets_a_fresh_sandbox_seeded_with_the_coders_snapshot():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3, snapshot={"inventory_report.py": "FIXED CODE"})
    await run(gw)
    env = gw.by_role("critic")[0][3]
    assert env["type"] == "remote" and {"type": "inline", "target": "/workspace/inventory_report.py", "content": "FIXED CODE"} in env["sources"]


# ---- recovery: fail -> reroute -> pass ----------------------------------------

async def test_failed_verdict_reroutes_to_a_stronger_model_with_the_critics_findings_and_then_passes():
    reasons, failing = ["Negative quantities are summed"], ["test_negative_quantity_is_rejected"]
    gw = FakeGateway(
        coders=[ok_coder()] * 4,
        critics=[verdict_result(False, reasons, failing), verdict_result(True)],  # only the gate step is reviewed
    )
    result, log = await run(gw)

    assert result.succeeded and result.attempts_used == 4
    coder_calls = gw.by_role("coder")
    assert [c[1] for c in coder_calls] == [CHEAP, CHEAP, MID, CHEAP]  # step 2 climbs one rung; step 3 starts cheap again
    retry_prompt = coder_calls[2][2]
    assert "Negative quantities are summed" in retry_prompt and "test_negative_quantity_is_rejected" in retry_prompt
    assert "SECRET_HIDDEN_TEST_BODY" not in retry_prompt  # names and reasons, never test source

    fail = next(e for e in events(log, "verdict") if not e.payload["passed"])
    assert (fail.step, fail.attempt, fail.agent, fail.model) == (2, 1, "critic", CRITIC_MODEL)
    assert fail.payload["subject_model"] == CHEAP and fail.payload["category"] == "coding_python" and fail.payload["gate"] is True
    reroute = events(log, "reroute")[0]
    assert (reroute.step, reroute.attempt) == (2, 1)
    assert (reroute.payload["from_model"], reroute.payload["to_model"]) == (CHEAP, MID)
    assert "Negative quantities are summed" in reroute.payload["modified_subtask"]
    assert next(e for e in events(log, "step_succeeded") if e.step == 2).payload["attempts"] == 2
    order = [k for k in kinds(log) if k in ("verdict", "reroute", "step_succeeded")]
    assert order.index("reroute") > order.index("verdict")  # verdict -> reroute -> ... -> step_succeeded


async def test_history_can_steer_the_first_pick_to_the_stronger_model():
    history = {"coding_python": {CHEAP: (0.0, 10), STRONG: (1.0, 10)}}
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)
    await run(gw, history=history)
    assert gw.by_role("coder")[0][1] == STRONG


# ---- failure guards ------------------------------------------------------------

async def test_exhausted_attempts_fail_the_step_and_stop_the_run():
    bad = verdict_result(False, ["still wrong"], ["t1"])
    gw = FakeGateway(coders=[ok_coder()] * 6, critics=[bad] * 3)
    result, log = await run(gw)

    assert not result.succeeded and (result.steps_succeeded, result.attempts_used) == (1, 4)
    assert [c[1] for c in gw.by_role("coder")][1:] == [CHEAP, MID, STRONG]  # each retry climbs a rung; the top is the last resort
    failed = events(log, "step_failed")
    assert len(failed) == 1 and failed[0].step == 2 and "exhausted 3 attempts" in failed[0].payload["reason"]
    assert not [e for e in events(log, "step_started") if e.step == 3]
    assert kinds(log)[-1] == "run_finished" and events(log, "run_finished")[0].payload["succeeded"] is False


async def test_coder_infrastructure_failure_escalates_and_never_reaches_the_critic():
    incomplete = AgentResult(status="incomplete", environment_id="env-1", tokens=120_000, error=None)
    gw = FakeGateway(plan=Plan(steps=[step("a", True), step("b", True)]),
                     coders=[incomplete, ok_coder(), ok_coder()], critics=[verdict_result(True), verdict_result(True)])
    result, log = await run(gw)

    assert result.succeeded
    assert [c[1] for c in gw.by_role("coder")][:2] == [CHEAP, MID]
    assert len(gw.by_role("critic")) == 2  # not consulted for the incomplete attempt
    assert "incomplete" in gw.by_role("coder")[1][2]  # the retry prompt says the last attempt did not finish


async def test_snapshot_failure_retries_without_blaming_the_model():
    class FlakySnapshots(FakeGateway):
        async def download_workspace(self, environment_id, **limits):
            self.snapshots += 1
            if self.snapshots == 1:
                raise SnapshotError("HTTP 503")
            return dict(self.snapshot)

    gw = FlakySnapshots(plan=Plan(steps=[step("a", True), step("b", True)]),
                        coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 2)
    result, log = await run(gw)
    assert result.succeeded
    assert [c[1] for c in gw.by_role("coder")][:2] == [CHEAP, CHEAP]  # same model: not its fault
    assert any(e.payload.get("status") == "snapshot_error" for e in events(log, "agent_result"))


async def test_critic_that_never_submits_a_verdict_fails_the_step_after_one_retry():
    silent = AgentResult(status="completed", environment_id="crit", tokens=100)
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[silent, silent])
    result, log = await run(gw)
    assert not result.succeeded and len(gw.by_role("critic")) == 2
    assert "critic produced no verdict" in events(log, "step_failed")[0].payload["reason"]


async def test_unusable_planner_output_falls_back_to_a_recorded_single_step_plan():
    gw = FakeGateway(plan_result=ModelResult(ok=False, error="invalid structured output", tokens=300),
                     coders=[ok_coder()], critics=[verdict_result(True)])
    result, log = await run(gw)
    assert result.succeeded and result.steps_total == 1
    fallback = next(e for e in events(log, "agent_result") if e.agent == "orchestrator")
    assert fallback.payload["status"] == "fallback" and "invalid structured output" in fallback.payload["error"]


async def test_plan_outside_the_step_bounds_also_falls_back():
    gw = FakeGateway(plan=Plan(steps=[step("only one")]), coders=[ok_coder()], critics=[verdict_result(True)])
    result, log = await run(gw)
    assert result.steps_total == 1 and next(e for e in events(log, "agent_result") if e.agent == "orchestrator").payload["status"] == "fallback"


async def test_run_token_ceiling_stops_the_run_with_a_step_failure():
    budget = replace(RunBudget(), max_run_tokens=2000)
    gw = FakeGateway(coders=[ok_coder(tokens=1500)] * 3, critics=[verdict_result(False, ["bad"], ["t"])] * 3)
    result, log = await run(gw, budget=budget)
    assert not result.succeeded and "token budget exhausted" in events(log, "step_failed")[0].payload["reason"]
    assert kinds(log)[-1] == "run_finished"


async def test_wall_clock_ceiling_ends_the_run_cleanly():
    budget = replace(RunBudget(), run_wall_clock_s=0.05)
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3, delay=0.5)
    result, log = await run(gw, budget=budget)
    assert not result.succeeded and "wall-clock" in result.error
    assert events(log, "step_failed") and events(log, "step_failed")[0].payload["reason"] == result.error
    assert kinds(log)[-1] == "run_finished" and log.closed


async def test_an_unexpected_exception_is_contained_and_recorded():
    gw = FakeGateway(coders=[RuntimeError("kaboom")], critics=[])
    result, log = await run(gw)
    assert not result.succeeded and "orchestrator error: RuntimeError: kaboom" in result.error
    assert kinds(log)[-1] == "run_finished" and events(log, "run_finished")[0].payload["error"] == result.error


# ---- event shape the UI depends on ---------------------------------------------

async def test_every_agent_call_is_paired_with_a_result_and_tool_events_carry_step_context():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)] * 3)
    _, log = await run(gw)
    assert len(events(log, "agent_call")) == len(events(log, "agent_result")) == 5  # plan + 3 coder + 1 critic (the gate)
    tool = events(log, "tool_called")[0]
    assert (tool.agent, tool.model) == ("critic", CRITIC_MODEL) and tool.step is not None and tool.payload["tool"] == "submit_verdict"
    started = events(log, "step_started")[1].payload
    assert started["gate"] is True and started["title"] == "fix" and started["modifies_code"] is True
    assert events(log, "run_started")[0].payload["critic_model"] == CRITIC_MODEL


# ---- only steps that change code are reviewed ---------------------------------------

async def test_steps_that_do_not_change_code_are_recorded_as_unreviewed_not_as_passed_verdicts():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(True)])
    result, log = await run(gw)

    assert result.succeeded and len(gw.by_role("critic")) == 1  # only the gate step is reviewed
    assert [e.step for e in events(log, "verdict")] == [2]
    by_step = {e.step: e.payload for e in events(log, "step_succeeded")}
    assert by_step[1]["reviewed"] is False and "not sent to the critic" in by_step[1]["note"]
    assert by_step[3]["reviewed"] is False
    assert "reviewed" not in by_step[2]  # the gate step went through a real verdict


async def test_the_gate_step_is_reviewed_even_when_no_step_is_marked_as_changing_code():
    plan = Plan(steps=[step("look", False), step("check", False)])
    gw = FakeGateway(plan=plan, coders=[ok_coder()] * 2, critics=[verdict_result(True)])
    result, log = await run(gw)

    assert result.succeeded and len(gw.by_role("critic")) == 1  # the last step is the gate by default
    assert "/workspace/test_hidden.py" in {s["target"] for s in gw.by_role("critic")[0][3]["sources"]}
    assert [e.step for e in events(log, "verdict")] == [2]
