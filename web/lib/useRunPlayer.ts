"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { RunEvent } from "./events";
import { gapSeconds, replay } from "./runPlayer";

/** Drives a recorded run: how many events are shown, play/pause, speed. State itself comes from the pure reducer. */
export function useRunPlayer(events: RunEvent[], initialSpeed = 2) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(initialSpeed);

  // Autoplay once on load; under reduced motion jump straight to the finished state.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) setIndex(events.length);
    else setPlaying(true);
  }, [events.length]);

  useEffect(() => {
    if (!playing) return;
    if (index >= events.length) { setPlaying(false); return; }
    const wait = gapSeconds(events[index - 1], events[index], speed) * 1000;
    const t = setTimeout(() => setIndex((i) => i + 1), wait);
    return () => clearTimeout(t);
  }, [playing, index, speed, events]);

  const state = useMemo(() => replay(events, index), [events, index]);
  const play = useCallback(() => { if (index >= events.length) setIndex(0); setPlaying(true); }, [index, events.length]);
  const pause = useCallback(() => setPlaying(false), []);
  const scrub = useCallback((n: number) => { setPlaying(false); setIndex(n); }, []);
  return { index, playing, speed, setSpeed, state, total: events.length, play, pause, scrub };
}
