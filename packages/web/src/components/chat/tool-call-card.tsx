"use client";

import { ChevronRight, Plug, Sparkles, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

// Spec 30 T01/T03 (D-30-1): the four-value source taxonomy the runtime resolves.
type ToolKind = "builtin" | "skill" | "mcp:builtin" | "mcp:optional";

/** Parse the MCP server out of an `mcp:<server>:<tool>` name (empty otherwise). */
function mcpServerName(toolName: string): string {
  if (!toolName.startsWith("mcp:")) return "";
  return toolName.split(":", 3)[1] ?? "";
}

/** The lucide glyph for a source kind — skill ✦, MCP plug, built-in wrench. */
function kindIcon(kind: ToolKind | undefined) {
  if (kind === "skill") return Sparkles;
  if (kind === "mcp:builtin" || kind === "mcp:optional") return Plug;
  return Wrench;
}

// One tool round in the chat stream: the call (name + args) and, once it lands,
// the result. Collapsed by default (transparency without noise — spec §4.1).
export interface ToolEntry {
  toolName: string;
  args?: Record<string, unknown>;
  result?: string;
  isError?: boolean;
  pending: boolean;
  /**
   * Spec 30 T01 (D-30-1): the call's source — `builtin` / `skill` /
   * `mcp:builtin` / `mcp:optional`. Drives the source badge (T03). Absent on
   * pre-spec-30 frames → the card renders unbadged (the legacy wrench).
   */
  kind?: string;
}

export function ToolCallCard({ entry }: { entry: ToolEntry }) {
  const t = useTranslations("chat");
  const hasBody =
    (entry.args && Object.keys(entry.args).length > 0) ||
    entry.result !== undefined;
  const kind = entry.kind as ToolKind | undefined;
  const Icon = kindIcon(kind);
  const server = mcpServerName(entry.toolName);
  // The source badge label: MCP names its server; skill/built-in are plain.
  // Absent kind (pre-spec-30 frames) → no badge (legacy unbadged card).
  const badge =
    kind === "mcp:builtin" || kind === "mcp:optional"
      ? server
        ? t("kindMcpServer", { server })
        : t("kindMcp")
      : kind === "skill"
        ? t("kindSkill")
        : kind === "builtin"
          ? t("kindBuiltin")
          : null;
  return (
    <Collapsible className="group/tool w-fit max-w-full rounded-md border bg-muted/40">
      <CollapsibleTrigger
        disabled={!hasBody}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left font-mono text-xs"
      >
        <Icon
          className={cn(
            "size-3.5 shrink-0 motion-safe:transition-colors",
            entry.pending
              ? "motion-safe:animate-pulse text-primary"
              : "text-muted-foreground",
          )}
        />
        <span className="truncate">
          {t("toolUsing", { tool: entry.toolName })}
        </span>
        {badge ? (
          <span
            className="shrink-0 rounded-sm border border-border bg-background px-1.5 py-0.5 text-[0.625rem] font-medium tracking-wide text-muted-foreground uppercase"
            data-slot="tool-kind-badge"
            data-kind={kind}
          >
            {badge}
          </span>
        ) : null}
        {entry.isError ? (
          <span className="text-destructive">· {t("toolError")}</span>
        ) : null}
        {hasBody ? (
          <ChevronRight className="ml-1 size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[panel-open]/tool:rotate-90" />
        ) : null}
      </CollapsibleTrigger>
      {hasBody ? (
        <CollapsibleContent className="border-t px-3 py-2 font-mono text-xs">
          {entry.args && Object.keys(entry.args).length > 0 ? (
            <pre className="overflow-x-auto whitespace-pre-wrap text-muted-foreground">
              {JSON.stringify(entry.args, null, 2)}
            </pre>
          ) : null}
          {entry.result !== undefined ? (
            <pre
              className={cn(
                "mt-2 overflow-x-auto whitespace-pre-wrap",
                entry.isError ? "text-destructive" : "text-foreground",
              )}
            >
              {entry.result}
            </pre>
          ) : null}
        </CollapsibleContent>
      ) : null}
    </Collapsible>
  );
}
