"""FastAPI app: serves the demo UI and (next step) streams the state log.

Run:  uvicorn app.server:app --reload

Live now:   GET /health, GET /
Next step:  POST /run                 start a run, returns run_id
            GET  /stream/{run_id}     SSE of StateLog events (Last-Event-ID -> since_seq)
            GET  /runs/{run_id}       full recorded run (JSON)
            GET  /replay/{run_id}     re-stream a recorded run -- the default for the
                                      public demo, so visitors can't burn API quota;
                                      "run live" stays behind a rate limit.

SSE + backlog replay follows RoundtableCI's workflow_event_bus /
GET /roundtable/stream/{id} design, minus Redis and JWT (no auth, by plan).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import ROOT, api_key

app = FastAPI(title="roundtable-gem")

UI_INDEX = ROOT / "ui" / "index.html"


@app.get("/health")
def health() -> dict:
    # Only reports whether a key is configured, never the key itself.
    return {"ok": True, "api_key_configured": api_key() is not None}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_INDEX)
