"use client";

/**
 * `SignUp` — cloud (Clerk) branded sign-up (Spec 34, Cluster C).
 *
 * A custom email + password sign-up with email-code verification, built on
 * Clerk's Core-3 signal hook `useSignUp()` (`@clerk/react@6`, re-exported by
 * `@clerk/nextjs`). The hook returns `{ signUp, errors, fetchStatus }`; the flow
 * drives the `SignUpFuture` resource:
 *
 *   1. start    → signUp.password({ emailAddress, password })
 *                 then signUp.verifications.sendEmailCode()
 *   2. verify   → signUp.verifications.verifyEmailCode({ code })
 *                 (status -> 'complete')
 *   3. finalize → signUp.finalize({ navigate })   (sets the active session)
 *
 * The 6-digit code uses the per-digit OTP input which auto-submits when full.
 * "Resend code" is throttled by a themed cooldown countdown. OAuth (gated OFF
 * for v1) is wired via signUp.sso(). Errors map to themed copy; `fetchStatus`
 * drives the loading state.
 *
 * Verified against the installed Core-3 types and Clerk's custom-flow docs.
 * Hook-driven branches need the user's real-browser pass; the pure logic
 * (cooldown formatting, error mapping, OAuth gate) is unit-tested separately.
 */
import { useSignUp } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ErrorAlert,
  Field,
  OAuthRow,
  OtpInput,
  PasswordInput,
  useResendCooldown,
} from "./auth-fields.cloud";
import {
  type ClerkErrorLike,
  clerkErrorToMessage,
  dedupeFieldError,
  formatCooldown,
} from "./auth-flow.cloud";
import { ArrowIcon, MailIcon } from "./auth-icons.cloud";
import { AuthLoading, isAuthSignalReady } from "./auth-ready.cloud";
import { AuthShell, authStyles as s } from "./auth-shell.cloud";

const SIGN_UP_BRAND = {
  kicker: "Typed-memory AI",
  tagline: "Build personas that remember.",
  note: "One continuous identity across voice and text — with real, typed memory.",
  compact: "Build personas that remember.",
} as const;

const VERIFY_BRAND = {
  kicker: "One last step",
  tagline: "Confirm it's you.",
  note: "We sent a 6-digit code to your inbox. Enter it to finish creating your account.",
  compact: "Enter the code we emailed you.",
} as const;

/** The two steps of the sign-up flow. */
type Step = "start" | "verify";

