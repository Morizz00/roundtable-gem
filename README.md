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
| `scripts/smoke_test.py` | **run 2026-09-26, 3/3 passed** against the live API (see findings below) |
| `app/interactions.py`, `app/orchestrator.py`, `app/agents/*` | stubs |
| `demo/`, `ui/index.html` | placeholders |

## Interactions API findings (verified live, 2026-09-26)

- **Function-tool schema is flat**: `{"type":"function","name":...,"description":...,"parameters":...}`. Nested was not needed (flat passed first), so `SCHEMA_STYLE = "flat"` stays.
- **Round trip**: a tool-calling interaction returns `status == "requires_action"` with steps of `type == "function_call"` carrying `id`, `name`, and `arguments` as an already-parsed dict (not a JSON string). Reply with `input=[{"type":"function_result","name":...,"call_id": step.id,"result":{...}}]`, `previous_interaction_id=<id>`, and `environment=<environment_id>`; the follow-up returns `completed`.
- **Coder -> critic handoff works by environment id**: a file written in one interaction was read by a second, independent interaction (no `previous_interaction_id`) given the same `environment_id`. Inline `sources` fallback not needed.
- **Token baseline is heavy**: a trivial first call costs ~12k input tokens (harness overhead); a call that reads a file from a reused environment cost ~18.7k total; a `function_result` continuation is cheap (~2.3k). The smoke test's `max_total_tokens=20_000` was nearly hit by a trivial task, so the default per-interaction budget needs headroom (see `RunBudget`).
- Usage is on `interaction.usage` (`total_tokens`, `total_input_tokens`, `total_output_tokens`, `total_thought_tokens`).

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
