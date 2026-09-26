export function fmtDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

export function fmtClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

export function clip(text: unknown, limit: number): string {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  return s.length > limit ? `${s.slice(0, limit - 1)}…` : s;
}

/** "gemini-3.5-flash-lite" -> "3.5 flash-lite" for tight chips; unknown ids pass through unchanged. */
export function shortModel(model: string | null | undefined): string {
  if (!model) return "—";
  return model.replace(/^gemini-/, "");
}

export function firstSentence(text: string): string {
  const m = text.match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : text).trim();
}
