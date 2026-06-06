"use client";

import { CornerDownLeft } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/**
 * Spec F2 T30 — AskUserPrompt (retokenised).
 *
 * The ask-user moment (spec §4.4): the agentic loop blocks on a question; the
 * answer is delivered via POST /runs/:id/respond and the run resumes.
 *
 * Behaviour preserved verbatim (per audit.md §runs.plumbing). T30 closes
 * `text-sm` → `.type-body` (question copy reads as inline conversation, not
 * UI chrome) and standardises the icon to `aria-hidden`.
 */
export function AskUserPrompt({
  question,
  onAnswer,
}: {
  question: string;
  onAnswer: (answer: string) => Promise<void>;
}) {
  const t = useTranslations("runs");
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);

  async function submit() {
    const answer = value.trim();
    if (!answer || pending) return;
    setPending(true);
    try {
      await onAnswer(answer);
      setValue("");
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className="rounded-md border border-primary/30 bg-primary/5 p-3"
      data-slot="ask-user-prompt"
    >
      <p className="type-body mb-2 font-medium" data-slot="ask-user-question">
        {question}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex items-end gap-2"
      >
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder={t("answerPlaceholder")}
          rows={1}
          disabled={pending}
          className="field-sizing-content max-h-40 min-h-10 flex-1 resize-none bg-background"
        />
        <button
          type="submit"
          disabled={pending || !value.trim()}
          className={cn(buttonVariants({ size: "sm" }), "gap-1.5")}
        >
          <CornerDownLeft className="size-3.5" aria-hidden="true" />
          {t("answer")}
        </button>
      </form>
    </div>
  );
}
