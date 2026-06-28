import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  resolve: {
    alias: {
      // The `server-only` guard package isn't resolvable under vitest; stub it
      // so server modules (e.g. `@/auth/server`) can be unit-tested (Spec 33).
      "server-only": fileURLToPath(
        new URL("./src/test/server-only-stub.ts", import.meta.url),
      ),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    // Infra guard against the full-suite load-flake: under heavy parallel load
    // on a saturated machine, jsdom render tests can blow past the 5s default
    // (environment/setup spin-up dominates), causing UNIFORM 5000ms timeouts on
    // RANDOM files — not a real regression (the same files pass in isolation in
    // milliseconds). Raise the per-test/hook ceiling and cap fork parallelism so
    // jsdom environment spin-up doesn't thrash under contention.
    testTimeout: 20000,
    hookTimeout: 20000,
    // Vitest 4: pool worker counts are top-level (the old nested `poolOptions`
    // form was removed). Cap fork parallelism so jsdom env spin-up doesn't
    // thrash under contention.
    pool: "forks",
    maxWorkers: 4,
  },
});
