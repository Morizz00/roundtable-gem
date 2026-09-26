// Pure replay engine: fold a run's events into what the UI shows. No React, no I/O, fully unit-tested.
// Ported from the reference reducer in ui/index.html, with two corrections learned from real runs:
//  - a verdict's `model` is the CRITIC's; the model being judged is payload.subject_model
//  - a coder call that times out is a failure too (both recorded runs recover from exactly that)

import { clip } from "./format";
import type { Role, RunEvent } from "./events";

export type NodeState = "idle" | "working" | "pass" | "fail";
export type Tone = "" | "good" | "bad" | "warn";
export type Edge = "delegate" | "submit" | "verdict" | "reroute";
export type FailReason = "timeout" | "verdict" | "error";

export interface NodeView {
  state: NodeState;
  model: string | null;
  note: string;
  tone: Tone;
}

export interface Attempt {
  attempt: number;
  model: string | null;
  outcome: "pass" | "fail" | "timeout" | "error" | "completed";
  reason?: string;
}

export interface StepView {
  n: number;
  title: string;
  instruction: string;
  acceptance: string;
  modifiesCode: boolean;
  gate: boolean;
  status: "running" | "passed" | "failed";
  reviewed: boolean | null; // null until the step finishes; false = not sent to the critic
  attempts: Attempt[];
  failReason?: string;
}

export interface Recovery {
  step: number;
  title: string;
  failedModel: string | null;
  failedReason: FailReason;
  failedDetail: string;
  passedModel: string | null;
  attempts: number;
}

export interface PlayerState {
  applied: number;
  runId: string;
  task: string;
  category: string;
  fixture: boolean;
  status: "idle" | "running" | "succeeded" | "failed";
  t0: number | null;
  now: number | null;
  nodes: Record<Role, NodeView>;
  steps: StepView[];
  recoveries: Recovery[];
  coderAttempts: number;
  tokens: number;
  stepsTotal: number;
  stepsPassed: number;
  hot: { edge: Edge; tone: "info" | "ok" | "bad" | "warn"; seq: number } | null;
  verdict: { passed: boolean; evidence: string; reasons: string[]; failingTests: string[]; subjectModel: string | null } | null;
  planTitles: string[];
  error: string | null;
}

const blankNode = (model: string | null = null): NodeView => ({ state: "idle", model, note: "", tone: "" });

export function initialState(): PlayerState {
  return {
    applied: 0, runId: "", task: "", category: "", fixture: false, status: "idle", t0: null, now: null,
    nodes: { orchestrator: blankNode(), coder: blankNode(), critic: blankNode() },
    steps: [], recoveries: [], coderAttempts: 0, tokens: 0, stepsTotal: 0, stepsPassed: 0,
    hot: null, verdict: null, planTitles: [], error: null,
  };
}

function withNode(s: PlayerState, role: Role, patch: Partial<NodeView>): PlayerState {
  return { ...s, nodes: { ...s.nodes, [role]: { ...s.nodes[role], ...patch } } };
}

function stepOf(s: PlayerState, n: number | null): StepView | undefined {
  return n == null ? undefined : s.steps.find((x) => x.n === n);
}

function updateStep(s: PlayerState, n: number | null, fn: (st: StepView) => StepView): PlayerState {
  if (n == null) return s;
  return { ...s, steps: s.steps.map((st) => (st.n === n ? fn(st) : st)) };
}

/** Why the first failed attempt of a step failed, for the recovery banner. */
function firstFailure(step: StepView): Attempt | undefined {
  return step.attempts.find((a) => a.outcome !== "pass" && a.outcome !== "completed");
}

