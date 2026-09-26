import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.config as config
import app.state_log as state_log
from app.server import _resolve_log, app
from app.state_log import StateLog, register_log


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(state_log, "ACTIVE_LOGS", {})
    return TestClient(app)


def write_run(tmp_path, run_id, *, fixture=False, succeeded=True, finish=True):
    log = StateLog(run_id, runs_dir=tmp_path)
    log.append("run_started", agent="orchestrator", payload={"task": f"task of {run_id}", "fixture": fixture})
    log.append("step_started", step=1, agent="orchestrator", payload={"title": "s1"})
    log.append("verdict", step=1, agent="critic", model="m", payload={"passed": True, "category": "c"})
    if finish:
        log.append("run_finished", agent="orchestrator", payload={"succeeded": succeeded})
    return log


def parse_sse(text):
    """-> list of (id, event dict) from an SSE body."""
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            out.append((int(fields["id"]), json.loads(fields["data"])))
    return out


# ---- basics ---------------------------------------------------------------

def test_health_reports_key_presence_but_never_the_key(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-secret-value")
    response = client.get("/health")
    assert response.json() == {"ok": True, "api_key_configured": True}
    assert "sk-test-secret-value" not in response.text


def test_index_serves_the_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Roundtable" in response.text


# ---- /runs ----------------------------------------------------------------

def test_runs_lists_real_runs_before_fixtures_newest_first(client, tmp_path):
    write_run(tmp_path, "sample_fx", fixture=True)
    write_run(tmp_path, "old_real")
    time.sleep(0.02)
    write_run(tmp_path, "new_real", succeeded=False)
    write_run(tmp_path, "in_flight", finish=False)

    runs = client.get("/runs").json()
    assert [r["run_id"] for r in runs][-1] == "sample_fx"
    assert runs[0]["run_id"] == "in_flight"  # newest real run
    by_id = {r["run_id"]: r for r in runs}
    assert by_id["sample_fx"]["fixture"] is True
    assert by_id["new_real"]["succeeded"] is False and by_id["new_real"]["finished"] is True
    assert by_id["old_real"]["succeeded"] is True
    assert by_id["in_flight"]["finished"] is False and by_id["in_flight"]["succeeded"] is None
    assert by_id["old_real"]["task"] == "task of old_real" and by_id["old_real"]["events"] == 4


def test_runs_skips_files_with_invalid_run_ids(client, tmp_path):
    (tmp_path / "bad name.jsonl").write_text("{}\n")
    write_run(tmp_path, "good")
    assert [r["run_id"] for r in client.get("/runs").json()] == ["good"]


def test_runs_empty_when_nothing_recorded(client):
    assert client.get("/runs").json() == []


def test_run_detail_returns_events(client, tmp_path):
    write_run(tmp_path, "r1")
    body = client.get("/runs/r1").json()
    assert body["run_id"] == "r1"
    assert [e["kind"] for e in body["events"]] == ["run_started", "step_started", "verdict", "run_finished"]


@pytest.mark.parametrize("bad", ["nope", "bad.id", "a%20b"])
def test_run_detail_404_for_unknown_or_invalid_ids(client, bad):
    assert client.get(f"/runs/{bad}").status_code == 404


@pytest.mark.parametrize("bad", ["..", "../secret", "a/b", "a\\b", "", "x" * 65])
def test_resolver_rejects_path_traversal_ids(tmp_path, monkeypatch, bad):
    # The HTTP client normalizes "/runs/.." to "/" before the request is sent, so
    # traversal ids are exercised against the resolver directly.
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    (tmp_path.parent / "secret.jsonl").write_text('{"run_id":"secret","seq":1,"ts":1,"kind":"run_started"}\n')
    with pytest.raises(HTTPException) as exc:
        _resolve_log(bad)
    assert exc.value.status_code == 404


# ---- /stream --------------------------------------------------------------

def test_stream_replays_a_finished_run_as_sse(client, tmp_path):
    write_run(tmp_path, "r1")
    response = client.get("/stream/r1")
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [i for i, _ in events] == [1, 2, 3, 4]
    assert events[-1][1]["kind"] == "run_finished"


def test_stream_resumes_after_since_and_last_event_id(client, tmp_path):
    write_run(tmp_path, "r1")
    assert [i for i, _ in parse_sse(client.get("/stream/r1?since=2").text)] == [3, 4]
    assert [i for i, _ in parse_sse(client.get("/stream/r1", headers={"Last-Event-ID": "3"}).text)] == [4]
    # the larger of the two wins
    assert [i for i, _ in parse_sse(client.get("/stream/r1?since=3", headers={"Last-Event-ID": "1"}).text)] == [4]


def test_stream_pace_replays_the_same_events(client, tmp_path):
    write_run(tmp_path, "r1")
    started = time.monotonic()
    events = parse_sse(client.get("/stream/r1?pace=1&speed=20").text)
    assert [i for i, _ in events] == [1, 2, 3, 4]
    assert time.monotonic() - started < 2  # min gap 0.2s / 20x, not real time


def test_stream_serves_a_registered_in_memory_run_without_a_file(client):
    log = StateLog("mem1", persist=False)
    log.append("run_started")
    log.append("run_finished")
    register_log(log)
    assert [i for i, _ in parse_sse(client.get("/stream/mem1").text)] == [1, 2]


def test_stream_prefers_the_active_log_over_a_stale_file(client, tmp_path):
    write_run(tmp_path, "r1")  # 4 events on disk
    live = StateLog("r1", persist=False)
    live.append("run_started")
    live.append("run_finished")
    register_log(live)
    assert [i for i, _ in parse_sse(client.get("/stream/r1").text)] == [1, 2]


@pytest.mark.parametrize("bad", ["nope", "bad.id"])
def test_stream_404_for_unknown_or_invalid_ids(client, bad):
    assert client.get(f"/stream/{bad}").status_code == 404


@pytest.mark.parametrize("query", ["speed=0", "speed=21", "since=-1"])
def test_stream_rejects_bad_query_params(client, tmp_path, query):
    write_run(tmp_path, "r1")
    assert client.get(f"/stream/r1?{query}").status_code == 422
