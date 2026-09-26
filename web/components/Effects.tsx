"use client";

import Lenis from "lenis";
import { useEffect, useRef } from "react";
import gsap from "gsap";

const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Fades and lifts its children in when they scroll into view (GSAP). No-op under reduced motion. */
export function Reveal({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced()) return;
    gsap.set(el, { opacity: 0, y: 26 });
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { gsap.to(el, { opacity: 1, y: 0, duration: 0.85, delay, ease: "power3.out" }); io.disconnect(); }
    }, { threshold: 0.12 });
    io.observe(el);
    return () => io.disconnect();
  }, [delay]);
  return <div ref={ref} className={className}>{children}</div>;
}

/** Hero entrance: staggers every [data-hero] element inside it. */
export function HeroIntro({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || reduced()) return;
    const ctx = gsap.context(() => {
      gsap.from("[data-hero]", { opacity: 0, y: 30, duration: 1, stagger: 0.12, ease: "power3.out" });
      gsap.from("[data-orb]", { opacity: 0, scale: 0.6, duration: 1.2, stagger: 0.18, ease: "back.out(1.6)", delay: 0.3 });
    }, ref);
    return () => ctx.revert();
  }, []);
  return <div ref={ref}>{children}</div>;
}

/** Lenis smooth scrolling, driven by the GSAP ticker. Landing page only. */
export function SmoothScroll() {
  useEffect(() => {
    if (reduced()) return;
    const lenis = new Lenis({ lerp: 0.1 });
    const tick = (t: number) => lenis.raf(t * 1000);
    gsap.ticker.add(tick);
    gsap.ticker.lagSmoothing(0);
    return () => { gsap.ticker.remove(tick); lenis.destroy(); };
  }, []);
  return null;
}
