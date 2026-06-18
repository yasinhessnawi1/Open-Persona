"use client";

/**
 * `SignIn` — cloud (Clerk) branded sign-in (Spec 34, Cluster B).
 *
 * A custom email + password sign-in flow built on Clerk's Core-3 signal hook
 * `useSignIn()` (`@clerk/react@6`, re-exported by `@clerk/nextjs`). The hook
 * returns `{ signIn, errors, fetchStatus }`; the flow drives the `SignInFuture`
 * resource:
 *
 *   1. start (email)     → signIn.create({ identifier: email })
 *   2. password          → signIn.password({ password })  (status -> 'complete')
 *   3. finalize          → signIn.finalize({ navigate })   (sets the active session)
 *
 * OAuth (gated OFF for v1 via OAUTH_PROVIDERS) is wired through
 * signIn.sso({ strategy, redirectUrl, redirectCallbackUrl }). Errors are read
 * from the returned `{ error }` and the hook's `errors` projection, then mapped
 * to themed copy; `fetchStatus === 'fetching'` drives the loading state.
 *
 * Verified against the installed Core-3 types and Clerk's custom-flow docs.
 * Hook-driven branches need the user's real-browser pass; the pure logic
 * (error mapping, OAuth gate) is unit-tested separately.
 */
import { useSignIn } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ErrorAlert,
  Field,
  HiddenUsernameField,
  OAuthRow,
  PasswordInput,
} from "./auth-fields.cloud";
import {
  type ClerkErrorLike,
  clerkErrorToMessage,
  dedupeFieldError,
} from "./auth-flow.cloud";
import { ArrowIcon } from "./auth-icons.cloud";
import { AuthLoading, isAuthSignalReady } from "./auth-ready.cloud";
import { AuthShell, authStyles as s } from "./auth-shell.cloud";

const SIGN_IN_BRAND = {
  kicker: "Typed-memory AI",
  tagline: "The persona you talk to is the one you type to.",
  note: "Sign in to personas that remember you — across voice and text.",
  compact: "Sign in to personas that remember you.",
} as const;

/** The two steps of the email→password sign-in flow. */
type Step = "start" | "password";

