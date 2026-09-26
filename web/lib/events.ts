// Shape of the recorded state log (see app/state_log.py in the backend). One run = an ordered list of events.

export type Role = "orchestrator" | "coder" | "critic";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Payload = Record<string, any>;

export interface RunEvent {
  run_id: string;
  seq: number;
  ts: number; // unix seconds
  kind: string; // run_started | step_started | agent_call | agent_result | tool_called | tool_returned | verdict | reflection | reroute | step_succeeded | step_failed | run_finished
  step: number | null;
  attempt: number;
  agent: Role | null;
  model: string | null;
  payload: Payload;
}

export interface RunFile {
  run_id: string;
  events: RunEvent[];
}

export interface RunIndexEntry {
  id: string;
  task: string;
  fixture: boolean;
  durationS: number;
  succeeded: boolean;
  tokens: number;
}
