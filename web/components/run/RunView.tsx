"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import gsap from "gsap";
import type { Role, RunEvent, RunFile, RunIndexEntry } from "../../lib/events";
import { clip, firstSentence, fmtClock, fmtDuration, fmtTokens, shortModel } from "../../lib/format";
import { describeRecovery, markers, summarize, type NodeView } from "../../lib/runPlayer";
import { useRunPlayer } from "../../lib/useRunPlayer";

const ROLE_COLOR: Record<Role, "blue" | "coral" | "green"> = { orchestrator: "blue", coder: "coral", critic: "green" };
const ROLE_LABEL: Record<Role, string> = { orchestrator: "Orchestrator", coder: "Coder", critic: "Critic" };
const ROLE_SUB: Record<Role, string> = { orchestrator: "Plan & route", coder: "Execute code", critic: "Verify results" };

// Facts about the demo task itself (see demo/ in the repo), not run metrics.
const FILES = [
  { name: "inventory_report.py", note: "the broken script", who: "coder" },
  { name: "SPEC.md", note: "7 requirements", who: "coder" },
  { name: "sample_stock.csv", note: "crashes the script", who: "coder" },
  { name: "test_hidden.py", note: "8 tests, critic only", who: "critic" },
];

function AnimatedNumber({ value, format = (n: number) => String(Math.round(n)) }: { value: number; format?: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useRef({ v: 0 });
  useEffect(() => {
    const t = gsap.to(shown.current, { v: value, duration: 0.6, ease: "power2.out", onUpdate: () => { if (ref.current) ref.current.textContent = format(shown.current.v); } });
    return () => { t.kill(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return <span ref={ref}>{format(0)}</span>;
}

function GraphNode({ role, node, x, y }: { role: Role; node: NodeView; x: number; y: number }) {
  return (
    <div className={`gnode ${ROLE_COLOR[role]}`} data-state={node.state} style={{ left: `${x}%`, top: `${y}%` }}>
      <div className="gcard">
        <div className={`orb dot ${ROLE_COLOR[role]}`} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{ROLE_LABEL[role]}</div>
          <div className="chip mono" style={{ marginTop: 4, maxWidth: 130, overflow: "hidden", textOverflow: "ellipsis" }}>{node.model ? shortModel(node.model) : ROLE_SUB[role]}</div>
        </div>
      </div>
      <div className={`note ${node.tone}`}>{node.note || " "}</div>
    </div>
  );
}

export function RunView({ run, others }: { run: RunFile; others: RunIndexEntry[] }) {
  const events = run.events;
  const p = useRunPlayer(events, 2);
  const s = p.state;
  const [filter, setFilter] = useState<"all" | Role>("all");
  const [auto, setAuto] = useState(true);
  const [selected, setSelected] = useState<RunEvent | null>(null);
  const [tab, setTab] = useState<"summary" | "raw">("summary");
  const logRef = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);

  const t0 = events[0]?.ts ?? 0;
  const totalDuration = (events[events.length - 1]?.ts ?? t0) - t0;
  const elapsed = s.now != null ? s.now - t0 : 0;
  const ticks = useMemo(() => markers(events), [events]);
  const shown = events.slice(0, p.index);
  const lines = filter === "all" ? shown : shown.filter((e) => e.agent === filter);
  const hot = s.hot;

  useEffect(() => {
    gsap.from(root.current?.querySelectorAll(".panel") ?? [], { opacity: 0, y: 18, duration: 0.6, stagger: 0.08, ease: "power2.out" });
  }, []);
  useEffect(() => { if (auto && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [p.index, auto, filter]);

  const edge = (name: string, d: string) => (
    <g className={`edge ${hot?.edge === name ? `hot ${hot.tone}` : ""}`} key={name}><path className="base" d={d} /><path className="flow" d={d} /></g>
  );
  const statusBadge = s.status === "succeeded" ? <span className="badge ok">Completed</span> : s.status === "failed" ? <span className="badge bad">Failed</span> : <span className="badge info">Replaying…</span>;
  const title = s.task ? firstSentence(s.task) : "Loading run…";

  return (
    <div ref={root} className="mx-auto grid w-full max-w-[1480px] gap-6 px-4 pb-10 lg:grid-cols-[210px_1fr] lg:px-8">
      <aside className="rail hidden lg:block">
        <div className="eyebrow" style={{ margin: "8px 14px 12px" }}>Run</div>
        <a href="#overview" aria-current="page">Overview</a>
        <a href="#graph">Agents</a>
        <a href="#steps">Steps</a>
        <a href="#logs">Logs</a>
        <div className="eyebrow" style={{ margin: "26px 14px 10px" }}>Recordings</div>
        {others.map((o) => (
          <Link key={o.id} href={`/run/${o.id}/`} aria-current={o.id === run.run_id ? "page" : undefined} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
            <span style={{ fontSize: 13 }}>{o.fixture ? "Fixture" : "Recorded"} · {fmtDuration(o.durationS)}</span>
            <span style={{ fontSize: 11, opacity: 0.7 }}>{fmtTokens(o.tokens)} tokens · {o.succeeded ? "passed" : "failed"}</span>
          </Link>
        ))}
      </aside>

      <main style={{ minWidth: 0 }}>
        <section id="overview" className="panel" style={{ display: "grid", gap: 16 }}>
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div style={{ maxWidth: 620 }}>
              <div className="eyebrow" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span>Run · {run.run_id.replace("recorded_", "")}</span>{statusBadge}
                <span className="badge">{s.fixture ? "FIXTURE · synthetic sample data" : "Replay of a real recorded run"}</span>
              </div>
              <h1 className="font-display" style={{ fontSize: 34, fontWeight: 400, margin: "10px 0 6px", lineHeight: 1.1 }}>{title}</h1>
              <p className="muted" style={{ margin: 0, fontSize: 14 }}>{s.task}</p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                {s.category && <span className="chip">{s.category}</span>}
                {(["orchestrator", "coder", "critic"] as Role[]).map((r) => s.nodes[r].model && <span key={r} className="chip mono">{r}: {shortModel(s.nodes[r].model)}</span>)}
              </div>
            </div>
            <div className="glass flex flex-wrap" style={{ padding: "14px 6px" }}>
              <div className="stat"><b><AnimatedNumber value={Math.round(elapsed)} format={(n) => fmtDuration(n)} /></b><span>Duration</span></div>
              <div className="stat"><b><AnimatedNumber value={s.stepsPassed} />{s.stepsTotal ? <span style={{ fontSize: 16 }}> / {s.stepsTotal}</span> : null}</b><span>Steps passed</span></div>
              <div className="stat"><b><AnimatedNumber value={s.coderAttempts} /></b><span>Coder attempts</span></div>
              <div className="stat"><b><AnimatedNumber value={s.recoveries.length} /></b><span>Recoveries</span></div>
              <div className="stat"><b><AnimatedNumber value={s.tokens} format={(n) => fmtTokens(Math.round(n))} /></b><span>Tokens</span></div>
            </div>
          </div>
        </section>

        <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_340px]">
          <div style={{ display: "grid", gap: 20, minWidth: 0, alignContent: "start" }}>
            <section id="graph" className="panel glass" style={{ padding: 22 }}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-display" style={{ fontSize: 20, fontWeight: 400, margin: 0 }}>Agent flow</h2>
                <div className="flex gap-3 text-xs muted">
                  <span><span className="badge info">delegate</span></span><span><span className="badge ok">pass</span></span><span><span className="badge bad">fail</span></span><span><span className="badge warn">reroute</span></span>
                </div>
              </div>
              <div aria-live="polite" style={{ display: "grid", gap: 8, marginTop: s.recoveries.length ? 14 : 0 }}>
                {s.recoveries.map((r) => (
                  <div key={r.step} className="recovery"><span style={{ color: "var(--green)" }}>✓</span><span>{describeRecovery(r)}</span></div>
                ))}
              </div>
              <div className="graph" style={{ marginTop: 6 }}>
                <svg viewBox="0 0 1000 420" preserveAspectRatio="none" aria-hidden="true">
                  <g className="edge"><path className="base" d="M 150 210 C 270 210, 300 70, 395 70" /></g>
                  <g className="edge"><path className="base" d="M 150 210 L 395 210" /></g>
                  <g className="edge"><path className="base" d="M 150 210 C 270 210, 300 350, 395 350" /></g>
                  <g className="edge"><path className="base" d="M 605 70 C 720 70, 770 190, 845 208" /></g>
                  <g className="edge"><path className="base" d="M 605 210 L 845 210" /></g>
                  {edge("delegate", "M 500 108 L 500 172")}
                  {edge("submit", "M 500 248 L 500 312")}
                  {edge("verdict", "M 605 350 C 730 350, 770 232, 845 212")}
                  {edge("reroute", "M 600 88 C 690 120, 690 176, 600 200")}
                </svg>
                <div className="gnode" style={{ left: "12%", top: "50%", width: 150 }} data-state="idle">
                  <div className="gcard"><div style={{ fontSize: 22 }}>▤</div><div><div style={{ fontSize: 14, fontWeight: 500 }}>Task</div><div className="muted" style={{ fontSize: 11 }}>fix the script</div></div></div>
                </div>
                <GraphNode role="orchestrator" node={s.nodes.orchestrator} x={50} y={16.7} />
                <GraphNode role="coder" node={s.nodes.coder} x={50} y={50} />
                <GraphNode role="critic" node={s.nodes.critic} x={50} y={83.3} />
                <div className="gnode" style={{ left: "88%", top: "50%", width: 150 }} data-state={s.status === "succeeded" ? "pass" : s.status === "failed" ? "fail" : "idle"}>
                  <div className="gcard"><div style={{ fontSize: 22 }}>{s.status === "succeeded" ? "✓" : s.status === "failed" ? "✗" : "◌"}</div><div><div style={{ fontSize: 14, fontWeight: 500 }}>Result</div><div className="muted" style={{ fontSize: 11 }}>{s.status === "succeeded" ? "hidden suite passed" : s.status === "failed" ? "run failed" : "pending"}</div></div></div>
                </div>
              </div>
            </section>

            <section id="logs" className="panel glass" style={{ padding: 22 }}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-display" style={{ fontSize: 20, fontWeight: 400, margin: 0 }}>State log</h2>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="tabs" role="tablist" aria-label="Filter by agent">
                    {(["all", "orchestrator", "coder", "critic"] as const).map((f) => (
                      <button key={f} role="tab" className="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>{f === "all" ? "All" : ROLE_LABEL[f]}</button>
                    ))}
                  </div>
                  <label className="muted flex items-center gap-2 text-xs"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-scroll</label>
                  <button className="chip" onClick={() => navigator.clipboard?.writeText(lines.map((e) => `${fmtClock(e.ts - t0)} ${e.agent ?? "run"} ${summarize(e)}`).join("\n"))}>Copy</button>
                </div>
              </div>
              <div ref={logRef} className="logbox" style={{ marginTop: 14 }} data-lenis-prevent>
                {lines.length === 0 && <div className="muted">No events yet.</div>}
                {lines.map((e) => (
                  <div key={e.seq} className="logrow" role="button" tabIndex={0} onClick={() => { setSelected(e); setTab("summary"); }} onKeyDown={(k) => { if (k.key === "Enter") { setSelected(e); setTab("summary"); } }}>
                    <span className="a-none">{fmtClock(e.ts - t0)}</span>
                    <span className={`a-${e.agent ?? "none"}`}>{e.agent ?? "run"}</span>
                    <span style={{ overflowWrap: "anywhere", color: e.kind === "verdict" ? (e.payload.passed ? "var(--green)" : "var(--coral)") : e.kind === "reroute" ? "var(--amber)" : e.kind === "step_failed" ? "var(--coral)" : undefined }}>
                      <span className="muted">{e.kind} </span>{summarize(e)}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div style={{ display: "grid", gap: 20, alignContent: "start" }}>
            <section id="steps" className="panel glass" style={{ padding: 22 }}>
              <h2 className="font-display" style={{ fontSize: 20, fontWeight: 400, margin: "0 0 12px" }}>Plan</h2>
              {s.steps.length === 0 && <div className="muted text-sm">The orchestrator is planning…</div>}
              <div style={{ display: "grid", gap: 12 }}>
                {s.steps.map((st) => (
                  <div key={st.n} style={{ display: "grid", gridTemplateColumns: "22px 1fr", gap: 10 }}>
                    <span style={{ color: st.status === "passed" ? "var(--green)" : st.status === "failed" ? "var(--coral)" : "var(--muted)" }}>{st.status === "passed" ? "✓" : st.status === "failed" ? "✗" : "•"}</span>
                    <div>
                      <div style={{ fontSize: 14 }}>{st.n}. {st.title}</div>
                      <div className="muted" style={{ fontSize: 12, margin: "2px 0 6px" }}>{clip(st.acceptance, 110)}</div>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {st.gate && <span className="badge info">hidden-suite gate</span>}
                        {st.reviewed === false && <span className="badge">not sent to critic</span>}
                        {st.attempts.filter((a) => a.outcome === "timeout" || a.outcome === "fail" || a.outcome === "error").map((a) => (
                          <span key={a.attempt} className="badge bad">{shortModel(a.model)} {a.outcome === "timeout" ? "timed out" : a.outcome}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="panel glass" style={{ padding: 22 }}>
              <h2 className="font-display" style={{ fontSize: 20, fontWeight: 400, margin: "0 0 12px" }}>Result</h2>
              {s.verdict ? (
                <div>
                  <span className={`badge ${s.verdict.passed ? "ok" : "bad"}`}>{s.verdict.passed ? "Critic verdict: pass" : "Critic verdict: fail"}</span>
                  <p style={{ fontSize: 13, margin: "10px 0 0" }}>{s.verdict.evidence || s.verdict.reasons.join("; ")}</p>
                  <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>Judged: {s.verdict.subjectModel ?? "—"} · Critic: {shortModel(s.nodes.critic.model)}</p>
                </div>
              ) : <div className="muted text-sm">Waiting for the critic’s verdict…</div>}
              {s.error && <p style={{ color: "var(--coral)", fontSize: 13 }}>{s.error}</p>}
            </section>

            <section className="panel glass" style={{ padding: 22 }}>
              <h2 className="font-display" style={{ fontSize: 20, fontWeight: 400, margin: "0 0 12px" }}>Files</h2>
              <div style={{ display: "grid", gap: 8 }}>
                {FILES.map((f) => (
                  <div key={f.name} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 13 }}>
                    <span className="font-mono2" style={{ fontSize: 12 }}>{f.name}</span>
                    <span className="muted" style={{ fontSize: 12, textAlign: "right" }}>{f.note}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </div>

        <div className="replay glass mt-5" role="group" aria-label="Replay controls">
          <button className="iconbtn" onClick={() => (p.playing ? p.pause() : p.play())} aria-label={p.playing ? "Pause" : "Play"}>{p.playing ? "❚❚" : "▶"}</button>
          <button className="iconbtn" onClick={() => p.scrub(0)} aria-label="Restart">↺</button>
          <div className="tabs" role="tablist" aria-label="Speed">
            {[{ l: "1×", v: 1 }, { l: "2×", v: 2 }, { l: "4×", v: 4 }, { l: "Instant", v: 0 }].map((o) => (
              <button key={o.l} role="tab" className="tab" aria-selected={p.speed === o.v} onClick={() => p.setSpeed(o.v)}>{o.l}</button>
            ))}
          </div>
          <div className="scrub">
            {ticks.map((m) => <span key={m.index + m.label} className={`tick ${m.tone}`} title={m.label} style={{ left: `${(m.index / p.total) * 100}%` }} />)}
            <input type="range" min={0} max={p.total} value={p.index} onChange={(e) => p.scrub(Number(e.target.value))} aria-label="Scrub through the run" />
          </div>
          <span className="muted font-mono2" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{fmtClock(elapsed)} / {fmtClock(totalDuration)}</span>
        </div>
      </main>

      {selected && (
        <aside className="drawer" data-lenis-prevent role="dialog" aria-label="Event details">
          <div className="flex items-start justify-between gap-3">
            <div>
              <span className="badge info">{selected.kind}</span>
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>#{selected.seq} · +{fmtClock(selected.ts - t0)} · {selected.agent ?? "run"}{selected.model ? ` · ${selected.model}` : ""}</div>
            </div>
            <button className="iconbtn" onClick={() => setSelected(null)} aria-label="Close">✕</button>
          </div>
          <div className="tabs" role="tablist" style={{ margin: "16px 0" }}>
            <button role="tab" className="tab" aria-selected={tab === "summary"} onClick={() => setTab("summary")}>Summary</button>
            <button role="tab" className="tab" aria-selected={tab === "raw"} onClick={() => setTab("raw")}>Raw JSON</button>
          </div>
          {tab === "raw" ? <pre>{JSON.stringify(selected, null, 2)}</pre> : (
            <div style={{ display: "grid", gap: 12, fontSize: 14 }}>
              <div>{summarize(selected)}</div>
              {selected.kind === "verdict" && (
                <>
                  <span className={`badge ${selected.payload.passed ? "ok" : "bad"}`}>{selected.payload.passed ? "pass" : "fail"}</span>
                  <div className="muted" style={{ fontSize: 12 }}>Judged: {selected.payload.subject_model ?? selected.model} · Critic: {selected.model}</div>
                  {(selected.payload.reasons ?? []).map((r: string) => <div key={r}>• {r}</div>)}
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{(selected.payload.failing_tests ?? []).map((t: string) => <span key={t} className="badge bad">{t}</span>)}</div>
                </>
              )}
              {selected.kind === "agent_call" && <pre>{selected.payload.prompt}</pre>}
              {selected.kind === "agent_result" && <pre>{selected.payload.error ?? selected.payload.output}</pre>}
              {selected.kind === "reroute" && <pre>{selected.payload.from_model} → {selected.payload.to_model}{"\n\n"}{selected.payload.modified_subtask}</pre>}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
