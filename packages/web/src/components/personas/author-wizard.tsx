"use client";

import { Sparkles, Wand2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { AuthoringDraft } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { useAuthor } from "@/lib/hooks/use-author";
import { createPersona } from "@/lib/persona-actions";
import { type PersonaDoc, yamlToDoc } from "@/lib/persona-draft";
import { cn } from "@/lib/utils";
import { PersonaEditor } from "./persona-editor";

type Phase = "describe" | "loading" | "review";

// Matches the server-side cap (D-10-5); the UI hides the questions after this,
// the server (POST /author/refine) backstops it.
const MAX_REFINE_ROUNDS = 3;

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
  // bumped on each refine so PersonaEditor re-mounts with the regenerated draft
  const [editorKey, setEditorKey] = useState(0);
  const [error, setError] = useState<string | null>(null);

  function applyDraft(next: AuthoringDraft): boolean {
    try {
      setDoc(yamlToDoc(next.yaml));
      setDraft(next);
      return true;
    } catch {
      // best-effort YAML from a retry-exhausted draft didn't parse (rare)
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
      <div className="flex flex-col gap-6">
        <header>
          <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
            {t("reviewByline")}
          </p>
          <h1 className="mt-1 font-heading text-2xl font-semibold tracking-tight">
            {t("reviewTitle")}
          </h1>
        </header>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
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
      </div>
    );
  }

  if (phase === "loading") {
    return <AuthorLoading />;
  }

  const examples = [t("example1"), t("example2"), t("example3")];

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="font-mono text-xs tracking-wide text-muted-foreground uppercase">
          {t("describeByline")}
        </p>
        <h1 className="mt-1 font-heading text-3xl font-semibold tracking-tight">
          {t("describeTitle")}
        </h1>
        <p className="mt-2 text-muted-foreground">{t("describeHint")}</p>
      </header>

      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={5}
        placeholder={t("describePlaceholder")}
        className="resize-none"
      />

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          {t("examplesTitle")}
        </span>
        <div className="flex flex-col gap-1.5">
          {examples.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setDescription(ex)}
              className="flex items-start gap-2 rounded-md border px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
            >
              <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" />
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => void generate()}
          disabled={!description.trim()}
          className={cn(buttonVariants(), "gap-2")}
        >
          <Wand2 className="size-4" />
          {t("generate")}
        </button>
      </div>
    </div>
  );
}

// A designed 10–30s loading state (spec §8 risk): the frontier call is slow, so
// this reads as deliberate work — cycling status + a skeleton of the persona
// taking shape — not a blank spinner.
function AuthorLoading() {
  const t = useTranslations("author");
  const steps = [t("loadingStep1"), t("loadingStep2"), t("loadingStep3")];
  const [i, setI] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % 3), 2800);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center gap-3">
        <Wand2 className="size-5 animate-pulse text-primary" />
        <div>
          <h1 className="font-heading text-2xl font-semibold tracking-tight">
            {t("loadingTitle")}
          </h1>
          <p className="text-sm text-muted-foreground">{steps[i]}</p>
        </div>
      </header>
      <div className="flex flex-col gap-4">
        {[0, 1, 2].map((row) => (
          <Card key={row} className="gap-3 p-5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </Card>
        ))}
      </div>
    </div>
  );
}
