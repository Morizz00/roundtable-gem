import Link from "next/link";
import { fmtDuration, fmtTokens } from "../../lib/format";
import { getRunIndex } from "../../lib/runs";

export default function RunsPage() {
  const runs = getRunIndex();
  return (
    <main className="mx-auto w-full max-w-[1100px] px-4 pb-16 pt-8 lg:px-8">
      <div className="eyebrow">Recordings</div>
      <h1 className="font-display" style={{ fontSize: 44, fontWeight: 300, margin: "8px 0 8px" }}>Real runs, replayed.</h1>
      <p className="muted" style={{ maxWidth: 560 }}>Each recording is the complete state log of a real run against the Antigravity agent. Nothing here is simulated.</p>
      <div style={{ display: "grid", gap: 16, marginTop: 28 }}>
        {runs.map((r) => (
          <Link key={r.id} href={`/run/${r.id}/`} className="glass" style={{ padding: 22, textDecoration: "none", color: "inherit", display: "flex", justifyContent: "space-between", gap: 20, flexWrap: "wrap", alignItems: "center" }}>
            <div>
              <div className="eyebrow">{r.fixture ? "Fixture (synthetic)" : "Recorded run"} · {r.id.replace("recorded_", "")}</div>
              <div style={{ marginTop: 6, fontSize: 15 }}>{r.task}</div>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className={`badge ${r.succeeded ? "ok" : "bad"}`}>{r.succeeded ? "passed" : "failed"}</span>
              <span className="chip">{fmtDuration(r.durationS)}</span>
              <span className="chip">{fmtTokens(r.tokens)} tokens</span>
              <span className="btn btn-primary">Open replay →</span>
            </div>
          </Link>
        ))}
      </div>
    </main>
  );
}
