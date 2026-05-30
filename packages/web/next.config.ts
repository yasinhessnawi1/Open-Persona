import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// Clerk Production Frontend API host. When the production instance is set up
// with a custom Application Domain + Allowed Subdomains (so cookies stay
// first-party on `app.<root>`), Clerk's client tries to load its JS bundle and
// reach its API via `<current-host>/__clerk/*`. Without an edge rewrite, those
// paths 404 on Vercel. We rewrite them server-side to Clerk's actual CDN.
//
// The host is read from a build-time env var so dev / preview / production each
// pick up the right CDN. Default falls back to the public path (Clerk's own
// shared CDN) so local + Preview builds without the var still work.
const CLERK_FRONTEND_API_HOST =
  process.env.NEXT_PUBLIC_CLERK_FRONTEND_API_HOST ?? "clerk.openpersona.online";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/__clerk/:path*",
        destination: `https://${CLERK_FRONTEND_API_HOST}/:path*`,
      },
    ];
  },
};

// Routes every request through next-intl's request config (src/i18n/request.ts).
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
