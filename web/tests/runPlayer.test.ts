import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { RunEvent, RunFile, RunIndexEntry } from "../lib/events";
import { describeRecovery, gapSeconds, initialState, markers, replay, summarize } from "../lib/runPlayer";

const RUNS = path.join(__dirname, "..", "public", "runs");
const index: RunIndexEntry[] = JSON.parse(fs.readFileSync(path.join(RUNS, "index.json"), "utf-8"));
const load = (id: string): RunFile => JSON.parse(fs.readFileSync(path.join(RUNS, `${id}.json`), "utf-8"));

const LITE = "gemini-3.5-flash-lite";
const MID = "gemini-3.5-flash";

describe("the bundled recordings", () => {
  it("has at least two real (non-fixture) runs indexed", () => {
    expect(index.filter((r) => !r.fixture).length).toBeGreaterThanOrEqual(2);
  });

  for (const entry of index) {
    describe(entry.id, () => {
      const run = load(entry.id);
      const final = replay(run.events);
      const finished = run.events.find((e) => e.kind === "run_finished")!.payload;

      it("ends succeeded with every step passed", () => {
        expect(final.status).toBe("succeeded");
        expect(final.stepsTotal).toBe(3);
        expect(final.stepsPassed).toBe(3);
        expect(final.fixture).toBe(false);
      });

      it("derives the recovery from the events: the lite coder timed out, was rerouted, and 3.5-flash passed", () => {
        expect(final.recoveries).toHaveLength(1);
        const r = final.recoveries[0];
        expect(r.step).toBe(2);
        expect(r.failedModel).toBe(LITE);
        expect(r.failedReason).toBe("timeout");
        expect(r.passedModel).toBe(MID);
        expect(r.attempts).toBe(2);
        expect(describeRecovery(r)).toContain("timed out");
      });

      it("counts coder attempts and tokens exactly as the run reports them", () => {
        expect(final.coderAttempts).toBe(finished.attempts_used);
        expect(final.tokens).toBe(finished.tokens);
      });

      it("records the hidden-suite verdict, attributed to the judged model (not the critic)", () => {
        expect(final.verdict?.passed).toBe(true);
        expect(final.verdict?.evidence).toMatch(/8/);
        expect(final.verdict?.subjectModel).toBe(MID);
      });

      it("marks only the code-changing gate step as reviewed by the critic", () => {
        expect(final.steps.map((s) => s.reviewed)).toEqual([false, true, false]);
        expect(final.steps.map((s) => s.gate)).toEqual([false, true, false]);
        expect(final.planTitles).toHaveLength(3);
      });

      it("shows the coder as timed out right before the reroute, then rerouted to the stronger model", () => {
        const rerouteAt = run.events.findIndex((e) => e.kind === "reroute");
        const before = replay(run.events, rerouteAt); // events before the reroute
        expect(before.nodes.coder.state).toBe("fail");
        expect(before.nodes.coder.note).toMatch(/timed out/);
        const after = replay(run.events, rerouteAt + 1);
        expect(after.nodes.coder.note).toBe("↻ rerouted");
        expect(after.nodes.coder.model).toBe(MID);
        expect(after.hot?.edge).toBe("reroute");
      });

      it("puts scrubber ticks on the failure, the reroute and the passes", () => {
        const tones = markers(run.events).map((m) => m.tone);
        expect(tones).toContain("bad");
        expect(tones).toContain("warn");
        expect(tones.filter((t) => t === "ok").length).toBeGreaterThanOrEqual(3);
      });

      it("summarizes every event without throwing", () => {
        for (const e of run.events) expect(typeof summarize(e)).toBe("string");
      });
    });
  }
});

describe("a failed verdict (not in the recordings, so synthesized)", () => {
  const ev = (seq: number, kind: string, extra: Partial<RunEvent> = {}): RunEvent => ({
    run_id: "syn", seq, ts: 1000 + seq * 10, kind, step: null, attempt: 1, agent: null, model: null, payload: {}, ...extra,
  });
  const events: RunEvent[] = [
    ev(1, "run_started", { agent: "orchestrator", payload: { task: "t", category: "c", coder_ladder: [LITE, MID], critic_model: MID, orchestrator_model: "gemini-3.8-flash" } }),
    ev(2, "step_started", { step: 1, agent: "orchestrator", payload: { title: "Fix", modifies_code: true, gate: true } }),
    ev(3, "agent_call", { step: 1, agent: "coder", model: LITE }),
    ev(4, "agent_result", { step: 1, agent: "coder", model: LITE, payload: { status: "completed", tokens: 100 } }),
    ev(5, "verdict", { step: 1, agent: "critic", model: MID, payload: { passed: false, reasons: ["negatives summed"], failing_tests: ["t1", "t2"], subject_model: LITE, evidence: "FAILED (failures=2)" } }),
    ev(6, "reroute", { step: 1, agent: "orchestrator", payload: { from_model: LITE, to_model: MID, escalated: true } }),
    ev(7, "agent_call", { step: 1, attempt: 2, agent: "coder", model: MID }),
    ev(8, "agent_result", { step: 1, attempt: 2, agent: "coder", model: MID, payload: { status: "completed", tokens: 200 } }),
    ev(9, "verdict", { step: 1, attempt: 2, agent: "critic", model: MID, payload: { passed: true, reasons: [], failing_tests: [], subject_model: MID, evidence: "OK" } }),
    ev(10, "step_succeeded", { step: 1, attempt: 2, agent: "orchestrator", payload: { attempts: 2 } }),
    ev(11, "run_finished", { agent: "orchestrator", payload: { succeeded: true, steps_total: 1, steps_succeeded: 1, attempts_used: 2, tokens: 300 } }),
  ];

  it("credits the model being judged, not the critic, in the recovery", () => {
    const final = replay(events);
    expect(final.recoveries).toHaveLength(1);
    expect(final.recoveries[0]).toMatchObject({ failedModel: LITE, failedReason: "verdict", passedModel: MID });
  });

  it("shows the critic failing right after the failed verdict", () => {
    const s = replay(events, 5);
    expect(s.nodes.critic).toMatchObject({ state: "fail", tone: "bad", note: "2 failing" });
    expect(s.hot).toMatchObject({ edge: "verdict", tone: "bad" });
    expect(s.verdict?.passed).toBe(false);
  });
});

describe("replay pacing", () => {
  const at = (ts: number): RunEvent => ({ run_id: "x", seq: 1, ts, kind: "k", step: null, attempt: 1, agent: null, model: null, payload: {} });
  it("clamps real gaps to 0.2-1.5s and divides by speed", () => {
    expect(gapSeconds(at(0), at(0.01), 1)).toBeCloseTo(0.2);
    expect(gapSeconds(at(0), at(500), 1)).toBeCloseTo(1.5);
    expect(gapSeconds(at(0), at(500), 2)).toBeCloseTo(0.75);
    expect(gapSeconds(at(0), at(1), 4)).toBeCloseTo(0.25);
  });
  it("is instant when speed is 0 or there is no previous event", () => {
    expect(gapSeconds(at(0), at(9), 0)).toBe(0);
    expect(gapSeconds(undefined, at(9), 1)).toBe(0);
  });
  it("starts from a blank state", () => {
    expect(replay([]).status).toBe("idle");
    expect(initialState().nodes.coder.state).toBe("idle");
  });
});
