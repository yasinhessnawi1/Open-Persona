/**
 * `UserButton` — cloud (Clerk) account button (Spec 33).
 *
 * The post-logout landing URL is pinned to the branded `/sign-in` on the
 * `ClerkProvider` (`afterSignOutUrl`, see `provider.cloud.tsx`) rather than per
 * button, so every sign-out — here or anywhere — lands on a screen that now
 * renders cleanly through the client-reset window.
 */
import { UserButton as ClerkUserButton } from "@clerk/nextjs";

export function UserButton() {
  return <ClerkUserButton />;
}