export function SignIn() {
  const { signIn, errors, fetchStatus } = useSignIn();
  const router = useRouter();

  const [step, setStep] = useState<Step>("start");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  // Guard the post-logout reset window: the typed-non-null `signIn` / `errors`
  // can both be absent while the Clerk client re-initialises. Reading
  // `errors.fields` (or `signIn.*`) before then throws in render and — without
  // an error boundary — blanks the whole screen. Show the calm loading state
  // inside the brand shell instead until the signal is safe to read.
  if (!isAuthSignalReady({ resource: signIn, errors })) {
    return <AuthLoading brand={SIGN_IN_BRAND} />;
  }

  const busy = fetchStatus === "fetching";
  const fieldErrors = errors.fields;
  // Dedupe against the top banner so Clerk errors surfaced at both the global
  // and field level (e.g. "Couldn't find your account.") never render twice.
  const emailError = dedupeFieldError(
    fieldErrors.identifier?.message,
    formError,
  );
  const passwordError = dedupeFieldError(
    fieldErrors.password?.message,
    formError,
  );

  /** Navigate after a completed sign-in, honouring any pending session task. */
  const finishSession: Parameters<typeof signIn.finalize>[0] = {
    navigate: ({ session, decorateUrl }) => {
      if (session?.currentTask) return;
      const url = decorateUrl("/");
      if (url.startsWith("http")) window.location.href = url;
      else router.push(url);
    },
  };

  /** Step 1: bind the identifier so the password step has context + a userData chip. */
  const handleEmail = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { error } = await signIn.create({ identifier: email.trim() });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    setStep("password");
  };

  /** Step 2: submit the password, then finalize on completion. */
  const handlePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { error } = await signIn.password({ password });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    if (signIn.status === "complete") {
      await signIn.finalize(finishSession);
    } else {
      // needs_second_factor / needs_client_trust etc. are not part of the v1
      // email+password config; surface a calm prompt rather than silently stall.
      setFormError(clerkErrorToMessage(null));
    }
  };

  /** Reset to the email step so the user can correct the identifier. */
  const changeIdentifier = async () => {
    await signIn.reset();
    setPassword("");
    setFormError(null);
    setStep("start");
  };

  /** OAuth (only reachable when OAUTH_PROVIDERS is non-empty). */
  const handleOAuth = async (strategy: string) => {
    setFormError(null);
    // `strategy` originates from the controlled OAUTH_PROVIDERS list; narrow to
    // the SDK's exact sso() strategy param type at the boundary.
    type SsoStrategy = Parameters<typeof signIn.sso>[0]["strategy"];
    const { error } = await signIn.sso({
      strategy: strategy as SsoStrategy,
      redirectUrl: "/sign-in/sso-callback",
      redirectCallbackUrl: "/",
    });
    if (error) setFormError(clerkErrorToMessage(error as ClerkErrorLike));
  };

  const startForgot = () => router.push("/reset-password");

  return (
    <AuthShell brand={SIGN_IN_BRAND}>
      <div className={s.head}>
        <h1>Welcome back</h1>
        <p>
          {step === "start"
            ? "Sign in to continue to Open Persona."
            : "Enter your password to continue."}
        </p>
      </div>

      {step === "start" ? (
        <form
          className={s.body}
          onSubmit={handleEmail}
          aria-busy={busy}
          noValidate
        >
          <ErrorAlert message={formError} />
          <OAuthRow onSelect={handleOAuth} disabled={busy} />
          <Field id="si-email" label="Email" error={emailError}>
            <div className={s.control}>
              <input
                className={s.input}
                id="si-email"
                name="email"
                type="email"
                autoComplete="email"
                inputMode="email"
                placeholder="you@example.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={busy}
                required
              />
            </div>
          </Field>
          <div className={s.actions}>
            <button
              className={`${s.btn} ${s.btnPrimary}`}
              type="submit"
              disabled={busy}
              aria-disabled={busy}
            >
              {busy ? (
                <>
                  <span className={s.spinner} aria-hidden="true" />
                  Continuing…
                </>
              ) : (
                <>
                  Continue
                  <ArrowIcon />
                </>
              )}
            </button>
          </div>
        </form>
      ) : (
        <form
          className={s.body}
          onSubmit={handlePassword}
          aria-busy={busy}
          noValidate
        >
          <ErrorAlert message={formError} />
          {/* Off-screen-but-in-DOM email so this password step is a complete
              credential form: password managers can associate the saved login
              and the "password forms should have a username field" a11y warning
              clears. Bound to the identifier captured in step 1. */}
          <HiddenUsernameField value={signIn.identifier ?? email} />
          <div className={s.idchip}>
            <span className={s.who}>{signIn.identifier ?? email}</span>
            <button
              type="button"
              className={s.link}
              onClick={changeIdentifier}
              aria-disabled={busy}
              disabled={busy}
            >
              Change
            </button>
          </div>
          <Field
            id="si-pw"
            label="Password"
            error={passwordError}
            rowExtra={
              <button
                type="button"
                className={s.link}
                onClick={startForgot}
                disabled={busy}
              >
                Forgot password?
              </button>
            }
          >
            <PasswordInput
              id="si-pw"
              value={password}
              onChange={setPassword}
              autoComplete="current-password"
              placeholder="Enter your password"
              invalid={Boolean(passwordError)}
              disabled={busy}
            />
          </Field>
          <div className={s.actions}>
            <button
              className={`${s.btn} ${s.btnPrimary}`}
              type="submit"
              disabled={busy}
              aria-disabled={busy}
            >
              {busy ? (
                <>
                  <span className={s.spinner} aria-hidden="true" />
                  Signing in…
                </>
              ) : (
                <>
                  Sign in
                  <ArrowIcon />
                </>
              )}
            </button>
          </div>
        </form>
      )}

      <p className={s.foot}>
        New to Open Persona?{" "}
        <a className={s.link} href="/sign-up">
          Create an account
        </a>
      </p>
    </AuthShell>
  );
}
