"use client";

import { Code2, Save, Sparkles } from "lucide-react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { useCallback, useState } from "react";
import {
  AutonomyConsentSection,
  type AutonomyLevel,
} from "@/components/persona/autonomy-consent-section";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { ClarifyingQuestion } from "@/lib/api";
import {
  docToYaml,
  type PersonaDoc,
  readIdentity,
  readStringList,
  writeStringList,
  yamlToDoc,
} from "@/lib/persona-draft";
import { cn } from "@/lib/utils";
import { ByoMcpManager } from "./byo-mcp-manager";
import {
  CollapsibleSection,
  SectionGroup,
  SectionTimelineNav,
} from "./collapsible-section";
import { type McpCatalogEntry, PersonaForm } from "./persona-form";
import {
  applyRecommendation,
  recommendationApplied,
  SuggestCapabilities,
  type ToolRecommendation,
} from "./suggest-capabilities";

const AUTONOMY_LEVELS: readonly AutonomyLevel[] = [
  "cautious",
  "balanced",
  "decisive",
];

/** Read the doc's autonomy field, defaulting to the schema default "cautious". */
function readAutonomy(doc: PersonaDoc): AutonomyLevel {
  const value = doc.autonomy;
  return typeof value === "string" &&
    (AUTONOMY_LEVELS as readonly string[]).includes(value)
    ? (value as AutonomyLevel)
    : "cautious";
}

// Monaco is lazy + client-only so it never enters the chat-page bundle (D-09-8).
const YAMLEditor = dynamic(() => import("./yaml-editor"), {
  ssr: false,
  loading: () => <Skeleton className="h-[440px] w-full rounded-md" />,
});

type SaveResult = { error: string } | undefined;

/**
 * The clarifying-questions + refinement seam (spec 10, D-10-2 / D-10-5). Present
 * only in the authoring wizard (not when editing an existing persona). Answering
 * a question re-generates the draft; the wizard owns the round counter and hides
 * this once `round >= maxRounds` (the server backstops the cap).
 */
export type Refinement = {
  questions: ClarifyingQuestion[];
  round: number;
  maxRounds: number;
  refining: boolean;
  onAnswer: (question: string, answer: string, currentYaml: string) => void;
};

/**
 * The shared persona editor (T08): structured form ⇄ Monaco YAML, kept in sync
 * with the parsed object as the single source of truth (D-09-9). A form edit
 * regenerates the YAML; a YAML edit re-parses into the form, and invalid YAML
 * surfaces an error while the form keeps its last valid state (save is blocked
 * until the YAML parses).
 */
