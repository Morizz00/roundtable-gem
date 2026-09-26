// Build-time loaders for the bundled recordings (static export: read once at build, ship as props).
import fs from "node:fs";
import path from "node:path";
import type { RunFile, RunIndexEntry } from "./events";

const RUNS_DIR = path.join(process.cwd(), "public", "runs");

export function getRunIndex(): RunIndexEntry[] {
  return JSON.parse(fs.readFileSync(path.join(RUNS_DIR, "index.json"), "utf-8")) as RunIndexEntry[];
}

export function getRun(id: string): RunFile {
  return JSON.parse(fs.readFileSync(path.join(RUNS_DIR, `${id}.json`), "utf-8")) as RunFile;
}

/** The recording the landing page tells its story with: the first real (non-fixture) run in the index. */
export function getFeaturedRun(): RunFile {
  const entry = getRunIndex().find((r) => !r.fixture) ?? getRunIndex()[0];
  return getRun(entry.id);
}
