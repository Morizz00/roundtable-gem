import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents import coder, critic
from app.demo import DEMO_DIR, load_demo_task
from app.interactions import AgentResult
from app.schemas import VERDICT_PARAMETERS, Plan, PlanStep, Verdict, validate_plan
from app.tools import ToolContext

STEP = PlanStep(title="Fix parsing", instruction="Make the parser skip bad rows", acceptance="No crash on the sample", modifies_code=True)
FIXTURES = Path(__file__).parent / "fixtures"
CTX = ToolContext(workspace_root=Path("."))


def plan_of(n, **overrides):
    return Plan(steps=[PlanStep(**{**STEP.model_dump(), **overrides}) for _ in range(n)])


# ---- schemas -----------------------------------------------------------------

@pytest.mark.parametrize("n", [2, 3, 5])
def test_plan_within_bounds_is_valid(n):
    assert validate_plan(plan_of(n)) is None


@pytest.mark.parametrize("n", [0, 1, 6])
def test_plan_outside_bounds_is_rejected(n):
    assert "expected 2-5" in validate_plan(plan_of(n))


@pytest.mark.parametrize("field", ["title", "instruction", "acceptance"])
def test_plan_with_a_blank_field_is_rejected(field):
    assert "empty" in validate_plan(plan_of(2, **{field: "   "}))


def test_plan_schema_has_no_length_or_count_keywords_the_api_might_not_honour():
    text = json.dumps(Plan.model_json_schema())
    assert not any(k in text for k in ("minLength", "maxLength", "minItems", "maxItems"))


def test_verdict_tool_schema_is_flat_and_requires_the_essentials():
    assert "$ref" not in json.dumps(VERDICT_PARAMETERS)
    assert VERDICT_PARAMETERS["type"] == "object" and set(VERDICT_PARAMETERS["required"]) == {"passed", "reasons", "failing_tests"}


# ---- coder prompt ---------------------------------------------------------------

def test_coder_first_attempt_prompt_has_task_step_and_acceptance_but_no_feedback():
    prompt = coder.build_prompt("fix the report", STEP, 2, 4)
    assert "fix the report" in prompt and "step (2 of 4)" in prompt.lower().replace("current step", "step")
    assert STEP.instruction in prompt and STEP.acceptance in prompt and "failed independent review" not in prompt


def test_coder_retry_prompt_carries_the_critics_findings():
    verdict = Verdict(passed=False, reasons=["Negatives are summed"], failing_tests=["test_neg", "test_skip"])
    prompt = coder.build_prompt("t", STEP, 1, 2, feedback=verdict)
    assert "failed independent review" in prompt and "- Negatives are summed" in prompt
    assert "Failing checks: test_neg, test_skip" in prompt


def test_coder_retry_after_an_unclean_finish_says_so():
    prompt = coder.build_prompt("t", STEP, 1, 2, failure_note="incomplete: token budget")
    assert "did not finish cleanly (incomplete: token budget)" in prompt and "failed independent review" not in prompt


# ---- critic prompt / tool / verdict ---------------------------------------------

def test_gate_prompt_names_the_suite_command_and_non_gate_prompt_does_not():
    gate = critic.build_prompt("t", STEP, 2, 3, gate=True, suite="test_hidden.py")
    plain = critic.build_prompt("t", STEP, 1, 3, gate=False, suite="test_hidden.py")
    assert "python -m unittest test_hidden -v" in gate and "submit_verdict" in gate
    assert "unittest" not in plain and "submit_verdict" in plain and "judge only this step" in plain


async def test_submit_verdict_tool_accepts_valid_and_rejects_invalid_arguments():
    registry = critic.build_tools()
    assert registry.names() == ["submit_verdict"]
    assert await registry.call("submit_verdict", {"passed": True, "reasons": [], "failing_tests": []}, CTX) == critic.VERDICT_OK
    assert (await registry.call("submit_verdict", {"reasons": []}, CTX)).startswith("error: invalid verdict")
    assert registry.interactions_tools_schema()[0]["name"] == "submit_verdict"


def call(args, result=critic.VERDICT_OK):
    return {"name": "submit_verdict", "arguments": args, "result": result}


def test_extract_verdict_takes_the_last_valid_recorded_call():
    result = AgentResult(status="completed", tool_calls=[
        call({"passed": False, "reasons": ["a"], "failing_tests": ["t"]}),
        call({"passed": True, "reasons": [], "failing_tests": []}),
    ])
    assert critic.extract_verdict(result).passed is True


def test_extract_verdict_ignores_rejected_and_malformed_calls():
    result = AgentResult(status="completed", tool_calls=[
        call({"passed": False, "reasons": ["real"], "failing_tests": ["t"]}),
        call({"reasons": ["no passed field"]}, result="error: invalid verdict (...)"),
        call({"passed": "maybe-not-a-bool", "reasons": []}),
        {"name": "other_tool", "arguments": {}, "result": "x"},
    ])
    verdict = critic.extract_verdict(result)
    assert verdict.passed is False and verdict.reasons == ["real"]


def test_extract_verdict_is_none_when_the_critic_never_submitted():
    assert critic.extract_verdict(AgentResult(status="completed")) is None
    assert critic.extract_verdict(AgentResult(status="timeout", error="slow")) is None


# ---- demo assets ------------------------------------------------------------------

def test_demo_task_separates_what_the_coder_sees_from_what_only_the_critic_sees():
    spec = load_demo_task()
    assert set(spec.workspace) == {"inventory_report.py", "SPEC.md", "sample_stock.csv"}
    assert set(spec.hidden_tests) == {"test_hidden.py"} and spec.suite == "test_hidden.py"
    assert not set(spec.workspace) & set(spec.hidden_tests)
    assert "sample_stock.csv" in spec.task and spec.category == "coding_python"


def run_hidden_suite(tmp_path, script: Path):
    shutil.copy(script, tmp_path / "inventory_report.py")
    shutil.copy(DEMO_DIR / "hidden_tests" / "test_hidden.py", tmp_path / "test_hidden.py")
    return subprocess.run([sys.executable, "-m", "unittest", "test_hidden"], cwd=tmp_path, capture_output=True, text=True, timeout=120)


def test_hidden_suite_passes_a_reference_solution(tmp_path):
    proc = run_hidden_suite(tmp_path, FIXTURES / "reference_inventory_report.py")
    assert proc.returncode == 0, proc.stderr[-800:]


@pytest.mark.parametrize("script", [DEMO_DIR / "broken_script" / "inventory_report.py", FIXTURES / "naive_inventory_report.py"])
def test_hidden_suite_fails_the_broken_script_and_the_naive_crash_fix(tmp_path, script):
    proc = run_hidden_suite(tmp_path, script)
    assert proc.returncode != 0 and "FAILED" in proc.stderr  # the suite has teeth: this is what makes a first-attempt failure real


def test_the_broken_script_really_crashes_on_the_sample(tmp_path):
    proc = subprocess.run([sys.executable, str(DEMO_DIR / "broken_script" / "inventory_report.py"),
                           str(DEMO_DIR / "broken_script" / "sample_stock.csv")], capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0 and "ValueError" in proc.stderr
