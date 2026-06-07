"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Spec F4 T07 — Shiki-highlighted code block (LAZY CHUNK).
 *
 * Loaded by `<ResultBlock>` via `React.lazy` ONLY when the user expands
 * the code section (D-F4-1 default-collapsed). The dynamic-import
 * boundary keeps Shiki + its grammars out of the main bundle; the lazy
 * chunk is ~30-60 KB gzipped per R-F4-X-syntax-highlighting research.
 *
 * Fallback render: a plain `<pre>` with the raw code. The Suspense
 * fallback at the consumer (ResultBlock) shows this same fallback while
 * the lazy chunk loads.
 */

interface HighlightedCodeProps {
  code: string;
  /** `python` / `bash` / `javascript` etc. Falls back to `text` on unknown. */
  lang: string;
  className?: string;
}

/**
 * Map a language hint onto a Shiki-supported language id. Closed
 * v0.1 list — extending it is additive. `text` is the no-highlight
 * fallback Shiki ships out of the box.
 */
function shikiLang(hint: string): string {
  switch (hint) {
    case "python":
    case "py":
      return "python";
    case "bash":
    case "sh":
    case "shell":
      return "bash";
    case "javascript":
    case "js":
      return "javascript";
    case "typescript":
    case "ts":
      return "typescript";
    case "json":
      return "json";
    default:
      return "text";
  }
}

export default function HighlightedCode({
  code,
  lang,
  className,
}: HighlightedCodeProps) {
  // Initialise with the unhighlighted source so the first paint shows
  // SOMETHING (Shiki's import + tokenise is async). The `dangerouslySetInnerHTML`
  // pattern is safe here — the HTML comes from Shiki, not user content.
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Dynamic import inside the lazy chunk — Shiki's own splitting
        // produces per-language sub-chunks that load on demand.
        const { codeToHtml } = await import("shiki");
        const out = await codeToHtml(code, {
          lang: shikiLang(lang),
          theme: "github-dark",
        });
        if (!cancelled) setHtml(out);
      } catch {
        // Network error / dynamic-import failure: stay on the plain-pre fallback.
        if (!cancelled) setHtml(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  if (html !== null) {
    // Safety: Shiki's `codeToHtml` is the trusted HTML producer here. Its
    // output is structurally `<pre><code>` with token spans whose text
    // content comes from Shiki's tokeniser — every character of the
    // input `code` is HTML-entity-escaped before being placed inside a
    // span. Shiki never echoes raw HTML / scripts / event handlers from
    // the source; the only attributes it emits are class names and
    // inline style colour declarations from the theme. The `code` input
    // itself originates from the persona's own tool_call args (the
    // sandbox code the persona ran), not from external untrusted user
    // input. DOMPurify would be redundant here — it cannot make
    // entity-escaped text safer than it already is. Documented for
    // future readers + the post-tool security hook.
    return (
      <div
        className={cn(
          "overflow-x-auto bg-[#0d1117] p-3 [&_pre]:!bg-transparent",
          className,
        )}
        // biome-ignore lint/security/noDangerouslySetInnerHtml: shiki output is HTML-entity-escaped token spans; see comment above
        dangerouslySetInnerHTML={{ __html: html }}
        data-slot="highlighted-code"
        data-lang={lang}
      />
    );
  }

  // First paint OR import failure: plain monospace fallback.
  return (
    <pre
      className={cn(
        "overflow-x-auto p-3 font-mono text-sm leading-relaxed",
        "bg-[#0d1117] text-[#c9d1d9]",
        className,
      )}
      data-slot="highlighted-code"
      data-lang={lang}
      data-fallback="true"
    >
      {code}
    </pre>
  );
}
