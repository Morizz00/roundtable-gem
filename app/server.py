"""FastAPI app: serves the demo UI and streams the state log.

Run:  uvicorn app.server:app --reload

    GET /health                  liveness + whether a key is configured (never the key)
    GET /                        the timeline UI (ui/index.html)
    GET /runs                    recorded runs, real ones first, fixtures last
    GET /runs/{run_id}           one run's full event list (JSON)
    GET /stream/{run_id}         SSE of StateLog events, one per message
                                   ?since=N or Last-Event-ID  resume after seq N
                                   ?pace=1&speed=2            replay a FINISHED run at
                                                              human speed (gaps capped)
Next step: POST /run to start a live run (orchestrator registers its StateLog
via state_log.register_log, so /stream serves it while it is still going).

SSE + backlog replay follows RoundtableCI's workflow_event_bus /
GET /roundtable/stream/{id} design, minus Redis and JWT (no auth, by plan).
TODO(live): idle keep-alive comments for proxies, once runs have long gaps.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

import app.config as config
from app.config import ROOT, api_key
from app.state_log import StateLog, get_active_log, is_valid_run_id

app = FastAPI(title="roundtable-gem")

UI_INDEX = ROOT / "ui" / "index.html"

# Replay pacing: real gaps between events can be minutes (LLM calls); clamp them
# so a recorded run plays back watchably, then divide by the requested speed.
_MIN_GAP_S = 0.2
_MAX_GAP_S = 1.5


def _run_path(run_id: str) -> Path:
    return config.RUNS_DIR / f"{run_id}.jsonl"


def _resolve_log(run_id: str) -> StateLog:
    """In-memory log for a run still in flight, else the recorded file. 404 otherwise."""
    if not is_valid_run_id(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    active = get_active_log(run_id)
    if active is not None:
        return active
    path = _run_path(run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="run not found")
    try:
        return StateLog.load(path)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="run not readable")


@app.get("/health")
def health() -> dict:
    # Only reports whether a key is configured, never the key itself.
    return {"ok": True, "api_key_configured": api_key() is not None}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_INDEX)


@app.get("/runs")
def list_runs() -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for path in config.RUNS_DIR.glob("*.jsonl"):
        if not is_valid_run_id(path.stem):
            continue
        try:
            log = StateLog.load(path)
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        events = log.events
        started = next((e for e in events if e.kind == "run_started"), None)
        finished = next((e for e in reversed(events) if e.kind == "run_finished"), None)
        runs.append(
            {
                "run_id": path.stem,
                "events": len(events),
                "task": (started.payload.get("task") if started else None),
                "fixture": bool(started and started.payload.get("fixture")),
                "finished": finished is not None,
                "succeeded": (bool(finished.payload.get("succeeded")) if finished else None),
                "mtime": mtime,
            }
        )
    runs.sort(key=lambda r: (r["fixture"], -r["mtime"]))  # real runs first, newest first
    return runs


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    log = _resolve_log(run_id)
    return {"run_id": run_id, "events": [e.to_dict() for e in log.events]}


@app.get("/stream/{run_id}")
async def stream(
    run_id: str,
    since: int = Query(0, ge=0),
    pace: bool = False,
    speed: float = Query(1.0, gt=0, le=20),
    last_event_id: Optional[str] = Header(None),
) -> StreamingResponse:
    log = _resolve_log(run_id)
    since_seq = since
    if last_event_id and last_event_id.isdigit():
        since_seq = max(since_seq, int(last_event_id))
    replaying = pace and log.closed  # never slow down a run that is live

    async def events() -> AsyncIterator[str]:
        prev_ts: Optional[float] = None
        async for event in log.subscribe(since_seq):
            if replaying:
                if prev_ts is not None:
                    await asyncio.sleep(min(max(event.ts - prev_ts, _MIN_GAP_S), _MAX_GAP_S) / speed)
                prev_ts = event.ts
            yield f"id: {event.seq}\ndata: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
