import Link from "next/link";
import { HeroIntro, Reveal, SmoothScroll } from "../components/Effects";
import { fmtDuration, fmtTokens, shortModel } from "../lib/format";
import { getFeaturedRun } from "../lib/runs";
import { deriveStory } from "../lib/story";

function Orb({ color, size, style, label, sub }: { color: string; size: number; style: React.CSSProperties; label: string; sub: string }) {
  return (
    <div data-orb style={{ position: "absolute", display: "flex", alignItems: "center", gap: 14, ...style }}>
      <div className={`orb ${color} drift`} style={{ width: size, height: size }} />
      <div><div style={{ fontWeight: 500 }}>{label}</div><div className="muted" style={{ fontSize: 13 }}>{sub}</div></div>
    </div>
  );
}

export default function Landing() {
  const run = getFeaturedRun();
  const st = deriveStory(run.events);
  const reason = st.failure?.reason === "timeout" ? "timed out" : st.failure?.reason === "verdict" ? "failed review" : "errored";

  return (
    <main>
      <SmoothScroll />

      {/* 01 hero */}
      <HeroIntro>
        <section style={{ minHeight: "calc(100vh - 70px)", position: "relative", display: "grid", placeItems: "start center", padding: "34px 20px 0", textAlign: "center" }}>
          <div>
            <span data-hero className="chip" style={{ padding: "8px 16px" }}>PS4 · Autonomous Orchestration</span>
            <h1 data-hero className="font-display" style={{ fontSize: "clamp(44px, 8vw, 88px)", fontWeight: 300, lineHeight: 1.02, margin: "22px 0 18px" }}>
              Agents<br />that <span className="grad-text" style={{ fontWeight: 400 }}>fail forward.</span>
            </h1>
            <p data-hero className="muted" style={{ fontSize: 19, margin: "0 auto", maxWidth: 560, lineHeight: 1.5 }}>
              A Coder. A Critic. An Orchestrator.<br />One broken task. A real recovery loop.
            </p>
            <div data-hero style={{ display: "flex", gap: 14, justifyContent: "center", marginTop: 28, flexWrap: "wrap" }}>
              <Link className="btn btn-primary" href={`/run/${run.run_id}/`}>Watch the recovery ▶</Link>
              <a className="btn btn-ghost" href="#system">How it works</a>
            </div>
          </div>

          <div aria-hidden="true" style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: "46%", overflow: "hidden", pointerEvents: "none" }}>
            <div className="ring" style={{ left: "6%", right: "6%", top: "8%", bottom: "-40%", transform: "rotate(-4deg)" }} />
            <div className="ring" style={{ left: "18%", right: "18%", top: "22%", bottom: "-20%", transform: "rotate(3deg)" }} />
          </div>
          <div className="hidden md:block" style={{ position: "absolute", left: 0, right: 0, bottom: 90, height: 260 }}>
            <Orb color="blue" size={54} style={{ left: "26%", top: 10 }} label="Orchestrator" sub="Plans, delegates, reroutes." />
            <Orb color="green" size={56} style={{ left: "33%", top: 150 }} label="Critic" sub="Runs hidden tests." />
            <Orb color="coral" size={50} style={{ left: "66%", top: 70 }} label="Coder" sub="Executes in sandbox." />
          </div>

          <div className="eyebrow" style={{ position: "absolute", left: 28, bottom: 24, textAlign: "left", lineHeight: 2, borderLeft: "1px solid var(--line)", paddingLeft: 14 }}>
            Antigravity agent<br />Interactions API<br />Gemini models
          </div>
          <a href="#system" className="eyebrow" style={{ position: "absolute", left: "50%", bottom: 22, transform: "translateX(-50%)", textDecoration: "none", textAlign: "center" }}>
            <span className="iconbtn" style={{ margin: "0 auto 10px" }}>↓</span>Scroll
          </a>
        </section>
      </HeroIntro>

      {/* 02 system */}
      <section id="system" className="mx-auto w-full max-w-[1200px] px-6 py-24">
        <Reveal>
          <div className="eyebrow">01 — The system</div>
          <h2 className="font-display" style={{ fontSize: "clamp(36px, 5vw, 60px)", fontWeight: 300, margin: "12px 0 8px", lineHeight: 1.08 }}>
            Three agents.<br /><span className="dim-line">One recovery loop.</span>
          </h2>
          <p className="muted" style={{ maxWidth: 520, fontSize: 17 }}>A team that plans, executes, verifies with tests the coder never saw, and recovers when a step fails.</p>
        </Reveal>
        <Reveal delay={0.1}>
          <div className="mt-14 grid items-start gap-10 md:grid-cols-[1fr_auto_1fr_auto_1fr]" style={{ textAlign: "center" }}>
            {[
              { c: "blue", n: "01", t: "Orchestrator", d: "Plans, delegates, and reroutes based on results.", m: st.models.orchestrator },
              { c: "coral", n: "02", t: "Coder", d: "Executes in a sandbox and solves the step.", m: st.models.coderLadder[0] },
              { c: "green", n: "03", t: "Critic", d: "Runs hidden tests and verifies the output.", m: st.models.critic },
            ].flatMap((a, i) => [
              <div key={a.t}>
                <div className={`orb ${a.c} drift`} style={{ width: 84, height: 84, margin: "0 auto", animationDelay: `${i * 0.8}s` }} />
                <div className="eyebrow" style={{ marginTop: 18 }}>{a.n}</div>
                <div style={{ fontSize: 20, fontWeight: 500 }}>{a.t}</div>
                <p className="muted" style={{ fontSize: 14, margin: "6px auto 12px", maxWidth: 230 }}>{a.d}</p>
                <span className="chip mono">{shortModel(a.m)}{a.t === "Coder" ? " → …" : ""}</span>
              </div>,
              i < 2 ? <div key={a.t + "arrow"} className="eyebrow hidden md:block" style={{ paddingTop: 34 }}>{i === 0 ? "Delegate" : "Submit"}<div style={{ fontSize: 24, marginTop: 6 }}>→</div></div> : null,
            ])}
          </div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 34 }}>
            <span className="chip" style={{ padding: "10px 22px", letterSpacing: "0.18em", fontSize: 12, borderColor: "rgba(251,191,36,.4)", color: "var(--amber)" }}>FAIL → REROUTE → RETRY</span>
          </div>
        </Reveal>
        <Reveal delay={0.1}>
          <div className="mt-24 grid gap-8 md:grid-cols-2">
            <div>
              <div className="eyebrow">02 — The problem</div>
              <h3 className="font-display" style={{ fontSize: "clamp(30px, 4vw, 48px)", fontWeight: 300, margin: "10px 0", lineHeight: 1.1 }}>A single prompt<br /><span className="dim-line">breaks at scale.</span></h3>
            </div>
            <p className="muted" style={{ fontSize: 16, lineHeight: 1.7, alignSelf: "end" }}>
              Real tasks take many steps, cross systems, and hit errors halfway. A single-prompt wrapper does not know what to do when that happens. PS4 asks for stateful, multi-agent systems that plan, delegate, track state, and recover.
            </p>
          </div>
        </Reveal>
      </section>

      {/* 03 a real run: every figure below is derived from the recording */}
      <section id="real-run" className="mx-auto w-full max-w-[1200px] px-6 pb-24">
        <Reveal>
          <div className="eyebrow">03 — A real run</div>
          <div className="mt-4 grid gap-10 lg:grid-cols-[1.1fr_1fr]">
            <div>
              <h2 className="font-display" style={{ fontSize: "clamp(40px, 6vw, 72px)", fontWeight: 300, margin: 0, lineHeight: 1.05 }}>Watch the<br /><span className="dim-line">recovery</span> happen.</h2>
              <p className="muted" style={{ maxWidth: 440, fontSize: 17, marginTop: 18 }}>A real, recorded run, step by step. The coder’s first attempt on the hard step fails, the orchestrator reroutes, and the hidden tests pass.</p>
              <div className="glass mt-8 flex flex-wrap" style={{ padding: "16px 6px", width: "fit-content" }}>
                <div className="stat"><b>{st.steps}</b><span>Steps planned</span></div>
                <div className="stat"><b>{st.coderAttempts}</b><span>Coder attempts</span></div>
                <div className="stat"><b>{st.recoveries}</b><span>Recovery</span></div>
                <div className="stat"><b>{fmtDuration(st.durationS)}</b><span>Duration</span></div>
                <div className="stat"><b>{fmtTokens(st.tokens)}</b><span>Tokens</span></div>
              </div>
            </div>

            <div style={{ display: "grid", gap: 14 }}>
              <div className="glass" style={{ padding: 18 }}>
                <div className="eyebrow">Plan · {shortModel(st.models.orchestrator)}</div>
                <ol style={{ margin: "10px 0 0", paddingLeft: 20, fontSize: 14, lineHeight: 1.8 }}>{st.planTitles.map((t) => <li key={t}>{t}</li>)}</ol>
              </div>
              {st.failure && (
                <div className="glass" style={{ padding: 18, borderColor: "rgba(255,107,94,.35)" }}>
                  <div className="eyebrow">Coder · {shortModel(st.failure.model)}</div>
                  <div style={{ marginTop: 10 }}><span className="badge bad">Step {st.failure.step} {reason}{st.failure.afterS ? ` after ${Math.round(st.failure.afterS)}s` : ""}</span></div>
                  <p className="muted" style={{ fontSize: 13, margin: "10px 0 0" }}>{st.failure.title}. No result came back, so the orchestrator moved on.</p>
                </div>
              )}
              {st.recoveredModel && (
                <div className="glass" style={{ padding: 18, borderColor: "rgba(251,191,36,.35)" }}>
                  <div className="eyebrow">Orchestrator → Coder · {shortModel(st.recoveredModel)}</div>
                  <div style={{ marginTop: 10 }}><span className="badge warn">Rerouted one rung up the model ladder</span></div>
                  <p className="muted" style={{ fontSize: 13, margin: "10px 0 0" }}>{shortModel(st.recoveredModel)} completed the step{st.recoveredAfterS ? ` in ${Math.round(st.recoveredAfterS)}s` : ""}.</p>
                </div>
              )}
              <div className="glass" style={{ padding: 18, borderColor: "rgba(52,211,153,.35)" }}>
                <div className="eyebrow">Critic · {shortModel(st.models.critic)}</div>
                <div style={{ marginTop: 10 }}><span className="badge ok">{st.hiddenTests ? `${st.hiddenTests} hidden tests passed` : "Hidden tests passed"}</span></div>
                <p className="muted" style={{ fontSize: 13, margin: "10px 0 0" }}>{st.evidence}</p>
              </div>
            </div>
          </div>
        </Reveal>
      </section>

      {/* 04 CTA */}
      <section className="mx-auto w-full max-w-[1200px] px-6 pb-28">
        <Reveal>
          <div className="glass" style={{ padding: "44px 36px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
            <div>
              <div className="eyebrow">04 — Try it yourself</div>
              <h2 className="font-display" style={{ fontSize: "clamp(30px, 4vw, 48px)", fontWeight: 300, margin: "10px 0 6px" }}>Explore the full run.</h2>
              <p className="muted" style={{ margin: 0, maxWidth: 480 }}>Step through the recorded state log: every plan, call, tool use, verdict and reroute.</p>
            </div>
            <Link className="btn btn-primary" href="/run/">Open run replay →</Link>
          </div>
          <p className="muted" style={{ fontSize: 12, textAlign: "center", marginTop: 26 }}>Built for the Google DeepMind Hyderabad Hackathon (GDG Hyderabad × Kaggle), PS4. The public demo replays recorded real runs; nothing runs live.</p>
        </Reveal>
      </section>
    </main>
  );
}
