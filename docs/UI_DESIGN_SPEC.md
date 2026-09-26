# Roundtable Gem: UI Design Spec

**Audience:** (1) ChatGPT, to generate UI design images from this brief. (2) Claude, to implement those designs in Next.js. (3) The teammates who own the UI.
**How to use:** paste this whole document into ChatGPT, then follow section 11 (style tile first, then one screen at a time). When the images are approved, hand Claude this document + the images + a sample run file (section 12).

---

## 1. The hackathon and the problem

**Event:** Google DeepMind Hyderabad Hackathon (GDG Hyderabad × Kaggle).
**Track:** PS4, Autonomous Orchestration with Managed Agents.
**Stack the problem requires:** the Antigravity Agent (`antigravity-preview-09-2026`) through the Gemini Interactions API.
**Deadline:** Saturday 26 Sep 2026, 3:00 PM IST. Submission = a Kaggle writeup (≤1500 words), a public code repo, and a live demo with no login or paywall.

**Problem statement (verbatim):**

> **Focus:** Antigravity Agent (antigravity-preview-09-2026) via the Interactions API
>
> **The Challenge:** Single-prompt wrappers break when a task takes twenty steps, spans multiple systems, or hits an error halfway through. Use the Interactions API and Antigravity managed agents to build stateful, multi-agent systems that plan, delegate, track state over long horizons, and recover when a step fails.
>
> **The Bar:** We want genuine multi-agent collaboration and tool use, not three prompts glued together with if/else statements.

## 2. What we understood from it

- **Recovery is the star.** The judges score what happens when a step fails, not the happy path.
- **The Antigravity agent runs its own loop** inside a Google-hosted Linux sandbox, but the API has no agent-to-agent delegation. So the orchestration layer between agents is ours to build.
- **"Genuine" means independent verification and model-made decisions**, not a scripted retry.
- **It must be legible.** A judge has about two minutes. The UI's whole job is to make the failure and the recovery obvious.

## 3. What we are building

A small team of agents that fix a deliberately broken Python script, and a UI that replays exactly how they did it.

| Role | What it is | Model |
|---|---|---|
| **Orchestrator** | A plain Gemini call (Interactions API, structured JSON) that turns the task into 2–5 steps and decides what happens after a failure. | `gemini-3.8-flash` |
| **Coder** | An Antigravity agent in its own Linux sandbox. Attempts each step. Starts on the cheap model and **escalates** when it fails. | `gemini-3.5-flash-lite` → `gemini-3.5-flash` → `gemini-3.8-flash` (one rung up per failed attempt) |
| **Critic** | A separate Antigravity agent in a **fresh** sandbox. Runs a **hidden test suite the coder never saw** and submits a structured verdict through a function tool. | `gemini-3.5-flash` (fixed; a judge is never routed down) |