export function applyEvent(prev: PlayerState, e: RunEvent): PlayerState {
  const p = e.payload ?? {};
  let s: PlayerState = { ...prev, applied: prev.applied + 1, now: e.ts, t0: prev.t0 ?? e.ts };

  switch (e.kind) {
    case "run_started":
      s = {
        ...s, runId: e.run_id, task: String(p.task ?? ""), category: String(p.category ?? ""), fixture: Boolean(p.fixture),
        status: "running",
        nodes: {
          orchestrator: { state: "working", model: p.orchestrator_model ?? null, note: "planning", tone: "" },
          coder: blankNode(Array.isArray(p.coder_ladder) ? p.coder_ladder[0] : null),
          critic: blankNode(p.critic_model ?? null),
        },
      };
      return s;

    case "step_started": {
      const step: StepView = {
        n: e.step ?? s.steps.length + 1, title: String(p.title ?? ""), instruction: String(p.instruction ?? ""),
        acceptance: String(p.acceptance ?? ""), modifiesCode: Boolean(p.modifies_code), gate: Boolean(p.gate),
        status: "running", reviewed: null, attempts: [],
      };
      s = { ...s, steps: [...s.steps, step], stepsTotal: Math.max(s.stepsTotal, step.n) };
      s = withNode(s, "orchestrator", { state: "working", note: `step ${step.n}: ${clip(step.title, 44)}`, tone: "" });
      s = withNode(s, "coder", { state: "idle", note: "" });
      return withNode(s, "critic", { state: "idle", note: "" });
    }

    case "agent_call": {
      const role = (e.agent ?? "coder") as Role;
      if (role === "coder") s = { ...s, coderAttempts: s.coderAttempts + 1, hot: { edge: "delegate", tone: "info", seq: e.seq } };
      if (role === "critic") s = { ...s, hot: { edge: "submit", tone: "info", seq: e.seq } };
      const note = role === "orchestrator" ? (p.purpose === "plan" ? "planning" : "thinking") : `attempt ${e.attempt || 1}`;
      return withNode(s, role, { state: "working", model: e.model ?? s.nodes[role].model, note, tone: "" });
    }

    case "agent_result": {
      const role = (e.agent ?? "coder") as Role;
      s = { ...s, tokens: s.tokens + (Number(p.tokens) || 0) };
      const status = String(p.status ?? "");
      if (role === "orchestrator") {
        if (p.purpose === "plan" || status === "completed" || status === "fallback") {
          const out = String(p.output ?? "");
          if (out && s.planTitles.length === 0 && status !== "snapshot_error") s = { ...s, planTitles: out.split(" | ").map((t) => t.trim()).filter(Boolean) };
        }
        return withNode(s, "orchestrator", { state: "working", note: status === "snapshot_error" ? "snapshot retry" : "", tone: status === "fallback" ? "warn" : "" });
      }
      const bad = status !== "completed";
      const outcome: Attempt["outcome"] = status === "completed" ? "completed" : status === "timeout" ? "timeout" : "error";
      if (role === "coder") {
        s = updateStep(s, e.step, (st) => ({
          ...st,
          attempts: [...st.attempts, { attempt: e.attempt || 1, model: e.model, outcome, reason: bad ? String(p.error ?? status) : undefined }],
        }));
      }
      return withNode(s, role, {
        state: bad ? "fail" : "idle",
        note: bad ? (status === "timeout" ? `timed out (${Math.round(Number(p.elapsed_s) || 0)}s)` : status) : "done",
        tone: bad ? "bad" : "",
      });
    }

    case "tool_called":
      return withNode(s, (e.agent ?? "critic") as Role, { state: "working", note: `${p.tool ?? "tool"}…`, tone: "" });

    case "tool_returned":
      return s;

    case "verdict": {
      const passed = Boolean(p.passed);
      const subject = (p.subject_model as string | undefined) ?? e.model;
      s = updateStep(s, e.step, (st) => ({
        ...st,
        // the coder's attempt was recorded as "completed"; the critic's verdict is what decides it
        attempts: st.attempts.map((a) => (a.attempt === (e.attempt || 1) && a.outcome === "completed" ? { ...a, outcome: passed ? "pass" : "fail", reason: passed ? undefined : (p.reasons ?? []).join("; ") } : a)),
      }));
      s = {
        ...s,
        hot: { edge: "verdict", tone: passed ? "ok" : "bad", seq: e.seq },
        verdict: { passed, evidence: String(p.evidence ?? ""), reasons: p.reasons ?? [], failingTests: p.failing_tests ?? [], subjectModel: subject ?? null },
      };
      return withNode(s, "critic", {
        state: passed ? "pass" : "fail",
        note: passed ? "verdict: pass" : `${(p.failing_tests ?? []).length} failing`,
        tone: passed ? "good" : "bad",
      });
    }

    case "reflection":
      return withNode(s, "orchestrator", { state: "working", note: "reflecting on failure", tone: "" });

    case "reroute":
      s = { ...s, hot: { edge: "reroute", tone: "warn", seq: e.seq } };
      s = withNode(s, "orchestrator", { state: "working", note: "rerouting", tone: "warn" });
      return withNode(s, "coder", { state: "idle", model: p.to_model ?? s.nodes.coder.model, note: "↻ rerouted", tone: "warn" });

    case "step_succeeded": {
      const step = stepOf(s, e.step);
      const failure = step ? firstFailure(step) : undefined;
      const passedAttempt = step ? [...step.attempts].reverse().find((a) => a.outcome === "pass" || a.outcome === "completed") : undefined;
      s = updateStep(s, e.step, (st) => ({ ...st, status: "passed", reviewed: p.reviewed === false ? false : true }));
      s = { ...s, stepsPassed: s.stepsPassed + 1 };
      if (step && failure) {
        const reason: FailReason = failure.outcome === "timeout" ? "timeout" : failure.outcome === "fail" ? "verdict" : "error";
        s = {
          ...s,
          recoveries: [...s.recoveries, {
            step: step.n, title: step.title, failedModel: failure.model, failedReason: reason,
            failedDetail: failure.reason ?? "", passedModel: passedAttempt?.model ?? null,
            attempts: Number(p.attempts ?? step.attempts.length),
          }],
        };
      }
      return withNode(s, "orchestrator", { state: "pass", note: `step ${e.step} passed`, tone: "good" });
    }

    case "step_failed":
      s = updateStep(s, e.step, (st) => ({ ...st, status: "failed", failReason: String(p.reason ?? "") }));
      return withNode(s, "orchestrator", { state: "fail", note: `step ${e.step} failed`, tone: "bad" });

    case "run_finished": {
      const ok = Boolean(p.succeeded);
      s = { ...s, status: ok ? "succeeded" : "failed", error: p.error ?? null, tokens: typeof p.tokens === "number" ? p.tokens : s.tokens };
      if (typeof p.steps_total === "number") s = { ...s, stepsTotal: p.steps_total };
      s = withNode(s, "orchestrator", { state: ok ? "pass" : "fail", note: ok ? "run succeeded" : "run failed", tone: ok ? "good" : "bad" });
      s = withNode(s, "coder", { state: "idle" });
      return withNode(s, "critic", { state: s.nodes.critic.state === "fail" ? "fail" : s.nodes.critic.state === "pass" ? "pass" : "idle" });
    }

    default:
      return s;
  }
}

