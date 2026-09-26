# Roundtable Gem: agents that fail forward

**Track:** PS4, Autonomous Orchestration with Managed Agents. **Stack:** Antigravity agent (`antigravity-preview-09-2026`) through the Gemini Interactions API.
**Repo:** <<FILL: public repo URL>> **Demo:** <<FILL: live demo URL>>, a replay of a real recorded run (no login, no API quota spent by visitors).

## 1. What we built

A small team of agents that fixes a deliberately broken program, and a state log that shows exactly how they did it, including the part where the first attempt fails.

| Role | Engine | Model |
|---|---|---|
| **Orchestrator** | plain Gemini call on the Interactions API, structured JSON, chained with `previous_interaction_id` | `gemini-3.8-flash` |
| **Coder** | Antigravity agent in its own Linux sandbox | ladder: `gemini-3.5-flash-lite` → `gemini-3.5-flash` → `gemini-3.8-flash` |
| **Critic** | a *separate* Antigravity agent in a *fresh* sandbox | `gemini-3.5-flash` |

**The loop.** The orchestrator turns the task into 2–5 steps (schema-validated; an unusable plan falls back to a recorded single-step plan). For each step the coder attempts it. The workspace it leaves behind is snapshotted out of its sandbox and copied into the critic's own fresh sandbox. On the final code-changing step, the critic also receives a hidden test suite **that the coder never saw**, runs it, and reports through a `submit_verdict` function tool, so the verdict is structured data, not parsed prose. If the verdict is a failure, the orchestrator reasons about why (another structured call, chained onto the plan so the plan and earlier failures live server-side), rewrites the sub-task, and the step is retried one rung up the model ladder. Every input, output and verdict is an event on a state log that a UI replays.

**The demo task** is a Python script that crashes on its sample CSV and has seven subtler requirements in a spec file. Fixing the crash is not enough; the hidden suite checks the rest. We verified the suite is satisfiable (a reference solution passes 8/8) and has teeth (the original script and a "just stop the crash" fix both fail 7/8), so a first-attempt failure is real, never scripted.

## 2. Lineage: RoundtableCI

Roundtable Gem is built from RoundtableCI, our product that routes each query to the best model using real race outcomes and uses a judge model to pick winners. We ported four pieces: the tool registry (never-raises dispatch), the event log (backlog and live subscribe), the routing policy (category → ranked candidates, Bayesian-smoothed ranking, deterministic tie-break), and the idea of a key pool (a dedicated key per role with failover). The judge role became the critic.

**What is new, and what we are not claiming.** The in-run retry loop and the model-written reflection are new code. RoundtableCI's *Reflections* architecture (observations → compiler → validation → store → genome → retrieval, "the orchestrator learns, the model does not") is a larger, asynchronous, validated-knowledge pipeline. It is our roadmap, not something this submission implements. What carries over is its principle: a failure teaches the orchestrator, not the model.

## 3. What broke while building it, and how the system recovered

This is the part we would want a reader to check, because each item was found by running the system, not by reasoning about it.

1. **The strong model was the slow one.** The first live run's critic on `gemini-3.8-flash` timed out at 300s on a trivial review. Instead of guessing, we probed all five supported models on the same critic task, one API key per call. `3.8-flash` took 156s and 162s on two different keys (so the model is slow, not a throttled key) against 42s for `3.5-flash`, which judged correctly. The critic moved to `3.5-flash`, and `3.8` became the last rung of the coder ladder.
2. **The heaviest step outran our timeout, and our first diagnosis was wrong.** The step that implements every spec item hit the 300s per-call ceiling on three runs in a row, on three different models. We first blamed reusing a sandbox by environment id; the same step then timed out in fresh sandboxes too, so we retracted that. A later retry on `gemini-3.5-flash` completed in 165s but used 168k tokens: the call was long-running, not hung (a cancelled call reports zero tokens). The per-call timeout is now 420s. Separately, the orchestrator owns the workspace: it snapshots every coder attempt and seeds the next attempt's fresh sandbox from it. That is a design choice for explicit state and for not tying the coder to one API key, not a claimed fix.
3. **A snapshot download timed out and cost a whole attempt.** Transient download failures (timeouts, 429, 5xx) are now retried with backoff before they count against a model, and a snapshot failure is never blamed on the model.
4. **Non-code steps were expensive to verify.** A critic review of a diagnosis step cost about 160s and 30k tokens. Steps that change no code are now recorded as `reviewed: false` rather than sent to a critic; the hidden suite still gates the code-changing step.
5. **Attribution bug.** With a fixed critic model, a verdict event's `model` is the *critic's*. Routing history and the UI must credit the model being judged (`subject_model`); synthetic fixture runs are excluded from routing history.

**Two recorded runs** (`runs/recorded_*.jsonl`, replayed by the demo), made independently on different API key sets, show the same recovery. The orchestrator planned three steps: reproduce and inspect, fix, verify. The middle step is the gate, so the critic runs the hidden suite there. In both runs the first attempt on that step, by `gemini-3.5-flash-lite`, exceeded the per-call timeout (300s in the first run, 420s in the second) and returned nothing. The orchestrator recorded that failure and rerouted one rung up the ladder to `gemini-3.5-flash`, which completed the step (165s and 173s). The critic, in a fresh sandbox seeded with the coder's snapshot, ran the hidden suite, saw 8 of 8 tests pass, and submitted a structured verdict. Steps 1 and 3 change no code, so they are recorded as unreviewed. The first run took 20m23s and 277,027 tokens; the second 15m19s and 214,025 tokens; each made four coder attempts across the three steps. In both, the recovered failure was a timeout, not a failed hidden test; the failed-verdict path (critic rejects, orchestrator reflects, sub-task is rewritten) is covered by tests and is what a weaker first attempt would trigger.

## 4. Why this meets the bar

- **Genuine multi-agent collaboration, not glued prompts.** The critic is independent: its own sandbox, its own model, tests the coder cannot see, and a structured verdict. The orchestrator's plan and its reflection are model outputs validated against schemas, not templates. The escalation is bounded and evidence-driven, and when the reflection fails we fall back and record that we did.
- **Tool use.** Both agents use the sandbox's code execution and filesystem tools; the critic additionally calls our `submit_verdict` function tool (the Interactions API `requires_action` round trip); the orchestrator uses the environment-snapshot endpoint.
- **State over a long horizon.** A replayable event log records every step, attempt, verdict and reroute. The orchestrator carries the workspace between steps and attempts, and its reasoning chain persists across calls.
- **Recovery when a step fails.** Bounded attempts per step, a model ladder to climb, timeouts on every call, retries with backoff, key failover, token and wall-clock ceilings, and a guarantee that a run always ends with exactly one `run_finished` event, even when everything goes wrong.

## 5. Honest limits

The demo is 3–5 steps, not twenty; the budgets and log scale, but we do not claim long-horizon results we did not run. Agent calls range from seconds to minutes, so the public demo replays a recorded real run at human speed. Rate limits are per Google project, so extra API keys only add capacity if they come from different projects. Reflections (validated cross-run learning) is roadmap.

**Try it:** `python scripts/run_demo.py` records a run; `uvicorn app.server:app` serves it. 200+ offline tests cover the gateway (retries, timeouts, key failover, tar safety), the orchestrator (recovery paths, isolation of the hidden suite) and the reflection logic.
