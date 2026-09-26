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
| Tool registry, state log, routing, key ring, config (`app/tools.py`, `state_log.py`, `routing.py`, `keys.py`, `config.py`) | done, unit-tested offline |
| `app/interactions.py` (SDK gateway: retries, timeouts, per-role keys + failover, snapshot download) | done, unit-tested with a fake client; exercised live |
| `app/orchestrator.py`, `app/reflection.py`, `app/agents/*` (plan, coder, critic, reroute, model-generated reflection) | done, unit-tested with a scripted gateway; a first live run is in progress |
| `demo/` (broken script + SPEC + hidden suite) | done; the suite is verified against a reference solution and against the broken and naive scripts |
| `app/server.py` | `/health`, `/`, `/runs`, `/runs/{id}`, `/stream/{id}` (replay-only public demo) |
| `docs/UI_DESIGN_SPEC.md` | done; the UI itself is built by teammates (`ui/index.html` is a logic reference only) |
| First real recorded run | **pending**; `runs/sample_fixture.jsonl` is synthetic and marked `fixture: true` |

## Models (measured, `scripts/probe_models.py`)

| Role | Engine | Model |
|---|---|---|
| Orchestrator (plan, reflect) | plain Gemini call, structured JSON | `gemini-3.8-flash` (~9s) |
| Coder | Antigravity agent, own sandbox | ladder `gemini-3.5-flash-lite` -> `gemini-3.5-flash` -> `gemini-3.8-flash`, one rung per failed attempt |
| Critic | Antigravity agent, fresh sandbox | `gemini-3.5-flash` (fixed) |

`gemini-3.8-flash` is ~4x slower than `3.5-flash` inside the agent harness (156s and 162s on two different keys vs 42s),
so it is only reached on a third attempt and is never the critic.

## Interactions API findings (verified live, 2026-09-26)

- **Function-tool schema is flat**: `{"type":"function","name":...,"description":...,"parameters":...}`. Nested was not needed (flat passed first), so `SCHEMA_STYLE = "flat"` stays.
- **Round trip**: a tool-calling interaction returns `status == "requires_action"` with steps of `type == "function_call"` carrying `id`, `name`, and `arguments` as an already-parsed dict (not a JSON string). Reply with `input=[{"type":"function_result","name":...,"call_id": step.id,"result":{...}}]`, `previous_interaction_id=<id>`, and `environment=<environment_id>`; the follow-up returns `completed`.
- **Coder -> critic handoff works by environment id**: a file written in one interaction was read by a second, independent interaction (no `previous_interaction_id`) given the same `environment_id`. Inline `sources` fallback not needed.
- **The gate step is heavy and agent latency varies a lot.** The step that implements every spec item timed out at our
  300s per-call ceiling on three runs in a row (three models each), in a reused sandbox AND in fresh ones, so sandbox reuse
  was *not* the cause (an early diagnosis we retracted). A `gemini-3.5-flash` retry then completed in 165s but used 168k
  tokens. A timed-out call reports 0 tokens only because we cancel it; it was long-running, not hung. Per-call timeout is
  now 420s. Separately, the orchestrator owns the workspace (it snapshots each attempt and seeds the next attempt's fresh
  sandbox from it): a design choice for explicit state and key independence, not a claimed fix for the timeouts.
  (An earlier 208s env-reuse read in the smoke test is unexplained; the timing was probably load.)
- **Agent-harness latency varies widely by model**: `gemini-3.8-flash` took 156-208s where `gemini-3.5-flash` took 42s on the
  same task (two different keys agreed), so the critic is `gemini-3.5-flash` and 3.8 is the last rung of the coder ladder.
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

## Web UI (`web/`)

A Next.js static site that replays the recorded runs (`web/public/runs/*.json`, exported with `scripts/export_run.py`).
It needs no backend and no API key. The replay engine is a pure reducer (`web/lib/runPlayer.ts`, 22 tests against both
real recordings); the landing page's "a real run" story is derived from the events, not typed in.

```
cd web
npm install
npm run dev        # http://localhost:3000
npm test           # vitest
npm run build      # static export to web/out/  (deploy that folder anywhere)
```

Deploy (Vercel): import the repo, set the **Root Directory** to `web`; the framework preset is detected. Nothing else to configure.

## API keys

One key works for everything (`GEMINI_API_KEY`). To give each agent its own key, set `GEMINI_API_KEYS` to a
comma-separated pool (1st = orchestrator, 2nd = coder, 3rd = critic, the rest are spares) or the per-role
`GEMINI_API_KEY_ORCHESTRATOR / _CODER / _CRITIC` variables; see `.env.example` and `app/keys.py`.

- **A role fails over to a spare on a 429**, but only at a fresh interaction. Interaction chains and sandbox
  environments belong to the key's Google project and cannot move to another key, so a call bound to an existing
  environment or chain never rotates, and a swap is sticky for the rest of the run.
- **Rate limits are per project, not per key.** Keys from one project share a quota, so a pool only adds capacity
  when the keys come from different projects. Check the hackathon rules and Google's terms before relying on it.
- Events carry a non-secret label (`k1`, `k2`, ...) for the key that served each call, never the key.
- Limitation: no cooldown tracking; an exhausted key is recycled to the back of the spares and may be retried.

## Security

- API keys come from the environment / `.env` only (`GEMINI_API_KEY`, `GEMINI_API_KEYS`, `GEMINI_API_KEY_*`). `.env` is gitignored.
- Error text is scrubbed of every configured key before it is logged, returned, or recorded on an event.
- Before every push: `git ls-files | grep -iE "\.env$"` must print nothing.
- A leaked key is killed and can disqualify the submission.

See [LINEAGE.md](LINEAGE.md) for what was ported from RoundtableCI and what is new code.
