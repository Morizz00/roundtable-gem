import type { NextConfig } from "next";

// The public demo replays bundled recorded runs, so it is a fully static export: no server, no backend, no API key.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
