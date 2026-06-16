import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Next 16 renamed `middleware.ts` → `proxy.ts`. Clerk's clerkMiddleware is the
// default export. Protected routes are the authenticated (app) group; the
// auth pages stay public. The root `/` is intentionally NOT force-protected:
// it is auth-aware and handles the signed-out case itself (redirect to the
// marketing site), so the middleware leaves it alone.
const isProtected = createRouteMatcher([
  "/personas(.*)",
  "/chat(.*)",
  "/runs(.*)",
  "/conversations(.*)",
  "/settings(.*)",
]);

export default clerkMiddleware(async (auth, req) => {
  if (isProtected(req)) {
    await auth.protect(); // unauthenticated → redirect to sign-in
  }
});

export const config = {
  matcher: [
    // Skip Next internals + static files (unless referenced in a query string).
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes + Clerk's auto-proxy path.
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
