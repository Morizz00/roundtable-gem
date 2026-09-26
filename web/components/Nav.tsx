"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrandMark } from "./BrandMark";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/run/", label: "Run" },
];

export function Nav() {
  const path = usePathname() ?? "/";
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, "")));
  return (
    <header className="nav">
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 12, textDecoration: "none", color: "inherit" }}>
        <BrandMark />
        <span className="font-display" style={{ fontSize: 19 }}>Roundtable Gem</span>
      </Link>
      <nav style={{ display: "flex", gap: 34 }} aria-label="Primary">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className="link" aria-current={active(l.href) ? "page" : undefined}>{l.label}</Link>
        ))}
      </nav>
      <div className="hide-sm" style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <span className="divider" />
        <span style={{ fontSize: 14 }}>GDG Hyderabad × Kaggle · PS4</span>
      </div>
    </header>
  );
}
