import ReactMarkdown, { type Components } from "react-markdown";

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
  return <ReactMarkdown components={COMPONENTS}>{children}</ReactMarkdown>;
}
