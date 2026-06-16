"use client";

import { useAuth } from "@clerk/nextjs";
import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { createApiClient, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

export type ToolRecommendation = components["schemas"]["ToolRecommendation"];

/** A human label for a provider tag (spec 30 T11 — explained picks, grouped). */
function providerGroup(provider: string): "tool" | "skill" | "mcp" {
  if (provider === "skill") return "skill";
  if (provider.startsWith("mcp:")) return "mcp";
  return "tool";
}

/**
 * Spec 30 T11 — the recommender's provider-tagged picks, surfaced as
 * suggested-and-explained. A user-triggered call (cost-aware — it deducts the
 * flat authoring credit) to POST /v1/personas/recommend-capabilities; the picks
 * render with their rationale + confidence, grouped by provider, each
 * applicable to the persona with one click. Composes the unified backend
 * recommender (D-26-10/D-27-13) — built-in tools ∪ skills ∪ MCP, combined-capped.
 */
export function SuggestCapabilities({
  description,
  onApply,
  isApplied,
}: {
  description: string;
  onApply: (rec: ToolRecommendation) => void;
  isApplied: (rec: ToolRecommendation) => boolean;
}) {
  const t = useTranslations("author");
  const { getToken } = useAuth();
  const [picks, setPicks] = useState<ToolRecommendation[] | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);

  const suggest = useCallback(async () => {
    if (pending || !description.trim()) return;
    setPending(true);
    setError(false);
    try {
      const jwt = await getToken(TEMPLATE ? { template: TEMPLATE } : undefined);
      const client = createApiClient(() => Promise.resolve(jwt));
      const res = await unwrap(
        await client.POST("/v1/personas/recommend-capabilities", {
          body: { description },
        }),
      );
      setPicks(res.recommendations ?? []);
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }, [description, getToken, pending]);

  return (
    <div className="flex flex-col gap-3" data-slot="suggest-capabilities">
      <button
        type="button"
        onClick={() => void suggest()}
        disabled={pending || !description.trim()}
        className={cn(
          buttonVariants({ variant: "outline", size: "sm" }),
          "w-fit gap-1.5",
        )}
      >
        <Sparkles className="size-3.5" aria-hidden="true" />
        {pending ? t("suggesting") : t("suggestCapabilities")}
      </button>

      {error ? (
        <p className="text-xs text-destructive">{t("suggestError")}</p>
      ) : null}

      {picks !== null && picks.length === 0 && !error ? (
        <p className="text-xs text-muted-foreground">{t("suggestEmpty")}</p>
      ) : null}

      {picks && picks.length > 0 ? (
        <ul className="flex flex-col gap-1.5" data-slot="suggestion-list">
          {picks.map((rec) => {
            const applied = isApplied(rec);
            return (
              <li
                key={`${rec.provider}:${rec.tool_name}`}
                className="flex items-start gap-2 rounded-md border bg-muted/30 p-2"
                data-slot="suggestion"
                data-provider={rec.provider}
              >
                <span className="mt-0.5 shrink-0 rounded-sm border border-border bg-background px-1.5 py-0.5 font-mono text-[0.625rem] tracking-wide uppercase">
                  {providerGroup(rec.provider)}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-xs">{rec.tool_name}</p>
                  <p className="type-caption text-muted-foreground">
                    {rec.rationale}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={applied}
                  onClick={() => onApply(rec)}
                  className={cn(
                    buttonVariants({ variant: "ghost", size: "sm" }),
                    "shrink-0",
                  )}
                  data-slot="suggestion-apply"
                >
                  {applied ? "✓" : t("suggestApply")}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Spec 30 T11 — the doc-mutation a recommendation maps to: a skill → the skills
 * list; an MCP server → an `mcp:<name>` entry in tools; anything else → tools.
 * Pure so the editor + tests share one mapping.
 */
export function applyRecommendation(
  rec: ToolRecommendation,
  current: { tools: string[]; skills: string[] },
): { tools: string[]; skills: string[] } {
  const group = providerGroup(rec.provider);
  if (group === "skill") {
    if (current.skills.includes(rec.tool_name)) return current;
    return { ...current, skills: [...current.skills, rec.tool_name] };
  }
  const entry =
    group === "mcp" && !rec.tool_name.startsWith("mcp:")
      ? `mcp:${rec.tool_name}`
      : rec.tool_name;
  if (current.tools.includes(entry)) return current;
  return { ...current, tools: [...current.tools, entry] };
}

/** Whether a recommendation is already present in the persona's selection. */
export function recommendationApplied(
  rec: ToolRecommendation,
  current: { tools: string[]; skills: string[] },
): boolean {
  const before = current;
  const after = applyRecommendation(rec, current);
  return after === before;
}
