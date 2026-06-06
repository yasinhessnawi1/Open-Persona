"use client";

import { Sparkles, Wand2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Stack } from "@/components/layout";
import { SkeletonLine } from "@/components/patterns/loading";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import type { AuthoringDraft } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { useAuthor } from "@/lib/hooks/use-author";
import { createPersona } from "@/lib/persona-actions";
import { type PersonaDoc, yamlToDoc } from "@/lib/persona-draft";
import { cn } from "@/lib/utils";
import { PersonaEditor } from "./persona-editor";

type Phase = "describe" | "loading" | "review";

/**
 * Server-side cap (D-10-5). UI hides the questions after this; server backstops.
 * Preserved verbatim per audit.md §authoring.plumbing.
 */
const MAX_REFINE_ROUNDS = 3;

/**
 * Spec F2 T29 — AuthorWizard (rebuilt presentation).
 *
 * DO NOT TOUCH (per audit.md §authoring.plumbing):
 *   - `useAuthor()` hook (D-09-11 seam, two-endpoint flow D-10-2);
 *   - `MAX_REFINE_ROUNDS = 3` (UI cap mirrors server backstop D-10-5);
 *   - `createPersona` action;
 *   - `applyDraft` / `generate` / `answerQuestion` state machine;
 *   - `ApiError.isRateLimited` check + `tRateLimited` copy (D-11-14);
 *   - `<PersonaEditor>` (form ⇄ Monaco sync D-09-9 + Monaco lazy-load D-09-8) —
 *     composed verbatim; T29 does not rewrite its internals.
 *
 * REPLACED (presentation only):
 *   - byline `font-mono text-xs tracking-wide uppercase` → `.type-caption font-mono uppercase`
 *     (resolves through F1's `--text-caption-*` tokens);
 *   - heading `font-heading text-2xl/3xl font-semibold tracking-tight` →
 *     `.type-heading` / `.type-display` (Fraunces lives in the token now);
 *   - body `text-sm text-muted-foreground` → `.type-body` / `.type-ui`;
 *   - shadcn `<Skeleton>` in `<AuthorLoading>` → T21 `<SkeletonLine>`
 *     (token-resolved animation-duration via F1 `--motion-duration-*`);
 *   - hand-rolled outer `<div className="flex flex-col gap-6">` → T20 `<Stack>`;
 *   - inline error `text-sm text-destructive` → `.type-ui text-destructive` with
 *     `role="alert"` for the assertive announcement; the full T22 `<ErrorState>`
 *     is reserved for surface-level error panels, not single-line form errors.
 */
