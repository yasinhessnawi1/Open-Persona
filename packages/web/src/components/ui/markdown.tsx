import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

// Editorial Markdown for the run viewer's final deliverable (acceptance #4).
// Tailwind v4 ships no `prose` plugin here, so elements are mapped explicitly to
// keep the "editorial instrument" voice (Fraunces headings, measured leading).
// react-markdown does not render raw HTML (no rehype-raw), so input is XSS-safe.
//
// F2 T11 retokenise (D-F2-1): typography utilities here (text-xl/lg/base/sm/xs)
// resolve through F1's type scale (text-sm = 14px = .type-ui equivalent; text-xs
// = 12px). Per the F2 T01 audit, the markdown wrapper is verify-and-document:
// no motion to retokenise; no shadow to elevate; inline-code text-[0.8em] is
// the F1-documented exception (allowlist in scripts/no-literals.sh). Prose
// body sizing could promote text-sm → .type-body (14→15px) if T34 criterion-#11
// review names that direction; deferred there.
const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mt-5 mb-2 font-heading text-xl font-semibold tracking-tight first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-5 mb-2 font-heading text-lg font-semibold tracking-tight first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-4 mb-1.5 font-heading text-base font-semibold first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="my-2 text-sm leading-relaxed first:mt-0 last:mb-0">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="my-2 list-disc space-y-1 pl-5 text-sm leading-relaxed">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal space-y-1 pl-5 text-sm leading-relaxed">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="marker:text-muted-foreground">{children}</li>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary underline underline-offset-2 hover:no-underline"
    >
      {children}
    </a>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-border" />,
  // GFM tables: remark-gfm parses them, but without explicit element mappings
  // they fall back to unstyled, cramped browser defaults. Render an editorial
  // table — bordered, header-emphasised — inside a horizontal scroll wrapper so
  // a wide table never blows out the message column.
  table: ({ children }) => (
    <div className="my-3 w-full overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-border bg-muted/50">{children}</thead>
  ),
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => (
    <tr className="border-b border-border last:border-0">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="px-3 py-2 text-left align-top font-semibold">{children}</th>
  ),
  td: ({ children }) => (
    <td className="px-3 py-2 align-top text-muted-foreground">{children}</td>
  ),
  img: ({ src, alt }) => {
    // Spec 28 follow-up: a persona reply / markdown file may embed an image as
    // `![alt](src)`. Workspace artifacts (e.g. `uploads/<hash>.png`, or the
    // `/v1/personas/:id/uploads/...` route) require a Bearer-authed fetch and
    // CANNOT load via a plain <img> — the browser sends no Authorization header
    // — so such embeds always render as a broken-image icon. The artifact is
    // already shown by the inline <FileCard> + right-panel renderer, so we
    // SUPPRESS non-external image embeds entirely (no broken icon, no redundant
    // duplicate). Genuine external images (https / data / blob) still render.
    const s = typeof src === "string" ? src : "";
    if (!/^(https?:|data:|blob:)/i.test(s)) return null;
    return (
      // biome-ignore lint/performance/noImgElement: model/markdown-provided external URLs can't go through next/image
      <img
        src={s}
        alt={alt ?? ""}
        className="my-2 max-w-full rounded-md"
        loading="lazy"
      />
    );
  },
  code: ({ className, children }) => {
    // react-markdown v10 dropped the `inline` prop: a fenced block has a
    // `language-*` class or multi-line content; everything else is inline.
    const isBlock =
      className?.startsWith("language-") ||
      String(children ?? "").includes("\n");
    if (isBlock) {
      return <code className="font-mono text-xs">{children}</code>;
    }
    return (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.8em]">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-md border bg-muted/50 p-3 text-xs leading-relaxed">
      {children}
    </pre>
  ),
};

export function Markdown({ children }: { children: string }) {
  // Spec 28 (D-28-X-svg-sanitization): react-markdown already escapes raw HTML
  // (no rehype-raw), and `rehype-sanitize` is the explicit defense-in-depth for
  // the rich-output renderer rendering untrusted persisted markdown files.
  // `remark-gfm` adds tables / task-lists / strikethrough.
  return (
    <ReactMarkdown
      components={COMPONENTS}
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeSanitize]}
    >
      {children}
    </ReactMarkdown>
  );
}
