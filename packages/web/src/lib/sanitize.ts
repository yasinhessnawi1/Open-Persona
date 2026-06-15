/**
 * Spec 28 — D-28-X-svg-sanitization (SECURITY).
 *
 * Single source of truth for sanitizing untrusted markup BEFORE it touches the
 * DOM. SVG is an XSS vector (`<script>`, `onload=`, `<foreignObject>` with
 * embedded HTML), so EVERY SVG render path — Mermaid output, Graphviz output,
 * and any inline SVG served from the persister — MUST pass through
 * {@link sanitizeSvg} before injection. The sandboxed-HTML renderer passes its
 * source through {@link sanitizeHtml} as defense-in-depth on top of the iframe
 * sandbox (markdown is separately hardened via `rehype-sanitize`).
 *
 * DOMPurify is browser-only; these helpers are called exclusively from client
 * components (the right-panel renderers), never during SSR.
 */

import DOMPurify from "dompurify";

/**
 * Sanitize an SVG string for safe DOM injection.
 *
 * Allows the SVG element set (shapes, paths, text, filters — what mermaid /
 * graphviz emit) while stripping scripts, event-handler attributes, and other
 * active content. `<foreignObject>` is forbidden: mermaid uses it for HTML
 * labels, but it re-opens the full HTML XSS surface inside SVG, so we drop it
 * (labels degrade to SVG `<text>`).
 */
export function sanitizeSvg(svg: string): string {
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["foreignObject", "script"],
    FORBID_ATTR: ["onload", "onerror", "onclick"],
  });
}

/**
 * Sanitize an HTML string for safe rendering inside the sandboxed iframe.
 *
 * Defense-in-depth: the iframe carries `sandbox` WITHOUT `allow-scripts`, but
 * we still strip active content here so the markup is inert even if the iframe
 * attributes are ever weakened. Never combine `allow-scripts` +
 * `allow-same-origin` on the consuming iframe (that defeats the sandbox).
 */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    FORBID_TAGS: [
      "script",
      "style",
      "iframe",
      "object",
      "embed",
      "link",
      "base",
    ],
    FORBID_ATTR: ["onload", "onerror", "onclick", "onmouseover", "style"],
  });
}
