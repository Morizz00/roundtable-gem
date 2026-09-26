# Lineage: RoundtableCI -> roundtable-gem

Where each piece came from, so the writeup's "RoundtableCI lineage" section is
accurate. RoundtableCI (private repo) is the reference codebase; nothing was
forked and no git history, config, or `.env` was carried over.

## Ported (code lifted or adapted)

| roundtable-gem | RoundtableCI source | What changed |
|---|---|---|
| `app/tools.py` | `roundtable-service/app/services/tool_registry.py` | Tool registration + never-raises dispatch + `resolve_safe_path` kept. File-editing trust model (`write_allowlist`, `proposed_edits`, sqlite) dropped. Wire schema targets the Interactions API (`SCHEMA_STYLE` flat/nested). |
| `app/ranking_math.py` | `shared/ranking_math.py` | Verbatim (`bayesian_smooth`, C=15, m=0.5), docstring trimmed. |
| `app/routing.py` | `sage-service/app/services/query_routing_service.py` (policy shape) | Category -> ranked candidates, Bayesian-smoothed ranking, ascending-name tie-break, fallback. Ranking *data* is not ported: candidates are Gemini models only, "races" are critic verdicts in `runs/*.jsonl`. |
| `app/state_log.py` | `engineer_runtime.py` (`MissionEvent`/`MissionResult`), `workflow_event_bus.py` (in-process fallback: backlog + subscribers + seq), `reflection_observation_layer.py` (`emit_observation` event shape) | No Redis; single process. Adds JSONL persistence + replay. |
| `app/config.py` | `engineer_runtime.py` (`MissionRuntimeConfig`) | Budget *shape* only; mapped onto Interactions API limits. |

## Ideas carried over (no code)

- **Judge role** (`app/agents/critic.py`): classic Roundtable's race -> judge -> improve, with one racer.
- **Bounded recovery** (`app/orchestrator.py`): detect -> bounded fix -> re-verify, never raise -- the shape of `runtime_healing_service.py`.
- **Hard-ceiling guard**: `asyncio.wait_for` around the whole run, as in `engineer_runtime.run_engineer`.
- **SSE with backlog replay** (`app/server.py`): the design of `GET /roundtable/stream/{id}`.

## New code (not ported -- say so in the writeup)

- **The in-run retry / reroute loop.** It is *not* "the Reflection System, ported". In RoundtableCI, `engineering_reflect_hook` is observation-only (emits a fact, returns the result unchanged) and the Reflection Store is an asynchronous validated-knowledge pipeline. What carries over is the observation idea: one recorded fact per attempt, which is what the state log holds.
- Everything that talks to the Interactions API (`app/interactions.py`), the demo task, and the UI.

## Deliberately not carried over

Auth, billing, Postgres, Redis, `resource_manager`, OpenRouter/AIML providers, the Next.js frontend, Rust/Go/NLP services, and every `.env`. The Antigravity agent runs its own loop and sandbox server-side, which makes the provider/key-health/loop machinery redundant.
