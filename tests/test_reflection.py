"""Model-generated reflection: the orchestrator reasons about a failed review and rewrites the sub-task."""
from dataclasses import replace

from test_orchestrator import (  # shared scripted fakes; pytest puts tests/ on sys.path
    CHEAP, MID, FakeGateway, ok_coder, run, events, kinds, verdict_result,
)

from app.config import MODEL_LADDER, ORCHESTRATOR_MODEL, RunBudget
from app.interactions import ModelResult
from app.schemas import Decision

REWRITE = "Re-read SPEC.md items 3 to 6 and handle each explicitly"


def decision(escalate=True, summary="Only the happy path was handled", subtask=REWRITE):
    return Decision(summary=summary, modified_subtask=subtask, escalate=escalate)


def reflection_ok(value, id="refl-1", tokens=700):
    return ModelResult(ok=True, value=value, interaction_id=id, tokens=tokens)


REASONS, FAILING = ["Negative quantities are summed"], ["test_negative_quantity_is_rejected"]


def fail_once_gateway(**kwargs):
    """3-step plan; step 2 (the gate) fails once, then passes."""
    return FakeGateway(
        coders=[ok_coder()] * 4,
        critics=[verdict_result(False, REASONS, FAILING), verdict_result(True)],  # only the gate step (2) is reviewed
        **kwargs,
    )


async def test_reflection_rewrites_the_subtask_and_drives_the_reroute():
    gw = fail_once_gateway(reflections=[reflection_ok(decision())])
    result, log = await run(gw)

    assert result.succeeded
    reflection = events(log, "reflection")[0]
    assert (reflection.step, reflection.attempt, reflection.agent, reflection.model) == (2, 1, "orchestrator", ORCHESTRATOR_MODEL)
    assert reflection.payload["status"] == "completed" and reflection.payload["escalate"] is True
    assert reflection.payload["summary"] == "Only the happy path was handled" and reflection.payload["tokens"] == 700

    order = [k for k in kinds(log) if k in ("verdict", "reflection", "reroute")]
    assert order[:3] == ["verdict", "reflection", "reroute"]  # the failing verdict, then the reflection, then the reroute
    reroute = events(log, "reroute")[0]
    assert reroute.payload["modified_subtask"] == REWRITE
    assert reroute.payload["escalated"] is True and reroute.payload["to_model"] == MID

    retry_prompt = gw.by_role("coder")[2][2]
    assert "Revised instruction from the orchestrator" in retry_prompt and REWRITE in retry_prompt
    assert FAILING[0] in retry_prompt  # the critic's findings still ride along
    assert result.tokens == 800 + 4 * 1000 + 2 * 500 + 700  # plan + 4 coder + 2 critic + the reflection call


async def test_reflection_prompt_carries_the_findings_but_never_the_test_source():
    gw = fail_once_gateway(reflections=[reflection_ok(decision())])
    await run(gw)
    prompt = gw.by_role("reflector")[0][2]
    assert REASONS[0] in prompt and FAILING[0] in prompt and CHEAP in prompt
    assert all(name in prompt for name in MODEL_LADDER)  # the model knows which rungs exist
    assert "SECRET_HIDDEN_TEST_BODY" not in prompt


async def test_reflections_chain_onto_the_plan_and_onto_each_other():
    bad = verdict_result(False, ["still wrong"], ["t1"])
    gw = FakeGateway(
        coders=[ok_coder()] * 5, critics=[bad, bad, verdict_result(True)],
        reflections=[reflection_ok(decision(), id="r1"), reflection_ok(decision(), id="r2")],
    )
    await run(gw)
    # plan (fresh) -> reflection 1 (onto the plan) -> reflection 2 (onto reflection 1)
    assert [k.get("previous_interaction_id") for k in gw.model_calls] == [None, "plan-1", "r1"]


async def test_the_models_escalate_flag_is_honoured_once_then_the_orchestrator_forces_escalation():
    bad = verdict_result(False, ["still wrong"], ["t1"])
    gw = FakeGateway(
        coders=[ok_coder()] * 5, critics=[bad, bad, verdict_result(True)],
        reflections=[reflection_ok(decision(escalate=False), id="r1"), reflection_ok(decision(escalate=False), id="r2")],
    )
    result, log = await run(gw)

    assert result.succeeded
    assert [c[1] for c in gw.by_role("coder")] == [CHEAP, CHEAP, CHEAP, MID, CHEAP]  # same-model retry once, then forced up
    first, second = events(log, "reroute")
    assert first.payload["escalated"] is False and second.payload["escalated"] is True


async def test_a_failed_reflection_falls_back_to_the_deterministic_retry_and_says_so():
    gw = fail_once_gateway(reflections=[ModelResult(ok=False, error="boom", tokens=50)])
    result, log = await run(gw)

    assert result.succeeded
    reflection = events(log, "reflection")[0]
    assert reflection.payload["status"] == "fallback" and reflection.payload["error"] == "boom"
    assert REASONS[0] in reflection.payload["summary"]
    retry_prompt = gw.by_role("coder")[2][2]
    assert REASONS[0] in retry_prompt and "Revised instruction" not in retry_prompt
    assert events(log, "reroute")[0].payload["to_model"] == MID  # recovery never depends on the reflection succeeding


async def test_an_empty_reflection_is_treated_as_unusable():
    gw = fail_once_gateway(reflections=[reflection_ok(decision(summary="  "))])
    _, log = await run(gw)
    payload = events(log, "reflection")[0].payload
    assert payload["status"] == "fallback" and payload["error"] == "empty reflection"


async def test_no_reflection_is_attempted_when_no_retry_remains():
    gw = FakeGateway(coders=[ok_coder()] * 3, critics=[verdict_result(False, ["bad"], ["t"])])
    result, log = await run(gw, budget=replace(RunBudget(), max_attempts=1))
    assert not result.succeeded and gw.by_role("reflector") == [] and not events(log, "reflection")
    assert "exhausted 1 attempts" in events(log, "step_failed")[0].payload["reason"]


async def test_reflection_starts_a_fresh_chain_when_the_plan_was_a_fallback():
    gw = FakeGateway(
        plan_result=ModelResult(ok=False, error="invalid structured output", tokens=10),
        coders=[ok_coder()] * 2, critics=[verdict_result(False, ["bad"], ["t"]), verdict_result(True)],
        reflections=[reflection_ok(decision())],
    )
    result, _ = await run(gw)
    assert result.succeeded and [k.get("previous_interaction_id") for k in gw.model_calls] == [None, None]
