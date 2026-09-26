import json

from app.config import MODEL_LADDER
from app.ranking_math import bayesian_smooth
from app.routing import AgentSpec, history_from_runs, pick

CHEAP, STRONG = MODEL_LADDER[0], MODEL_LADDER[-1]


# ---- ranking math (verbatim lift from RoundtableCI shared/ranking_math.py) -

def test_zero_races_scores_exactly_the_prior():
    assert bayesian_smooth(0.9, 0) == 0.5
    assert bayesian_smooth(0.0, 0) == 0.5


def test_many_races_converge_to_raw_rate():
    assert abs(bayesian_smooth(1.0, 100_000) - 1.0) < 0.001


def test_formula_matches_source_constants():
    # (n*R + C*m) / (n + C) with C=15, m=0.5
    assert bayesian_smooth(1.0, 15) == (15 * 1.0 + 15 * 0.5) / 30


# ---- pick: no history = escalation ladder ---------------------------------

def test_first_pick_is_the_cheapest_model():
    assert pick("coding_python") == AgentSpec(role="coder", model=CHEAP)


def test_reroute_escalates_after_a_failure():
    assert pick("coding_python", failed_models=[CHEAP]).model == STRONG


def test_all_candidates_failed_falls_back_to_strongest_instead_of_dying():
    assert pick("coding_python", failed_models=list(MODEL_LADDER)).model == STRONG


def test_unknown_category_uses_the_default_ladder():
    assert pick("some_new_category").model == CHEAP


def test_role_is_passed_through():
    assert pick("general", role="critic").role == "critic"


# ---- pick: with history = smoothed ranking --------------------------------

def test_history_prefers_the_smoothed_winner():
    history = {"coding_python": {STRONG: (1.0, 30), CHEAP: (0.2, 30)}}
    assert pick("coding_python", history=history).model == STRONG


def test_smoothing_stops_a_tiny_lucky_sample_from_winning():
    # cheap: 1 race, 1 win (raw 1.0) vs strong: 40 races, 80% -- the prior pulls the
    # 1-race model back toward 0.5 (0.531), below strong's 0.718.
    history = {"coding_python": {CHEAP: (1.0, 1), STRONG: (0.8, 40)}}
    assert pick("coding_python", history=history).model == STRONG


def test_history_ties_break_by_ascending_model_name():
    history = {"coding_python": {CHEAP: (0.5, 10), STRONG: (0.5, 10)}}
    assert pick("coding_python", history=history).model == min(CHEAP, STRONG)


def test_history_still_excludes_failed_models():
    history = {"coding_python": {STRONG: (1.0, 30), CHEAP: (0.2, 30)}}
    assert pick("coding_python", failed_models=[STRONG], history=history).model == CHEAP


def test_history_for_another_category_is_ignored():
    history = {"reasoning": {STRONG: (1.0, 30), CHEAP: (0.0, 30)}}
    assert pick("coding_python", history=history).model == CHEAP


# ---- history_from_runs -----------------------------------------------------

def _verdict(model, category, passed):
    return json.dumps({"kind": "verdict", "model": model, "payload": {"category": category, "passed": passed}})


def test_history_from_runs_tallies_verdicts(tmp_path):
    (tmp_path / "a.jsonl").write_text(
        "\n".join(
            [
                _verdict(CHEAP, "coding_python", False),
                _verdict(CHEAP, "coding_python", False),
                _verdict(STRONG, "coding_python", True),
                "garbage line",
                json.dumps({"kind": "step_started", "model": CHEAP, "payload": {}}),  # not a verdict
                json.dumps({"kind": "verdict", "model": CHEAP, "payload": {"passed": True}}),  # no category
                json.dumps({"kind": "verdict", "payload": {"category": "x", "passed": True}}),  # no model
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "b.jsonl").write_text(_verdict(CHEAP, "coding_python", True) + "\n", encoding="utf-8")

    history = history_from_runs(tmp_path)
    assert history == {"coding_python": {CHEAP: (1 / 3, 3), STRONG: (1.0, 1)}}


def test_history_from_runs_on_empty_dir_is_empty(tmp_path):
    assert history_from_runs(tmp_path) == {}