export function PersonaEditor({
  initialDoc,
  tools,
  skills,
  mcpServers = [],
  personaId,
  onSave,
  saveLabel,
  refinement,
  initialConsent,
  onConsentChange,
}: {
  initialDoc: PersonaDoc;
  tools: string[];
  skills: string[];
  // Spec 30 T11 — built-in MCP servers for the unified capability section.
  mcpServers?: McpCatalogEntry[];
  // Spec 30 T12 — the saved persona's id; enables the BYO-MCP manager (needs an
  // id to assign servers to). Absent in the author/new flow (no id yet). Spec 31
  // (D-31-X-autonomy-placement): also gates the autonomy + consent section —
  // when set with `onConsentChange`, the selector + toggle are surfaced (a
  // consent PATCH needs a persisted persona; the author/new flow omits them).
  personaId?: string;
  onSave: (yaml: string) => Promise<SaveResult>;
  saveLabel: string;
  refinement?: Refinement;
  initialConsent?: boolean | null;
  onConsentChange?: (granted: boolean | null) => Promise<SaveResult>;
}) {
  const t = useTranslations("author");
  const [doc, setDoc] = useState<PersonaDoc>(initialDoc);
  const [yamlText, setYamlText] = useState<string>(() => docToYaml(initialDoc));
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [showYaml, setShowYaml] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Consent is a DB-backed tri-state persisted out-of-band from the YAML save
  // (PATCH /consent); autonomy rides the doc → YAML save like any other field.
  const [consent, setConsent] = useState<boolean | null>(
    initialConsent ?? null,
  );
  const [consentPending, setConsentPending] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  const onFormChange = useCallback((next: PersonaDoc) => {
    setDoc(next);
    setYamlText(docToYaml(next));
    setYamlError(null);
  }, []);

  const handleConsentChange = useCallback(
    async (granted: boolean | null) => {
      if (!onConsentChange) return;
      const prev = consent;
      setConsent(granted); // optimistic
      setConsentPending(true);
      setConsentError(null);
      try {
        const result = await onConsentChange(granted);
        if (result?.error) {
          setConsent(prev); // revert
          setConsentError(result.error);
        }
      } catch {
        setConsent(prev);
        setConsentError(t("saveFailed"));
      } finally {
        setConsentPending(false);
      }
    },
    [consent, onConsentChange, t],
  );

  const onYamlChange = useCallback((text: string) => {
    setYamlText(text);
    try {
      setDoc(yamlToDoc(text));
      setYamlError(null);
    } catch (e) {
      setYamlError((e as Error).message);
    }
  }, []);

  // Spec 30 T11 — apply a recommender pick to the persona (skill → skills list;
  // MCP → mcp:<name> in tools; else tools), keeping the YAML buffer in sync.
  const currentCapabilities = useCallback(
    () => ({
      tools: readStringList(doc, "tools"),
      skills: readStringList(doc, "skills"),
    }),
    [doc],
  );
  const applyRec = useCallback(
    (rec: ToolRecommendation) => {
      const next = applyRecommendation(rec, currentCapabilities());
      onFormChange(
        writeStringList(
          writeStringList(doc, "tools", next.tools),
          "skills",
          next.skills,
        ),
      );
    },
    [doc, currentCapabilities, onFormChange],
  );
  const identity = readIdentity(doc);
  const suggestDescription = [identity.role, identity.background]
    .filter(Boolean)
    .join(". ")
    .trim();

  async function save() {
    if (saving || yamlError) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await onSave(yamlText);
      if (result?.error) setSaveError(result.error);
    } catch {
      setSaveError(t("saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <SectionGroup>
      <div className="lg:grid lg:grid-cols-[14rem_1fr] lg:gap-6">
        <SectionTimelineNav />
        <div className="flex min-w-0 flex-col gap-5">
          <PersonaForm
            doc={doc}
            onChange={onFormChange}
            tools={tools}
            skills={skills}
            mcpServers={mcpServers}
          />

          {/* Autonomy + consent: existing-persona edit only (D-31-X-autonomy-placement). */}
          {personaId && onConsentChange ? (
            <CollapsibleSection id="autonomy" title={t("autonomyTitle")}>
              <AutonomyConsentSection
                autonomy={readAutonomy(doc)}
                onAutonomyChange={(level) =>
                  onFormChange({ ...doc, autonomy: level })
                }
                consent={consent}
                onConsentChange={(granted) => void handleConsentChange(granted)}
                pending={consentPending}
              />
              {consentError ? (
                <p className="mt-2 text-sm text-destructive">
                  {t("saveError", { error: consentError })}
                </p>
              ) : null}
            </CollapsibleSection>
          ) : null}

          <SuggestCapabilities
            description={suggestDescription}
            onApply={applyRec}
            isApplied={(rec) =>
              recommendationApplied(rec, currentCapabilities())
            }
          />

          {/* Spec 30 T12 — BYO MCP servers (only for a saved persona). */}
          {personaId ? <ByoMcpManager personaId={personaId} /> : null}

          {/* Spec-10 seam: clarifying questions + refinement (D-10-2 / D-10-5).
              Authoring-only; a collapsible section that opens by default so it
              is the second open card in the authoring flow. */}
          {refinement && refinement.round >= refinement.maxRounds ? (
            <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
              {t("refineLimitReached")}
            </p>
          ) : refinement && refinement.questions.length > 0 ? (
            <CollapsibleSection
              id="refine"
              title={t("questionsTitle")}
              defaultOpen
            >
              <RefineQuestions refinement={refinement} currentYaml={yamlText} />
            </CollapsibleSection>
          ) : null}

          <div className="flex flex-col gap-2">
            <button
              type="button"
              onClick={() => setShowYaml((v) => !v)}
              className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              <Code2 className="size-4" />
              {showYaml ? t("hideRawYaml") : t("editRawYaml")}
            </button>
            {showYaml ? (
              <>
                <YAMLEditor value={yamlText} onChange={onYamlChange} />
                {yamlError ? (
                  <p className="text-sm text-destructive">
                    {t("yamlInvalid", { error: yamlError })}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>

          <div className="flex items-center justify-end gap-3">
            {saveError ? (
              <p className="flex-1 text-sm text-destructive">
                {t("saveError", { error: saveError })}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving || yamlError !== null}
              className={cn(buttonVariants(), "gap-2")}
            >
              <Save className="size-4" />
              {saving ? t("saving") : saveLabel}
            </button>
          </div>
        </div>
      </div>
    </SectionGroup>
  );
}

function RefineQuestions({
  refinement,
  currentYaml,
}: {
  refinement: Refinement;
  currentYaml: string;
}) {
  const t = useTranslations("author");
  const [answers, setAnswers] = useState<Record<number, string>>({});

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Sparkles className="size-3.5 text-primary" />
        {t("questionsHint", { max: refinement.maxRounds })}
      </p>
      <ul className="flex flex-col gap-3">
        {refinement.questions.map((q, i) => {
          const answer = answers[i] ?? "";
          return (
            <li
              key={`${q.section}-${q.question}`}
              className="flex flex-col gap-1.5"
            >
              <span className="text-sm">
                <span className="font-mono text-xs text-muted-foreground">
                  {q.section}
                </span>{" "}
                {q.question}
              </span>
              <div className="flex items-end gap-2">
                <Textarea
                  rows={1}
                  value={answer}
                  onChange={(e) =>
                    setAnswers((a) => ({ ...a, [i]: e.target.value }))
                  }
                  placeholder={t("answerPlaceholder")}
                  className="min-h-9 resize-none"
                />
                <button
                  type="button"
                  disabled={refinement.refining || !answer.trim()}
                  onClick={() =>
                    refinement.onAnswer(q.question, answer.trim(), currentYaml)
                  }
                  className={cn(
                    buttonVariants({ variant: "outline" }),
                    "shrink-0",
                  )}
                >
                  {refinement.refining ? t("refining") : t("applyAnswer")}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
