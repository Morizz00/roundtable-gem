import asyncio
import json
import logging

import pytest

from app.state_log import StateLog, StepEvent, new_run_id


def test_seq_is_monotonic_and_events_persist_as_jsonl(tmp_path):
    log = StateLog("r1", runs_dir=tmp_path)
    a = log.append("run_started")
    b = log.append("step_started", step=1, agent="orchestrator", payload={"task": "fix it"})
    assert (a.seq, b.seq) == (1, 2)

    lines = (tmp_path / "r1.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]
    assert json.loads(lines[1])["payload"] == {"task": "fix it"}


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "", "x" * 65, "a b", None])
def test_invalid_run_id_is_rejected_before_touching_disk(tmp_path, bad):
    with pytest.raises(ValueError):
        StateLog(bad, runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_new_run_id_is_a_valid_run_id(tmp_path):
    StateLog(new_run_id(), runs_dir=tmp_path)  # must not raise


def test_unknown_kind_is_recorded_with_a_warning(caplog):
    log = StateLog("r1", persist=False)
    with caplog.at_level(logging.WARNING, logger="app.state_log"):
        event = log.append("totally_new_kind")
    assert event.kind == "totally_new_kind"
    assert "unknown event kind" in caplog.text


def test_persist_failure_never_raises(tmp_path, caplog):
    not_a_dir = tmp_path / "afile"
    not_a_dir.write_text("x")
    log = StateLog("r1", runs_dir=not_a_dir)
    with caplog.at_level(logging.WARNING, logger="app.state_log"):
        event = log.append("run_started")
    assert event.seq == 1 and len(log.events) == 1
    assert "could not persist" in caplog.text


def test_backlog_since_seq():
    log = StateLog("r1", persist=False)
    for kind in ("run_started", "step_started", "agent_call"):
        log.append(kind)
    assert [e.seq for e in log.backlog(since_seq=1)] == [2, 3]
    assert log.backlog(since_seq=3) == []


async def test_subscribe_yields_backlog_then_live_and_ends_on_run_finished():
    log = StateLog("r1", persist=False)
    log.append("run_started")
    got = []

    async def consume():
        async for event in log.subscribe():
            got.append(event.kind)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    log.append("step_started", step=1)
    log.append("run_finished")
    await asyncio.wait_for(task, timeout=1)

    assert got == ["run_started", "step_started", "run_finished"]
    assert log.closed


async def test_subscribe_since_seq_skips_already_seen_events():
    log = StateLog("r1", persist=False)
    log.append("run_started")
    log.append("step_started")
    log.append("run_finished")
    kinds = [e.kind async for e in log.subscribe(since_seq=1)]
    assert kinds == ["step_started", "run_finished"]


async def test_subscribe_on_closed_log_replays_backlog_and_stops():
    log = StateLog("r1", persist=False)
    log.append("run_started")
    log.close()
    assert [e.kind async for e in log.subscribe()] == ["run_started"]


async def test_two_subscribers_each_get_every_event_exactly_once():
    log = StateLog("r1", persist=False)
    results = [[], []]

    async def consume(i):
        async for event in log.subscribe():
            results[i].append(event.seq)

    tasks = [asyncio.create_task(consume(i)) for i in range(2)]
    await asyncio.sleep(0)
    for kind in ("run_started", "step_started", "run_finished"):
        log.append(kind)
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
    assert results == [[1, 2, 3], [1, 2, 3]]


async def test_subscriber_is_unregistered_after_the_stream_ends():
    log = StateLog("r1", persist=False)
    log.append("run_started")

    async def drain():
        return [e async for e in log.subscribe()]

    task = asyncio.create_task(drain())
    await asyncio.sleep(0)
    log.append("run_finished")
    await asyncio.wait_for(task, timeout=1)
    assert log._subscribers == []


def test_close_is_idempotent():
    log = StateLog("r1", persist=False)
    log.close()
    log.close()
    assert log.closed


def test_load_roundtrip_is_closed_and_equal(tmp_path):
    log = StateLog("r1", runs_dir=tmp_path)
    log.append("run_started")
    log.append("verdict", step=1, attempt=2, agent="critic", model="m", payload={"passed": False, "category": "c"})

    loaded = StateLog.load(log.path)
    assert loaded.closed
    assert [e.to_dict() for e in loaded.events] == [e.to_dict() for e in log.events]


def test_load_skips_malformed_lines_and_ignores_unknown_fields(tmp_path):
    good = StepEvent(run_id="r1", seq=1, ts=1.0, kind="run_started").to_dict()
    good["future_field"] = "ignored"
    path = tmp_path / "r1.jsonl"
    path.write_text("not json\n" + json.dumps(good) + "\n\n{\"seq\": 2}\n", encoding="utf-8")

    loaded = StateLog.load(path)
    assert [e.seq for e in loaded.events] == [1]
