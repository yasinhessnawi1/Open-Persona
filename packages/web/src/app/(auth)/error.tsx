"use client";

/**
 * Error boundary for the branded auth route group (`/sign-in`, `/sign-up`,
 * `/reset-password`).
 *
 * Defense-in-depth for the post-logout black screen: even with the readiness
 * guards in the flow components, ANY render throw under `(auth)` is caught here
 * and shown as a calm branded fallback instead of unmounting the tree to a
 * blank screen. `reset()` re-renders the segment (the Clerk client has usually
 * finished re-initialising by the time the user clicks), and a hard link back
 * to `/sign-in` is the always-available escape hatch.
 *
 * No `@clerk/*` import — this stays edition-agnostic and the community build's
 * Clerk-free guarantee is unaffected.
 */
import { useEffect } from "react";
import styles from "./error.module.css";

export default function AuthError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface the boundary trip in the console for diagnosis (the digest links
    // a client error to its server-side log entry in production).
    console.error("Auth route error boundary:", error);
  }, [error]);

  return (
    <div className={styles.wrap} role="alert">
      <div className={styles.card}>
        <h1 className={styles.title}>Something went wrong</h1>
        <p className={styles.body}>
          We hit a snag loading the sign-in screen. This usually clears on its
          own — try again, or head back to sign in.
        </p>
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={reset}>
            Try again
          </button>
          <a className={styles.secondary} href="/sign-in">
            Back to sign in
          </a>
        </div>
      </div>
    </div>
  );
}