**The loop:** plan → coder attempts → critic judges → **fail** → orchestrator reroutes (stronger model + the critic's findings) → coder retries → **pass**.
**State log:** every input, output and verdict is recorded as an event. The UI replays that log.
**Demo task:** `inventory_report.py` crashes on its sample CSV and has subtler requirements in a spec file. The first attempt tends to miss some, the hidden tests catch it, and the retry on the stronger model passes.

**Lineage:** built from RoundtableCI, our existing product that routes queries to the best model using real race outcomes and uses a judge model. We ported its tool registry, event log and routing policy. The in-run retry loop is new. "Reflections" (the orchestrator learns, not the model) is roadmap only. Never present it as built.

**Status when this was written:** the backend is wired and tested offline, live runs are in progress, and the first real recorded run is pending. The public demo is **replay-only** (a recorded real run), so visitors can't spend API quota.

## 4. What the UI is for

- **Viewers:** hackathon judges (technical, Google/DeepMind folks), ~2 minutes, laptop or phone, arriving from a link.
- **The 10-second goal:** a viewer sees *an agent failed a hidden test → the orchestrator rerouted to a stronger model → it passed.*
- **Honesty rules (design must support these):**
  - Always label **Replay** vs **Live**.
  - If `run_started.payload.fixture` is true, show a persistent **FIXTURE · synthetic sample data** badge. Never present synthetic data as a real run.
  - Show the real model IDs from the data (`gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-3.8-flash`). Show a Gemma mark only if a `gemma-*` model id actually appears in the data. Today none does.
  - Don't reproduce the official Google, Gemini, Gemma or Antigravity logos. Use plain-text mentions and original abstract marks (a four-point sparkle, an upward chevron).
  - Don't invent metrics. Every number on screen comes from the run data.

## 5. Design direction: "Google-ish, a little"

**Vibe:** a Google product at night. Clean, confident, friendly, rounded, with generous space. Dark obsidian base (inherited from Roundtable) with soft aurora glow. Google-ish comes from shapes, type and *semantic* color, not from a white Material template.

**Palette (dark; approximate Google tints, not official assets)**

| Token | Hex | Use |
|---|---|---|
| bg-0 | `#0B0D12` | page background |
| bg-1 | `#11141B` | sections |
| surface | `#171B24` at 60% + 20px blur | glass panels |
| border | `rgba(255,255,255,0.08)` | hairlines |
| text | `#E8EAED` | primary text |
| text-muted | `#9AA0A6` | secondary text |
| info / delegate | `#8AB4F8` (base `#4285F4`) | working, delegation |
| pass | `#81C995` (base `#34A853`) | passed verdicts, success |
| fail | `#F28B82` (base `#EA4335`) | failed verdicts, failures |
| reroute | `#FDD663` (base `#FBBC04`) | reroute, retry |
| Gemini gradient | `#4285F4 → #9B72CB → #D96570` | highlights only: headline word, primary CTA glow, orchestrator accent |

Google's four colors are used **semantically**: blue = working, green = pass, red = fail, yellow = reroute. That semantic mapping is the main "Google-ish" move. Keep the gradient rare.

**Type** (all via `next/font/google`, self-hosted): display **Outfit** 600 (closest free feel to Google Sans), UI **Inter** 400/500, mono **JetBrains Mono**. Scale 12 / 14 / 16 / 20 / 28 / 44 / 72; display tracking −0.02em.

**Shape:** Material-3-style large radii: panels 28px, cards 20px, chips/buttons/pills fully rounded. Elevation from soft colored glows plus a 1px inner top highlight, not heavy drop shadows. Icons: Material Symbols Rounded (or Lucide, 2px rounded stroke).

**The Antigravity motif (signature idea):** *gravity vs anti-gravity.*
- **Working** nodes lift and glow.
- **A failed verdict makes the critic node fall** (accelerating drop, red glow, small shake).
- **The reroute lifts the coder back up** in amber, then the pass lands green.
- Ambient drift: nodes float gently, particles drift upward.
Call the recovery moment "the lift." It should be the most memorable animation in the product.

## 6. Tech and animation stack (for the implementation phase)

Build a **fast Next.js app**: App Router, TypeScript, Tailwind. Server components for static content; animated parts are client components. Target: LCP under 2.5s, initial JS under ~150 kB gzipped (excluding lazy chunks).

| Tool | Where it is used | Rules |
|---|---|---|
| **GSAP** (+ `@gsap/react`, ScrollTrigger) | All timeline animation: node states, connector pulses, event-row stagger-in, recovery banner reveal, stat count-ups, the landing page's pinned "how it works" scroll sequence. | One master timeline per replay; `useGSAP` for cleanup. |
| **Lenis** | Smooth scrolling on the **landing page only**. | Sync with ScrollTrigger (`lenis.on('scroll', ScrollTrigger.update)`; drive `lenis.raf` from `gsap.ticker`, `gsap.ticker.lagSmoothing(0)`). Put `data-lenis-prevent` on the state-log scroller and drawers so they scroll natively. Disable under `prefers-reduced-motion`. |
| **Vanta** | **Hero background only.** Recommended effect: `NET` (nodes and links read as an agent network), tinted Google blue/purple on `#0B0D12`, low density (~8–10 points), wide spacing. Alternatives: `HALO`, `DOTS`. | Client-only dynamic import after first paint; `destroy()` on unmount; pause when off-screen (IntersectionObserver); fall back to a CSS gradient under reduced-motion or on low-power devices. Vanta needs `three` (it targets an older release, around r134), so test the import. |
| **React Bits** (copy-paste, TS + Tailwind variants) | Micro-interactions and text effects (suggestions below). | **One WebGL/canvas background per page**, so React Bits backgrounds are for the run page; Vanta is the hero's. Verify each component name on reactbits.dev; if one is missing, use the nearest. |

**React Bits suggestions by role:** hero headline → `SplitText` or `BlurText`; the gradient word → `GradientText` or `ShinyText`; stats → `CountUp`; feature and lineage cards → `SpotlightCard`; primary CTA → `StarBorder` + `Magnet`; click feedback → `ClickSpark`; "how it works" → `Stepper`; run-page ambient background → a subtle `Particles` or `Aurora`. If React Bits offers an "Antigravity" particle component, use it for the hero accent, since it matches the hackathon's theme.

## 7. Screens

Design at **1440×900** desktop and **390×844** mobile. 12-column grid, max width 1280 (landing) / 1480 (app), 24px desktop gutters, 16px mobile gutters.

### A. Landing page (`/`)

1. **Hero (full viewport).** Vanta NET background. Floating centered glass nav pill: logo "Roundtable Gem" left; links *How it works · Watch a run · Lineage*; a chip "GDG × Kaggle · PS4" right.
   Headline: **"Agents that fail forward."** ("forward" in the Gemini gradient.)
   Sub: *"A Coder and a Critic on Google's Antigravity agent. The first attempt fails a hidden test. The orchestrator reflects, reroutes, and recovers, on screen."*
   Buttons: primary pill **"Watch the recovery ▶"**, ghost **"How it works"**. Under them, three small chips: *Antigravity agent · Interactions API · Gemini*. A glass preview card of the run view peeks up from the bottom edge. A scroll cue sits below it.
2. **The problem.** "Single prompts break at step 12." Three glass cards, straight from the problem statement: *Twenty steps · Multiple systems · An error halfway*.
3. **How it works.** Pinned scroll sequence, four beats: **Plan → Delegate → Verify → Recover.** Each beat highlights the matching part of a diagram of Orchestrator, Coder and Critic.
4. **The recovery moment.** An embedded, looping mini-replay of the real run with the caption *"Step 2 failed on gemini-3.5-flash-lite → rerouted → passed on gemini-3.5-flash."* (Caption text is generated from the data.)
5. **Built on RoundtableCI.** Three cards: *The judge role · Model routing · The event log.* A fourth, visually distinct card labeled **Roadmap**: *Reflections: the orchestrator learns, not the model.*
6. **Footer.** Repo link, hackathon line, team placeholders.

### B. Run view (`/run/[id]`), the core screen

- **Top bar:** logo, run title, status pill (Replay / Live / Succeeded / Failed / Disconnected), FIXTURE badge when relevant.
- **Left rail (300px, glass):**
  - *Run card:* task text, category chip, run id.
  - *Agents card:* three rows (Orchestrator, Coder, Critic), each with mark, current model chip and a status dot.
  - *Progress:* steps passed (e.g. 2 / 3), attempts, recoveries, tokens.
- **Main column:**
  1. **Orchestration panel.** Recovery banners on top, then the graph: Orchestrator at top center, Coder bottom-left, Critic bottom-right. Three flowing connectors: *delegate* (orchestrator → coder), *submit* (coder → critic), *verdict* (critic → orchestrator). Each node is a 64px disc with a mark, role label, model chip and one-line note.
  2. **State log.** Step headers ("Step 2 · Fix parsing"), then event rows: seq, +time, kind badge, agent · model, one-line summary. Click a row to open the inspector drawer (screen C).
- **Sticky replay bar (bottom):** play/pause, speed (1× / 2× / 4× / Instant), a scrubber with tick marks colored by meaning (red = fail, yellow = reroute, green = pass).

**Node states** (drive with GSAP): *idle* (gentle drift) · *working* (lifted 12px, blue glow ring, connector dashes speed up) · *pass* (green ring, small lift) · *fail* (drops 16px with accelerating ease, red glow, brief shake).

**Recovery banner** (appears when a step passes after an earlier fail; model names come from the data, these are examples): *"Step 2 recovered: failed on `gemini-3.5-flash-lite` → rerouted → passed on `gemini-3.5-flash` (attempt 2)."* Green-tinted glass, rises in.

### C. Event inspector drawer

Right-side sheet, 480px, scrolls natively. Header shows kind badge, seq, time, agent · model. Tabs: **Summary | Raw JSON**.
- *verdict:* pass/fail, reasons as a list, failing tests as chips, the evidence line, and two labels: **"Judged: <coder model>"** and **"Critic: <critic model>"**.
- *agent_call:* the prompt text with a copy button.
- *agent_result:* status, tokens, elapsed seconds, output.
- *reroute:* from-model → to-model and the modified sub-task.

### D. The recovery moment (storyboard for animation reference)

Four frames. Generate them as one 4-panel image.
1. **Attempt 1 working:** coder lifted and glowing blue, delegate connector bright. Critic idle.
2. **Verdict fail:** critic drops with a red glow, verdict connector flashes red, a red chip "2 failing".
3. **Reroute:** an amber connector pulses to the coder, whose model chip changes to `gemini-3.5-flash`, note "↻ rerouted".
4. **Recovered:** coder lifts, critic turns green, the green recovery banner rises in.

### E. Mobile run view (390×844)

Status pill on top; condensed graph (Orchestrator on top, Coder and Critic side by side); recovery card; the state log as a full-width list (tap opens the drawer as a bottom sheet); the replay bar pinned at the bottom.

### F. States

Loading ("Connecting to run…"), no recorded runs, disconnected (amber banner with Retry), run failed (red banner: *"Run failed at step 2: exhausted 3 attempts"*), fixture badge.

## 8. Motion principles

- Durations 0.4–0.9s; ease-out for lifts, a gravity-style ease-in for falls (`cubic-bezier(0.6, 0, 1, 0.5)`).
- Connectors are dashed lines with a flowing offset; when active they thicken, brighten in the state's color, and speed up.
- Event rows rise 6px and fade in with a small stagger; never animate more than ~10 rows at once.
- Replay pacing: clamp real gaps between events to 0.2–1.5s, divided by the speed setting.
- Everything respects `prefers-reduced-motion` (no drift, no falls, instant state changes).

## 9. Data contract

**Source.** A run is an ordered list of events. For the hosted demo, ship a recorded run as a static file (`public/runs/<id>.json`, the response of `GET /runs/<id>`) and replay it in the browser with no backend. A live backend is optional (local development).

**Event shape** (all fields present):
`{ run_id, seq, ts (unix seconds), kind, step (1-based or null), attempt (default 1), agent ("orchestrator" | "coder" | "critic" | null), model (string or null), payload (object) }`

| kind | payload fields | Notes |
|---|---|---|
| `run_started` | `task, category, budget, coder_ladder, critic_model, orchestrator_model, fixture?` | `fixture: true` = synthetic → show the badge |
| `step_started` | `title, instruction, acceptance, modifies_code, gate` | `gate: true` = the hidden test suite runs at this step |
| `agent_call` | `prompt, purpose?, gate?` | `purpose: "plan"` is the orchestrator's planning call |
| `agent_result` | `status, tokens, elapsed_s, output, key?, error?` | orchestrator statuses `fallback` and `snapshot_error` exist. `key` is a non-secret label (`k1`…`k6`) of the dedicated API key that served the call; optional tiny "key 2" chip on a node, never anything resembling a real key |
| `tool_called` / `tool_returned` | `tool, arguments` / `tool, result` | the critic's `submit_verdict` shows up here |
| `verdict` | `passed, reasons[], failing_tests[], evidence, category, subject_model, gate` | see the attribution rule below |
| `reflection` | `summary` | may be absent in early runs |
| `reroute` | `from_model, to_model, modified_subtask` | drives the amber reroute animation |
| `step_succeeded` | `attempts` | |
| `step_failed` | `reason` | |
| `run_finished` | `succeeded, steps_total, steps_succeeded, attempts_used, tokens, error?` | terminal |

**Model attribution rule (easy to get wrong):** on a `verdict` event, `event.model` is the **critic's** model. The model being judged, meaning the coder's, is `payload.subject_model`. For the recovery banner and scrubber ticks use `payload.subject_model ?? event.model`. (The synthetic fixture is an older shape without `subject_model`, which is what the fallback covers.)

**Deriving the on-screen state (the "run player")** from the events in order: `agent_call` → that agent's node = working; `agent_result` → idle (or fail if status isn't `completed`); `verdict` → critic pass/fail + a verdict-connector pulse; `reroute` → coder note "↻ rerouted", model chip updates, amber pulse; `step_succeeded` after any earlier failed verdict for that step → add a recovery banner; `run_finished` → orchestrator pass/fail and the status pill. A working reference of exactly this logic is `ui/index.html` in the repo. Treat it as **logic reference only**; the visuals are being replaced, and it has not been verified in a browser.

