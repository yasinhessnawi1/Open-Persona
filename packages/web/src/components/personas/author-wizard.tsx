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
import {
  docToYaml,
  emptyPersonaDoc,
  type PersonaDoc,
  yamlToDoc,
} from "@/lib/persona-draft";
import type { PersonaExample } from "@/lib/persona-examples";
import { ensureSafetyConstraint } from "@/lib/persona-safety";
import { validatePersonaDoc } from "@/lib/persona-schema";
import { cn } from "@/lib/utils";
import { ExampleGallery } from "./example-gallery";
import { PersonaEditor } from "./persona-editor";
import type { McpCatalogEntry } from "./persona-form";

type Phase = "describe" | "loading" | "creating" | "review";

/**
 * Server-side cap (D-10-5). UI hides the questions after this; server backstops.
 * Preserved verbatim per audit.md §authoring.plumbing.
 */
const MAX_REFINE_ROUNDS = 3;

/**
 * AuthorWizard — the new-persona flow (Spec 36 + Spec F2 T29 presentation).
 *
 * THREE create paths, all converging on the shared `PersonaEditor` + a single
 * direct-create assembly:
 *   1. PREBUILT STARTER (primary, Spec 36) — pick a flagship starter → its
 *      structured draft opens in the editor → Create posts it DIRECTLY to
 *      `POST /v1/personas` with NO `/author` LLM call (instant).
 *   2. START FROM SCRATCH — an empty structured draft opens in the editor.
 *   3. DESCRIBE YOUR OWN (drafter) — free text → `useAuthor()` two-endpoint
 *      flow (D-09-11 / D-10-2) → review with the clarifying-questions seam.
 *
 * Every path's Create runs through `handleCreate`: re-assert the safety
 * constraint (D-36-safety-ux), validate against the v1 schema client-side
 * (D-36-validation), then `createPersona`. The server (`_guard_safety`) remains
 * the authoritative safety floor.
 *
 * DO NOT TOUCH (per audit.md §authoring.plumbing):
 *   - `useAuthor()` hook, `MAX_REFINE_ROUNDS`, `createPersona`, the
 *     `applyDraft` / `generate` / `answerQuestion` drafter state machine,
 *     `ApiError.isRateLimited` handling, and `<PersonaEditor>` composition.
 */
