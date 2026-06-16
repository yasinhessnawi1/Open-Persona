import type { MetadataRoute } from "next";

/**
 * PWA web app manifest.
 *
 * Wires the brand PWA icons (`public/brand/icon-192.png` + `icon-512.png`,
 * generated from the vector mark) so installed/home-screen surfaces use the
 * Open Persona mark. Theme/background colours are intentionally omitted here:
 * the app is theme-aware (light/dark via next-themes) and the F1/F2 token
 * system owns colour — hard-coding a single manifest theme colour would fight
 * that. Name + description mirror the root metadata.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Open Persona",
    short_name: "Open Persona",
    description:
      "Build and run typed-memory AI personas with a tier-routed runtime.",
    start_url: "/",
    display: "standalone",
    icons: [
      {
        src: "/brand/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/brand/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
    ],
  };
}
