import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Outfit } from "next/font/google";
import { Nav } from "../components/Nav";
import "./globals.css";

const outfit = Outfit({ subsets: ["latin"], variable: "--font-outfit", weight: ["300", "400", "500", "600"] });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jbm = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jbm" });

export const metadata: Metadata = {
  title: "Roundtable Gem: agents that fail forward",
  description: "A Coder, a Critic and an Orchestrator on Google's Antigravity agent. Replay a real recorded run where the first attempt fails and the system recovers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${outfit.variable} ${inter.variable} ${jbm.variable}`}>
      <body>
        <div className="stars" aria-hidden="true" />
        <div style={{ position: "relative", zIndex: 1 }}>
          <Nav />
          {children}
        </div>
      </body>
    </html>
  );
}