/** State after the first `count` events (all of them if omitted). */
export function replay(events: RunEvent[], count: number = events.length): PlayerState {
  let s = initialState();
  for (let i = 0; i < Math.min(count, events.length); i++) s = applyEvent(s, events[i]);
  return s;
}

/** Seconds to wait before showing event `next` after `prev`: real gaps clamped to 0.2-1.5s, divided by speed. 0 = instant. */
export function gapSeconds(prev: RunEvent | undefined, next: RunEvent, speed: number): number {
  if (!prev || speed <= 0) return 0;
  return Math.min(Math.max(next.ts - prev.ts, 0.2), 1.5) / speed;
}

/** One-line description of an event for the log and timeline. */
export function summarize(e: RunEvent): string {
  const p = e.payload ?? {};
  switch (e.kind) {
    case "run_started": return clip(p.task, 160);
    case "step_started": return `${p.title ?? ""}${p.gate ? "  · hidden-suite gate" : ""}`;
    case "agent_call": return `${p.purpose === "plan" ? "plan" : "call"}: ${clip(p.prompt, 120)}`;
    case "agent_result": {
      const tail = p.error ? String(p.error) : clip(p.output, 110);
      return [p.status, p.tokens ? `${p.tokens} tok` : "", p.elapsed_s ? `${Math.round(Number(p.elapsed_s))}s` : "", tail].filter(Boolean).join(" · ");
    }
    case "tool_called": return `${p.tool}(${clip(JSON.stringify(p.arguments ?? {}), 90)})`;
    case "tool_returned": return `${p.tool} → ${clip(p.result, 90)}`;
    case "verdict": return p.passed ? `PASS · ${clip(p.evidence, 120)}` : `FAIL · ${clip((p.reasons ?? []).join("; "), 140)}`;
    case "reflection": return clip(p.summary, 180);
    case "reroute": return `${p.from_model ?? "?"} → ${p.to_model ?? "?"}${p.escalated === false ? " (same model)" : ""}`;
    case "step_succeeded": return p.reviewed === false ? `passed on attempt ${p.attempts ?? e.attempt} · not sent to the critic` : `passed on attempt ${p.attempts ?? e.attempt}`;
    case "step_failed": return clip(p.reason, 160);
    case "run_finished": return `${p.succeeded ? "Run succeeded" : "Run failed"}${p.steps_total != null ? ` · ${p.steps_succeeded}/${p.steps_total} steps` : ""}`;
    default: return clip(JSON.stringify(p), 140);
  }
}

export type MarkerTone = "bad" | "warn" | "ok";
export interface Marker { index: number; tone: MarkerTone; label: string }

/** Scrubber ticks: failed calls and verdicts (red), reroutes (amber), passes (green). `index` = events applied after it. */
export function markers(events: RunEvent[]): Marker[] {
  const out: Marker[] = [];
  events.forEach((e, i) => {
    const p = e.payload ?? {};
    if (e.kind === "agent_result" && e.agent !== "orchestrator" && p.status !== "completed") out.push({ index: i + 1, tone: "bad", label: `${e.agent} ${p.status}` });
    else if (e.kind === "verdict") out.push({ index: i + 1, tone: p.passed ? "ok" : "bad", label: p.passed ? "verdict pass" : "verdict fail" });
    else if (e.kind === "reroute") out.push({ index: i + 1, tone: "warn", label: "reroute" });
    else if (e.kind === "step_succeeded") out.push({ index: i + 1, tone: "ok", label: `step ${e.step} passed` });
  });
  return out;
}

export function describeRecovery(r: Recovery): string {
  const why = r.failedReason === "timeout" ? "timed out" : r.failedReason === "verdict" ? "failed review" : "errored";
  return `Step ${r.step} recovered: ${r.failedModel ?? "the first attempt"} ${why} → rerouted → passed on ${r.passedModel ?? "a stronger model"} (attempt ${r.attempts})`;
}
