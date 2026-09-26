// The landing page's "a real run" story, derived from a recording's events (nothing here is typed in by hand).
import type { RunEvent } from "./events";
import { replay } from "./runPlayer";

export interface Story {
  runId: string;
  models: { orchestrator: string; coderLadder: string[]; critic: string };
  planTitles: string[];
  steps: number;
  coderAttempts: number;
  recoveries: number;
  durationS: number;
  tokens: number;
  succeeded: boolean;
  failure: { step: number; title: string; model: string; reason: "timeout" | "verdict" | "error"; afterS: number | null } | null;
  recoveredModel: string | null;
  recoveredAfterS: number | null;
  hiddenTests: number | null; // parsed from the critic's own evidence line, e.g. "All 8 tests passed"
  evidence: string;
}

export function deriveStory(events: RunEvent[]): Story {
  const s = replay(events);
  const started = events.find((e) => e.kind === "run_started")?.payload ?? {};
  const rec = s.recoveries[0] ?? null;
  const failedResult = rec
    ? events.find((e) => e.kind === "agent_result" && e.agent === "coder" && e.step === rec.step && e.payload.status !== "completed")
    : undefined;
  const passedResult = rec
    ? [...events].reverse().find((e) => e.kind === "agent_result" && e.agent === "coder" && e.step === rec.step && e.payload.status === "completed")
    : undefined;
  const evidence = s.verdict?.evidence ?? "";
  const m = evidence.match(/(\d+)\s+(?:hidden\s+)?(?:test|check)/i) ?? evidence.match(/all\s+(\d+)/i);
  return {
    runId: events[0]?.run_id ?? "",
    models: { orchestrator: started.orchestrator_model ?? "", coderLadder: started.coder_ladder ?? [], critic: started.critic_model ?? "" },
    planTitles: s.planTitles,
    steps: s.stepsTotal,
    coderAttempts: s.coderAttempts,
    recoveries: s.recoveries.length,
    durationS: (events[events.length - 1]?.ts ?? 0) - (events[0]?.ts ?? 0),
    tokens: s.tokens,
    succeeded: s.status === "succeeded",
    failure: rec ? { step: rec.step, title: rec.title, model: rec.failedModel ?? "", reason: rec.failedReason, afterS: failedResult ? Number(failedResult.payload.elapsed_s) || null : null } : null,
    recoveredModel: rec?.passedModel ?? null,
    recoveredAfterS: passedResult ? Number(passedResult.payload.elapsed_s) || null : null,
    hiddenTests: m ? Number(m[1]) : null,
    evidence,
  };
}
