"use client";

import { useTranslations } from "next-intl";
import Papa from "papaparse";
import { useMemo } from "react";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { RendererError, RendererLoading, RendererUnavailable } from "./_states";
import type { ArtifactRendererProps } from "./types";

/** CSV renderer — parsed with PapaParse, rendered as a semantic table.
 *  (A plain table keeps deps minimal per ENGINEERING_STANDARDS §5; column
 *  sorting/virtualization is a v0.2 candidate.) */
export function CsvRenderer({
  personaId,
  workspacePath,
}: ArtifactRendererProps) {
  const t = useTranslations("chat.output.renderer");
  const { text, loading, error } = useAuthedArtifactText(
    personaId,
    workspacePath,
  );
  const rows = useMemo<string[][]>(() => {
    if (text === null) return [];
    const parsed = Papa.parse<string[]>(text.trim(), { skipEmptyLines: true });
    return parsed.data;
  }, [text]);

  if (error) return <RendererError reason={error.message} />;
  if (loading && text === null) return <RendererLoading />;
  if (text === null) return <RendererUnavailable />;
  if (rows.length === 0) {
    return (
      <div
        className="p-4 text-sm text-muted-foreground"
        data-slot="renderer-csv-empty"
      >
        {t("emptyTable")}
      </div>
    );
  }

  const [header, ...body] = rows;
  return (
    <div className="overflow-auto p-4" data-slot="renderer-csv">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left">
            {header.map((cell, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: static parsed header row
              <th key={i} className="px-2 py-1 font-semibold">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static parsed rows
            <tr key={r} className="border-b border-border/50">
              {row.map((cell, c) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: static parsed cells
                <td key={c} className="px-2 py-1 align-top font-mono">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