export function SignUp() {
  const { signUp, errors, fetchStatus } = useSignUp();
  const router = useRouter();

  const [step, setStep] = useState<Step>("start");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const cooldown = useResendCooldown();

  // Guard the post-logout reset window: `signUp` / `errors` can both be absent
  // while the Clerk client re-initialises (despite the typed non-null shape).
  // Reading `errors.fields` (or `signUp.*`) before then throws in render and —
  // without an error boundary — blanks the whole screen. Show the calm loading
  // state inside the brand shell until the signal is safe to read.
  if (!isAuthSignalReady({ resource: signUp, errors })) {
    return <AuthLoading brand={SIGN_UP_BRAND} />;
  }

  const busy = fetchStatus === "fetching";
  const fieldErrors = errors.fields;
  // Dedupe against the top banner so an error surfaced at both the global and
  // field level never renders twice (banner kept; under-field copy suppressed).
  const emailError = dedupeFieldError(
    fieldErrors.emailAddress?.message,
    formError,
  );
  const passwordError = dedupeFieldError(
    fieldErrors.password?.message,
    formError,
  );

  /** Navigate after a completed sign-up, honouring any pending session task. */
  const finishSession: Parameters<typeof signUp.finalize>[0] = {
    navigate: ({ session, decorateUrl }) => {
      if (session?.currentTask) return;
      const url = decorateUrl("/");
      if (url.startsWith("http")) window.location.href = url;
      else router.push(url);
    },
  };

  /** Step 1: create the account with email + password, then send the code. */
  const handleStart = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { error } = await signUp.password({
      emailAddress: email.trim(),
      password,
    });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    const { error: sendError } = await signUp.verifications.sendEmailCode();
    if (sendError) {
      setFormError(clerkErrorToMessage(sendError as ClerkErrorLike));
      return;
    }
    cooldown.start();
    setStep("verify");
  };

  /** Step 2: verify the 6-digit code, then finalize on completion. */
  const submitCode = async (value: string) => {
    setFormError(null);
    const { error } = await signUp.verifications.verifyEmailCode({
      code: value,
    });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    if (signUp.status === "complete") {
      await signUp.finalize(finishSession);
    } else {
      setFormError(clerkErrorToMessage(null));
    }
  };

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    await submitCode(code);
  };

  /** Resend the email code (throttled by the cooldown). */
  const resend = async () => {
    if (cooldown.isCoolingDown || busy) return;
    setFormError(null);
    const { error } = await signUp.verifications.sendEmailCode();
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    cooldown.start();
  };

  /** OAuth (only reachable when OAUTH_PROVIDERS is non-empty). */
  const handleOAuth = async (strategy: string) => {
    setFormError(null);
    type SsoStrategy = Parameters<typeof signUp.sso>[0]["strategy"];
    const { error } = await signUp.sso({
      strategy: strategy as SsoStrategy,
      redirectUrl: "/sign-up/sso-callback",
      redirectCallbackUrl: "/",
    });
    if (error) setFormError(clerkErrorToMessage(error as ClerkErrorLike));
  };

  if (step === "start") {
    return (
      <AuthShell brand={SIGN_UP_BRAND}>
        <div className={s.head}>
          <h1>Create your account</h1>
          <p>Start building personas in minutes.</p>
        </div>
        <form
          className={s.body}
          onSubmit={handleStart}
          aria-busy={busy}
          noValidate
        >
          <ErrorAlert message={formError} />
          <OAuthRow onSelect={handleOAuth} disabled={busy} />
          <Field id="su-email" label="Email" error={emailError}>
            <div className={s.control}>
              <input
                className={s.input}
                id="su-email"
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
          <Field
            id="su-pw"
            label="Password"
            hint="At least 8 characters."
            error={passwordError}
          >
            <PasswordInput
              id="su-pw"
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              placeholder="Create a password"
              invalid={Boolean(passwordError)}
              describedBy="su-pw-hint"
              disabled={busy}
            />
          </Field>
          {/* Clerk Smart CAPTCHA mounts here for the custom sign-up flow (bot
              protection). Without this element Clerk warns and silently falls
              back to Invisible CAPTCHA. */}
          <div id="clerk-captcha" />
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
                  Creating account…
                </>
              ) : (
                <>
                  Create account
                  <ArrowIcon />
                </>
              )}
            </button>
          </div>
          <p className={s.legal}>
            By creating an account you agree to the{" "}
            <a href="/legal/terms">Terms</a> and{" "}
            <a href="/legal/privacy">Privacy Policy</a>.
          </p>
        </form>
        <p className={s.foot}>
          Already have an account?{" "}
          <a className={s.link} href="/sign-in">
            Sign in
          </a>
        </p>
      </AuthShell>
    );
  }

  const cooldownLabel = formatCooldown(cooldown.remaining);

  return (
    <AuthShell brand={VERIFY_BRAND}>
      <div className={s.head}>
        <h1>Check your inbox</h1>
        <p>
          Enter the 6-digit code we sent to{" "}
          <strong className={s.resendStrong}>
            {signUp.emailAddress ?? email}
          </strong>
          .
        </p>
      </div>
      <form
        className={s.body}
        onSubmit={handleVerify}
        aria-busy={busy}
        noValidate
      >
        <ErrorAlert message={formError} />
        <OtpInput
          value={code}
          onChange={setCode}
          onComplete={submitCode}
          invalid={Boolean(fieldErrors.code)}
          disabled={busy}
        />
        <div className={s.actions}>
          <button
            className={`${s.btn} ${s.btnPrimary}`}
            type="submit"
            disabled={busy || code.length < 6}
            aria-disabled={busy || code.length < 6}
          >
            {busy ? (
              <>
                <span className={s.spinner} aria-hidden="true" />
                Verifying…
              </>
            ) : (
              <>
                Verify email
                <ArrowIcon />
              </>
            )}
          </button>
        </div>
        {cooldown.isCoolingDown ? (
          <p className={s.resend}>
            Resend code in{" "}
            <strong className={s.resendStrong}>{cooldownLabel}</strong>
          </p>
        ) : (
          <p className={s.resend}>
            <MailIcon />
            Didn&apos;t get it?{" "}
            <button
              type="button"
              className={s.link}
              onClick={resend}
              disabled={busy}
            >
              Resend code
            </button>
          </p>
        )}
      </form>
      <p className={s.foot}>
        Wrong address?{" "}
        <a className={s.link} href="/sign-up">
          Go back
        </a>
      </p>
    </AuthShell>
  );
}
