/**
 * `@/auth/provider` — cloud (Clerk) app provider (Spec 33).
 *
 * Wraps the app tree in `<ClerkProvider>` with the shadcn theme. The Clerk UI
 * theme CSS is imported HERE (not in globals.css) so the community CSS graph
 * never references `@clerk/ui` — part of keeping the community bundle Clerk-free.
 */
import { ClerkProvider } from "@clerk/nextjs";
import { shadcn } from "@clerk/ui/themes";
import type { ReactNode } from "react";
import "@clerk/ui/themes/shadcn.css";

export function AuthProvider({ children }: { children: ReactNode }) {
  // `afterSignOutUrl` pins the post-logout landing to the branded `/sign-in`
  // (Clerk's default is `/`). The branded sign-in now renders a calm loading
  // state through the client-reset window and is wrapped by the `(auth)` error
  // boundary, so logout always lands on a screen that renders cleanly — never a
  // black screen.
  return (
    <ClerkProvider appearance={{ theme: shadcn }} afterSignOutUrl="/sign-in">
      {children}
    </ClerkProvider>
  );
}