**Optional live/backend endpoints** (FastAPI): `GET /runs` (list; real runs first, fixtures last), `GET /runs/{id}` (all events), `GET /stream/{id}` (SSE, one event per message, `id:` = `seq`, supports `?since=` and `Last-Event-ID`, `?pace=1&speed=2` to replay a finished run). **Close the `EventSource` when `run_finished` arrives**, or the browser reconnects forever. The backend has no CORS yet; either proxy through Next.js `rewrites` or add CORS (a small backend change).

**Sample data:** `runs/sample_fixture.jsonl` (synthetic; use it to build against) and, once recorded, `runs/<run_id>.jsonl` (real). Convert JSONL to the `{ run_id, events }` JSON shape for `public/runs/`.

## 10. Constraints and acceptance criteria

- **Performance:** LCP < 2.5s on the landing page with Vanta loaded after first paint; no layout shift; Vanta and React Bits WebGL chunks lazy-loaded; one canvas background per page.
- **Accessibility:** AA contrast on dark; the event list and replay controls are keyboard-operable; recovery banners use `aria-live="polite"`; reduced-motion supported.
- **Responsive:** works at 390 / 768 / 1440 with no horizontal page scroll.
- **Self-contained:** fonts self-hosted via `next/font`; the recorded run bundled as a static asset; no runtime dependency on a live API for the public demo; no secrets or API keys anywhere in the front end.
- **Honesty:** every rule in section 4 is visibly enforced.

