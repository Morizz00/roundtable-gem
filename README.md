# roundtable-gem

PS4 entry, Google DeepMind Hyderabad Hackathon (GDG Hyderabad x Kaggle):
**autonomous orchestration with managed agents**, built on the Antigravity agent
(`antigravity-preview-09-2026`) via the Interactions API.

A task fails halfway, the system catches it, reroutes, and succeeds -- visibly.
Demo task: debug and fix a broken script.

## Architecture

```
            task
              |
        +-----v------+     routing.pick(category, failed_models)
        | orchestrator|---> cheapest model first, stronger after a failure
        +-----+------+
              | delegate            (client-side: the API has no subagent delegation)
      +-------+--------+
      v                v
   coder agent     critic agent      two Antigravity interactions, each in its own
  (fix in sandbox) (hidden tests,    Google-hosted Linux sandbox
                    submit_verdict)
      |                |
      +-------+--------+
              v
        verdict: pass -> next step
                 fail -> reflection -> reroute (new model / modified sub-task) -> retry
              |
              v
          state log  --->  SSE stream / JSONL replay  --->  timeline UI
```

Two specialists only (coder + critic). No auth.

## Status

| Piece | State |
|---|---|
| `app/tools.py`, `app/state_log.py`, `app/routing.py`, `app/ranking_math.py`, `app/config.py` | done, unit-tested offline |
| `app/server.py` | `/health` and `/` only |
| `scripts/smoke_test.py` | written, **not yet run** (needs `GEMINI_API_KEY`) |
| `app/interactions.py`, `app/orchestrator.py`, `app/agents/*` | stubs |
| `demo/`, `ui/index.html` | placeholders |

## Setup

Needs Python >= 3.10 (`google-genai` 2.x). On the dev box `python` is 3.9, so use the launcher:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then set GEMINI_API_KEY -- never commit .env
```

## Run

```powershell
.venv\Scripts\python -m pytest                     # offline unit tests, no key needed
.venv\Scripts\python scripts\smoke_test.py         # first thing once the key is set
.venv\Scripts\python -m uvicorn app.server:app --reload
```

## Security

- `GEMINI_API_KEY` comes from the environment / `.env` only. `.env` is gitignored.
- Before every push: `git ls-files | grep -iE "\.env$"` must print nothing.
- A leaked key is killed and can disqualify the submission.

See [LINEAGE.md](LINEAGE.md) for what was ported from RoundtableCI and what is new code.
