"use client";

/**
 * `ResetPassword` — cloud (Clerk) branded forgot/reset-password flow
 * (Spec 34, Cluster D).
 *
 * These screens are in the spec's acceptance criteria but not in the prototype
 * frames, so they reuse the same branded shell + controls to stay visually
 * consistent. Built on Clerk's Core-3 signal hook `useSignIn()`; drives the
 * `SignInFuture` reset flow:
 *
 *   1. request → signIn.create({ identifier: email })
 *               then signIn.resetPasswordEmailCode.sendCode()
 *   2. code    → signIn.resetPasswordEmailCode.verifyCode({ code })
 *               (status -> 'needs_new_password')
 *   3. set pw  → signIn.resetPasswordEmailCode.submitPassword({ password })
 *               (status -> 'complete'), then signIn.finalize({ navigate })
 *
 * Lockout / rate-limit errors are surfaced with calmer themed copy (D-34-3 AC):
 * `clerkErrorToMessage` maps `too_many_requests` / `*_locked` codes to a
 * security-minded message rather than the raw provider string.
 *
 * Verified against the installed Core-3 types and Clerk's custom-flow docs.
 * Hook-driven branches need the user's real-browser pass.
 */
import { useSignIn } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ErrorAlert,
  Field,
  HiddenUsernameField,
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
import { ArrowIcon } from "./auth-icons.cloud";
import { AuthLoading, isAuthSignalReady } from "./auth-ready.cloud";
import { AuthShell, authStyles as s } from "./auth-shell.cloud";

const RESET_BRAND = {
  kicker: "Account recovery",
  tagline: "Let's get you back in.",
  note: "We'll email a 6-digit code to confirm it's you, then you can set a new password.",
  compact: "Reset your password.",
} as const;

/** The three steps of the reset flow. */
type Step = "request" | "code" | "newPassword";

export function ResetPassword() {
  const { signIn, errors, fetchStatus } = useSignIn();
  const router = useRouter();

  const [step, setStep] = useState<Step>("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const cooldown = useResendCooldown();

  // Guard the post-logout reset window: `signIn` / `errors` can both be absent
  // while the Clerk client re-initialises (despite the typed non-null shape).
  // Reading `errors.fields` (or `signIn.*`) before then throws in render and —
  // without an error boundary — blanks the whole screen. Show the calm loading
  // state inside the brand shell until the signal is safe to read.
  if (!isAuthSignalReady({ resource: signIn, errors })) {
    return <AuthLoading brand={RESET_BRAND} />;
  }

  const busy = fetchStatus === "fetching";
  const fieldErrors = errors.fields;
  // Dedupe against the top banner so an error surfaced at both the global and
  // field level never renders twice (banner kept; under-field copy suppressed).
  const emailError = dedupeFieldError(
    fieldErrors.identifier?.message,
    formError,
  );
  const passwordError = dedupeFieldError(
    fieldErrors.password?.message,
    formError,
  );

  const finishSession: Parameters<typeof signIn.finalize>[0] = {
    navigate: ({ session, decorateUrl }) => {
      if (session?.currentTask) return;
      const url = decorateUrl("/");
      if (url.startsWith("http")) window.location.href = url;
      else router.push(url);
    },
  };

  /** Step 1: bind the identifier, then send the reset code. */
  const handleRequest = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { error: createError } = await signIn.create({
      identifier: email.trim(),
    });
    if (createError) {
      setFormError(clerkErrorToMessage(createError as ClerkErrorLike));
      return;
    }
    const { error: sendError } = await signIn.resetPasswordEmailCode.sendCode();
    if (sendError) {
      setFormError(clerkErrorToMessage(sendError as ClerkErrorLike));
      return;
    }
    cooldown.start();
    setStep("code");
  };

  /** Step 2: verify the reset code (moves status to needs_new_password). */
  const submitCode = async (value: string) => {
    setFormError(null);
    const { error } = await signIn.resetPasswordEmailCode.verifyCode({
      code: value,
    });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    if (signIn.status === "needs_new_password") {
      setStep("newPassword");
    }
  };

  const handleCode = async (event: React.FormEvent) => {
    event.preventDefault();
    await submitCode(code);
  };

  /** Step 3: submit the new password, then finalize on completion. */
  const handleNewPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const { error } = await signIn.resetPasswordEmailCode.submitPassword({
      password,
    });
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    if (signIn.status === "complete") {
      await signIn.finalize(finishSession);
    } else {
      setFormError(clerkErrorToMessage(null));
    }
  };

  /** Resend the reset code (throttled by the cooldown). */
  const resend = async () => {
    if (cooldown.isCoolingDown || busy) return;
    setFormError(null);
    const { error } = await signIn.resetPasswordEmailCode.sendCode();
    if (error) {
      setFormError(clerkErrorToMessage(error as ClerkErrorLike));
      return;
    }
    cooldown.start();
  };

  const backToSignIn = () => router.push("/sign-in");
  const cooldownLabel = formatCooldown(cooldown.remaining);

  return (
    <AuthShell brand={RESET_BRAND}>
      {step === "request" ? (
        <>
          <div className={s.head}>
            <h1>Forgot your password?</h1>
            <p>Enter your email and we'll send a reset code.</p>
          </div>
          <form
            className={s.body}
            onSubmit={handleRequest}
            aria-busy={busy}
            noValidate
          >
            <ErrorAlert message={formError} />
            <Field id="rp-email" label="Email" error={emailError}>
              <div className={s.control}>
                <input
                  className={s.input}
                  id="rp-email"
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
                    Sending code…
                  </>
                ) : (
                  <>
                    Send reset code
                    <ArrowIcon />
                  </>
                )}
              </button>
            </div>
          </form>
        </>
      ) : null}

      {step === "code" ? (
        <>
          <div className={s.head}>
            <h1>Check your inbox</h1>
            <p>
              Enter the 6-digit code we sent to{" "}
              <strong className={s.resendStrong}>
                {signIn.identifier ?? email}
              </strong>
              .
            </p>
          </div>
          <form
            className={s.body}
            onSubmit={handleCode}
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
                    Verify code
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
        </>
      ) : null}

      {step === "newPassword" ? (
        <>
          <div className={s.head}>
            <h1>Set a new password</h1>
            <p>Choose a strong password you haven't used before.</p>
          </div>
          <form
            className={s.body}
            onSubmit={handleNewPassword}
            aria-busy={busy}
            noValidate
          >
            <ErrorAlert message={formError} />
            {/* Off-screen-but-in-DOM email so this new-password step is a
                complete credential form (password-manager association + clears
                the "password forms should have a username field" warning). */}
            <HiddenUsernameField value={signIn.identifier ?? email} />
            <Field
              id="rp-pw"
              label="New password"
              hint="At least 8 characters."
              error={passwordError}
            >
              <PasswordInput
                id="rp-pw"
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
                placeholder="Create a new password"
                invalid={Boolean(passwordError)}
                describedBy="rp-pw-hint"
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
                    Updating…
                  </>
                ) : (
                  <>
                    Set new password
                    <ArrowIcon />
                  </>
                )}
              </button>
            </div>
          </form>
        </>
      ) : null}

      <p className={s.foot}>
        Remembered it?{" "}
        <button type="button" className={s.link} onClick={backToSignIn}>
          Back to sign in
        </button>
      </p>
    </AuthShell>
  );
}