## 11. Prompts to paste into ChatGPT

Image generators mangle small text, so ask for **layout, color, spacing and hierarchy**; placeholder text is fine because real text is typed in code. Generate the **style tile first**, then reuse its look in every later prompt.

**Global style block (prepend to every prompt):**
> Dark, obsidian UI (#0B0D12 background) with glassmorphism panels (#171B24 at 60% with blur), 28px rounded corners, pill-shaped buttons, Google-Sans-like geometric typography (Outfit headings, Inter body). Semantic accent colors: soft blue #8AB4F8 = working, soft green #81C995 = pass, soft red #F28B82 = fail, soft yellow #FDD663 = reroute. Rare accent gradient blue #4285F4 → purple #9B72CB → coral #D96570. Subtle aurora glow, faint stars, a thin celestial arc. Calm, premium, Google-product-at-night feel. No real brand logos. Placeholder text is fine.

1. **Style tile:** "A design system board: color swatches with names, type scale (Outfit + Inter + JetBrains Mono), button and chip styles, a glass panel, four node states (idle, working with blue glow and lifted, pass with green ring, fail dropped with red glow), connector line styles, and an event-row component."
2. **Landing hero, 1440×900:** section 7A.1 as written, with a network-of-nodes background, the floating nav pill, the headline with a gradient word, two pill buttons, three small chips, and a glass preview card peeking from the bottom.
3. **Run view, 1440×900:** section 7B as written: top bar, 300px left rail (run card, three agent rows, progress stats), the orchestration graph with three glowing discs and curved animated connectors, a green recovery banner above it, the state-log list below, and a sticky replay bar with a scrubber that has red, yellow and green ticks.
4. **Graph close-up (the fail state):** "Close-up of the orchestration graph: Orchestrator at top center, Coder bottom-left, Critic bottom-right. The Critic disc has dropped lower with a red glow and a '2 failing' chip; a bright red connector runs from Critic to Orchestrator; an amber connector runs from Orchestrator to Coder with a model chip reading 'gemini-3.5-flash' and the note '↻ rerouted'."
5. **Inspector drawer:** section 7C, a 480px right sheet over the run view showing a failed verdict (reasons list, red failing-test chips, "Judged" and "Critic" labels).
6. **Storyboard:** section 7D as one 4-panel image, panels labeled 1 to 4.
7. **Mobile run view, 390×844:** section 7E.
8. **States sheet:** loading, disconnected (amber banner with Retry), run failed (red banner), and the FIXTURE badge on the top bar.

## 12. Hand-off to Claude (implementation phase)

Give Claude: this document, the approved images, `runs/sample_fixture.jsonl` (or a real recorded run), and the repo path (`roundtable-gem/`). Ask it to build the app in a new `web/` folder in this order:

1. Design tokens (Tailwind theme from section 5), fonts, layout shell.
2. A `useRunPlayer(events, { speed })` hook that implements section 9's state derivation (port the logic from `ui/index.html`).
3. The orchestration graph, with GSAP node states and connector pulses.
4. The state-log timeline and the inspector drawer.
5. The replay bar and scrubber.
6. The landing page (Lenis, Vanta hero, React Bits text and cards, the pinned "how it works" sequence).
7. Reduced-motion, accessibility and performance pass against section 10.

Definition of done: the recovery moment (section 7D) plays from a real recorded run, with correct model attribution in the banner, on desktop and mobile.