export function AuthorWizard({
  tools,
  skills,
  mcpServers = [],
}: {
  tools: string[];
  skills: string[];
  mcpServers?: McpCatalogEntry[];
}) {
  const t = useTranslations("author");
  const { author, refine } = useAuthor();
  const [description, setDescription] = useState("");
  const [phase, setPhase] = useState<Phase>("describe");
  // `draft` is the DRAFTER output (carries clarifying questions); it is null for
  // the prebuilt-starter and start-from-scratch paths, which need no refinement.
  const [draft, setDraft] = useState<AuthoringDraft | null>(null);
  const [doc, setDoc] = useState<PersonaDoc | null>(null);
  const [round, setRound] = useState(0);
  const [refining, setRefining] = useState(false);
  const [editorKey, setEditorKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Open the editor on a structured draft directly (no drafter call). The safety
  // constraint is re-asserted up front so the editor shows it pinned from the
  // start (starters already carry it; scratch does not until now).
  function openDirect(seedDoc: PersonaDoc) {
    setDraft(null);
    setDoc(ensureSafetyConstraint(seedDoc));
    setError(null);
    setEditorKey((k) => k + 1);
    setPhase("review");
  }

  function openStarter(example: PersonaExample) {
    openDirect(example.structure as unknown as PersonaDoc);
  }

  function openScratch() {
    openDirect(emptyPersonaDoc());
  }

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

  // The single direct-create assembly, shared by all three paths (Spec 36 T2):
  //   re-assert safety → validate against the v1 schema → POST.
  // Validation runs BEFORE the phase flips to "creating" so a failure leaves the
  // editor mounted (edits preserved) and surfaces inline via the editor's save
  // error. On success `createPersona` redirects; it returns only on a server
  // error, which drops back to the editor.
  async function handleCreate(
    yaml: string,
    _avatarUrl?: string | null,
  ): Promise<{ error: string } | undefined> {
    let parsed: PersonaDoc;
    try {
      parsed = yamlToDoc(yaml);
    } catch {
      return { error: t("authorError") };
    }
    const guarded = ensureSafetyConstraint(parsed);
    const validation = validatePersonaDoc(guarded);
    if (!validation.ok) {
      const fields = validation.issues.map((i) => i.path).join(", ");
      return { error: t("createValidationFailed", { fields }) };
    }
    setPhase("creating");
    setError(null);
    const result = await createPersona(docToYaml(guarded));
    if (result?.error) {
      setError(result.error);
      setPhase("review");
    }
    return result;
  }

  if (phase === "review" && doc) {
    // Direct-create (starter / scratch) has no drafter draft → no refinement
    // seam and a "make it yours" framing instead of "review the draft".
    const direct = draft === null;
    return (
      <Stack gap={6} data-slot="author-wizard-review">
        <header>
          <p className="type-caption font-mono text-muted-foreground uppercase">
            {direct ? t("directByline") : t("reviewByline")}
          </p>
          <h1 className="type-heading mt-1" data-slot="author-wizard-title">
            {direct ? t("directTitle") : t("reviewTitle")}
          </h1>
          {direct ? (
            <p className="type-body mt-2 max-w-prose text-muted-foreground">
              {t("directSubtitle")}
            </p>
          ) : null}
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
          mcpServers={mcpServers}
          onSave={handleCreate}
          saveLabel={t("save")}
          refinement={
            draft
              ? {
                  questions: draft.questions ?? [],
                  round,
                  maxRounds: MAX_REFINE_ROUNDS,
                  refining,
                  onAnswer: (q, a, yaml) => void answerQuestion(q, a, yaml),
                }
              : undefined
          }
        />
      </Stack>
    );
  }

  if (phase === "loading") {
    return (
      <WizardLoading
        title={t("loadingTitle")}
        steps={[t("loadingStep1"), t("loadingStep2"), t("loadingStep3")]}
      />
    );
  }

  if (phase === "creating") {
    // The structure already exists; now it's persisting + the avatar/voice
    // enrich asynchronously in the background (no taking-shape skeleton).
    return (
      <WizardLoading
        title={t("creatingTitle")}
        steps={[t("creatingStep1"), t("creatingStep2"), t("creatingStep3")]}
        skeleton={false}
      />
    );
  }

  return (
    <Stack gap={8} data-slot="author-wizard-describe">
      <header>
        <p className="type-caption font-mono text-muted-foreground uppercase">
          {t("describeByline")}
        </p>
        <h1 className="type-display mt-1" data-slot="author-wizard-title">
          {t("gallery.title")}
        </h1>
        <p className="type-body mt-2 max-w-prose text-muted-foreground">
          {t("gallery.subtitle")}
        </p>
      </header>

      {/* PRIMARY (top): describe your own → the LLM drafter, or start from
          scratch with an empty editable draft. */}
      <Stack gap={4} data-slot="author-wizard-own">
        <p className="type-body text-muted-foreground">{t("describeHint")}</p>

        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={5}
          placeholder={t("describePlaceholder")}
          className="resize-none"
          data-slot="author-wizard-description"
        />

        {error ? (
          <p
            className="type-ui text-destructive"
            role="alert"
            data-slot="author-wizard-error"
          >
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={openScratch}
            className={cn(buttonVariants({ variant: "outline" }), "gap-2")}
            data-slot="author-wizard-scratch"
          >
            <Sparkles className="size-4" aria-hidden="true" />
            {t("gallery.startScratch")}
          </button>
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

      {/* SECONDARY (below): or pick a ready-made starter → edit → create
          directly (no drafter call). */}
      <div className="flex items-center gap-3" aria-hidden="true">
        <span className="h-px flex-1 bg-border" />
        <span className="type-caption font-mono text-muted-foreground uppercase">
          {t("gallery.ownPathLabel")}
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <ExampleGallery onSelect={openStarter} />
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
function WizardLoading({
  title,
  steps,
  skeleton = true,
}: {
  title: string;
  steps: string[];
  skeleton?: boolean;
}) {
  const [i, setI] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % steps.length), 2800);
    return () => clearInterval(id);
  }, [steps.length]);

  return (
    <Stack gap={6} data-slot="author-wizard-loading">
      <header className="flex items-center gap-3">
        <Wand2
          className="size-5 animate-pulse text-primary"
          aria-hidden="true"
        />
        <div>
          <h1 className="type-heading" data-slot="author-wizard-loading-title">
            {title}
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
      {skeleton ? (
        <Stack gap={4}>
          {[0, 1, 2].map((row) => (
            <Card key={row} className="gap-3 p-5">
              <SkeletonLine className="w-24" />
              <SkeletonLine className="w-3/4" />
              <SkeletonLine className="w-1/2" />
            </Card>
          ))}
        </Stack>
      ) : null}
    </Stack>
  );
}
