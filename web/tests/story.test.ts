import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { RunFile, RunIndexEntry } from "../lib/events";
import { deriveStory } from "../lib/story";

const RUNS = path.join(__dirname, "..", "public", "runs");
const index: RunIndexEntry[] = JSON.parse(fs.readFileSync(path.join(RUNS, "index.json"), "utf-8"));

describe("the landing story is derived from the recording, not typed in", () => {
  for (const entry of index.filter((r) => !r.fixture)) {
    it(`${entry.id}: every figure matches the events`, () => {
      const run: RunFile = JSON.parse(fs.readFileSync(path.join(RUNS, `${entry.id}.json`), "utf-8"));
      const story = deriveStory(run.events);
      const finished = run.events.find((e) => e.kind === "run_finished")!.payload;

      expect(story.succeeded).toBe(true);
      expect(story.tokens).toBe(finished.tokens);
      expect(story.coderAttempts).toBe(finished.attempts_used);
      expect(story.steps).toBe(finished.steps_total);
      expect(story.durationS).toBeCloseTo(entry.durationS, 1);
      expect(story.recoveries).toBe(1);
      expect(story.failure).toMatchObject({ step: 2, model: "gemini-3.5-flash-lite", reason: "timeout" });
      expect(story.failure!.afterS).toBeGreaterThanOrEqual(300); // the timeout that was recorded, not a made-up number
      expect(story.recoveredModel).toBe("gemini-3.5-flash");
      expect(story.recoveredAfterS).toBeGreaterThan(0);
      expect(story.hiddenTests).toBe(8); // parsed from the critic's own evidence line
      expect(story.planTitles).toHaveLength(3);
      expect(story.models.coderLadder[0]).toBe("gemini-3.5-flash-lite");
    });
  }
});
