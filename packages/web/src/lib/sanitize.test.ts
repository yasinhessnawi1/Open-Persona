import { describe, expect, it } from "vitest";
import { sanitizeHtml, sanitizeSvg } from "./sanitize";

// Spec 28 — D-28-X-svg-sanitization. The XSS-critical helpers every SVG/HTML
// render path routes through.

describe("sanitizeSvg", () => {
  it("strips <script> from SVG", () => {
    const dirty =
      '<svg><script>alert(1)</script><rect width="10" height="10"/></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean).not.toContain("<script");
    expect(clean).not.toContain("alert(1)");
  });

  it("strips onload / event-handler attributes", () => {
    const dirty =
      '<svg onload="alert(1)"><circle cx="5" cy="5" r="3" onclick="steal()"/></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean.toLowerCase()).not.toContain("onload");
    expect(clean.toLowerCase()).not.toContain("onclick");
  });

  it("drops foreignObject (the SVG→HTML XSS bridge)", () => {
    const dirty =
      '<svg><foreignObject><img src=x onerror="alert(1)"/></foreignObject></svg>';
    const clean = sanitizeSvg(dirty);
    expect(clean.toLowerCase()).not.toContain("foreignobject");
    expect(clean.toLowerCase()).not.toContain("onerror");
  });

  it("keeps legitimate SVG shapes", () => {
    const clean = sanitizeSvg('<svg><rect width="10" height="10"/></svg>');
    expect(clean).toContain("rect");
  });
});

describe("sanitizeHtml", () => {
  it("strips <script> and inline handlers", () => {
    const dirty = '<div onclick="x()">hi<script>alert(1)</script></div>';
    const clean = sanitizeHtml(dirty);
    expect(clean).not.toContain("<script");
    expect(clean.toLowerCase()).not.toContain("onclick");
    expect(clean).toContain("hi");
  });

  it("strips nested iframe/object/embed", () => {
    const clean = sanitizeHtml('<p>ok</p><iframe src="evil"></iframe>');
    expect(clean.toLowerCase()).not.toContain("<iframe");
    expect(clean).toContain("ok");
  });
});