export function AuthorWizard({
  tools,
  skills,
}: {
  tools: string[];
  skills: string[];
}) {
  const t = useTranslations("author");
  const { author, refine } = useAuthor();
  const [description, setDescription] = useState("");
  const [phase, setPhase] = useState<Phase>("describe");
  const [draft, setDraft] = useState<AuthoringDraft | null>(null);
  const [doc, setDoc] = useState<PersonaDoc | null>(null);
  const [round, setRound] = useState(0);
  const [refining, setRefining] = useState(false);
  const [editorKey, setEditorKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  function applyDraft(next: AuthoringDraft): boolean {
    try {
      setDoc(yamlToDoc(next.yaml));
      setDraft(next);
      return true;
    } catch {
      setError(t("authorError"));
      return false;
    }
  }

  async function generate() {
    const desc = description.trim();
    if (!desc) return;
    setPhase("loading");
    setError(null);
    try {
      const result = await author(desc);
      setRound(0);
      setEditorKey((k) => k + 1);
      if (applyDraft(result)) setPhase("review");
      else setPhase("describe");
    } catch (e) {
      setError(
        e instanceof ApiError && e.isRateLimited
          ? t("rateLimited")
          : t("authorError"),
      );
      setPhase("describe");
    }
  }

  async function answerQuestion(
    question: string,
    answer: string,
    currentYaml: string,
  ) {
    if (refining || round >= MAX_REFINE_ROUNDS) return;
    setRefining(true);
    setError(null);
    try {
      const next = await refine({ currentYaml, question, answer, round });
      if (applyDraft(next)) {
        setRound((r) => r + 1);
        setEditorKey((k) => k + 1);
      }
    } catch (e) {
      setError(
        e instanceof ApiError && e.isRateLimited
          ? t("rateLimited")
          : t("authorError"),
      );
    } finally {
      setRefining(false);
    }
  }

  if (phase === "review" && draft && doc) {
    return (
      <Stack gap={6} data-slot="author-wizard-review">
        <header>
          <p className="type-caption font-mono text-muted-foreground uppercase">
            {t("reviewByline")}
          </p>
          <h1 className="type-heading mt-1" data-slot="author-wizard-title">
            {t("reviewTitle")}
          </h1>
        </header>
        {error ? (
          <p className="type-ui text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        <PersonaEditor
          key={editorKey}
          initialDoc={doc}
          tools={tools}
          skills={skills}
          onSave={createPersona}
          saveLabel={t("save")}
          refinement={{
            questions: draft.questions ?? [],
            round,
            maxRounds: MAX_REFINE_ROUNDS,
            refining,
            onAnswer: (q, a, yaml) => void answerQuestion(q, a, yaml),
          }}
        />
      </Stack>
    );
  }

  if (phase === "loading") {
    return <AuthorLoading />;
  }

  const examples = [t("example1"), t("example2"), t("example3")];

  return (
    <Stack gap={6} data-slot="author-wizard-describe">
      <header>
        <p className="type-caption font-mono text-muted-foreground uppercase">
          {t("describeByline")}
        </p>
        <h1 className="type-display mt-1" data-slot="author-wizard-title">
          {t("describeTitle")}
        </h1>
        <p className="type-body mt-2 text-muted-foreground">
          {t("describeHint")}
        </p>
      </header>

      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={5}
        placeholder={t("describePlaceholder")}
        className="resize-none"
        data-slot="author-wizard-description"
      />

      <Stack gap={2}>
        <span className="type-ui font-medium text-muted-foreground">
          {t("examplesTitle")}
        </span>
        <Stack gap={2}>
          {examples.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setDescription(ex)}
              className="type-body flex items-start gap-2 rounded-md border px-3 py-2 text-left text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
              data-slot="author-wizard-example"
            >
              <Sparkles
                className="mt-0.5 size-3.5 shrink-0 text-primary"
                aria-hidden="true"
              />
              {ex}
            </button>
          ))}
        </Stack>
      </Stack>

      {error ? (
        <p
          className="type-ui text-destructive"
          role="alert"
          data-slot="author-wizard-error"
        >
          {error}
        </p>
      ) : null}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => void generate()}
          disabled={!description.trim()}
          className={cn(buttonVariants(), "gap-2")}
          data-slot="author-wizard-generate"
        >
          <Wand2 className="size-4" aria-hidden="true" />
          {t("generate")}
        </button>
      </div>
    </Stack>
  );
}

/**
 * Designed 10–30s loading state (spec §8 risk): the frontier call is slow, so
 * the surface reads as deliberate work — cycling status + persona-taking-shape
 * skeleton — not a blank spinner.
 *
 * Preserved behaviour (per audit.md §authoring.plumbing): the 3-step cycling
 * `setInterval` at 2800ms. T29 rebuild swaps shadcn `<Skeleton>` for T21
 * `<SkeletonLine>` (motion resolves through F1 `--motion-duration-*` tokens),
 * retokenises typography, and wraps in T20 `<Stack>`.
 */
function AuthorLoading() {
  const t = useTranslations("author");
  const steps = [t("loadingStep1"), t("loadingStep2"), t("loadingStep3")];
  const [i, setI] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % 3), 2800);
    return () => clearInterval(id);
  }, []);

  return (
    <Stack gap={6} data-slot="author-wizard-loading">
      <header className="flex items-center gap-3">
        <Wand2
          className="size-5 animate-pulse text-primary"
          aria-hidden="true"
        />
        <div>
          <h1 className="type-heading" data-slot="author-wizard-loading-title">
            {t("loadingTitle")}
          </h1>
          <p
            className="type-ui mt-1 text-muted-foreground"
            data-slot="author-wizard-loading-step"
            aria-live="polite"
          >
            {steps[i]}
          </p>
        </div>
      </header>
      <Stack gap={4}>
        {[0, 1, 2].map((row) => (
          <Card key={row} className="gap-3 p-5">
            <SkeletonLine className="w-24" />
            <SkeletonLine className="w-3/4" />
            <SkeletonLine className="w-1/2" />
          </Card>
        ))}
      </Stack>
    </Stack>
  );
}
