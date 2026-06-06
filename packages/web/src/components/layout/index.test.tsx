/**
 * Spec F2 T20 — Layout primitives tests.
 *
 * Verifies the five primitives render correctly with their token-clean
 * defaults + variant props. All are server-renderable (no hooks/refs).
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Grid, PageBody, PageHeader, Section, Stack } from "./index";

describe("PageHeader", () => {
  it("renders title in .type-heading", () => {
    const { container } = render(<PageHeader title="Personas" />);
    const title = container.querySelector('[data-slot="page-header-title"]');
    expect(title?.textContent).toBe("Personas");
    expect(title?.className).toContain("type-heading");
  });

  it("renders subtitle in .type-ui muted when provided", () => {
    const { container } = render(
      <PageHeader title="Personas" subtitle="Your AI personas" />,
    );
    const subtitle = container.querySelector(
      '[data-slot="page-header-subtitle"]',
    );
    expect(subtitle?.textContent).toBe("Your AI personas");
    expect(subtitle?.className).toContain("type-ui");
    expect(subtitle?.className).toContain("text-muted-foreground");
  });

  it("omits subtitle when not provided", () => {
    const { container } = render(<PageHeader title="Personas" />);
    expect(
      container.querySelector('[data-slot="page-header-subtitle"]'),
    ).toBeNull();
  });

  it("renders actions slot when provided", () => {
    const { container } = render(
      <PageHeader
        title="Personas"
        actions={<button type="button">New</button>}
      />,
    );
    const actions = container.querySelector(
      '[data-slot="page-header-actions"]',
    );
    expect(actions).not.toBeNull();
    expect(actions?.textContent).toBe("New");
  });
});

describe("PageBody", () => {
  it("applies max-w-4xl by default", () => {
    const { container } = render(
      <PageBody>
        <p>x</p>
      </PageBody>,
    );
    const body = container.querySelector('[data-slot="page-body"]');
    expect(body?.className).toContain("max-w-4xl");
  });

  it("applies max-w-2xl for narrow width", () => {
    const { container } = render(
      <PageBody width="narrow">
        <p>x</p>
      </PageBody>,
    );
    const body = container.querySelector('[data-slot="page-body"]');
    expect(body?.className).toContain("max-w-2xl");
  });

  it("applies max-w-6xl for wide width", () => {
    const { container } = render(
      <PageBody width="wide">
        <p>x</p>
      </PageBody>,
    );
    const body = container.querySelector('[data-slot="page-body"]');
    expect(body?.className).toContain("max-w-6xl");
  });
});

describe("Section", () => {
  it("renders heading in .type-heading when provided", () => {
    const { container } = render(
      <Section heading="Constraints">
        <p>x</p>
      </Section>,
    );
    const heading = container.querySelector('[data-slot="section-heading"]');
    expect(heading?.textContent).toBe("Constraints");
    expect(heading?.className).toContain("type-heading");
  });

  it("omits heading when not provided", () => {
    const { container } = render(
      <Section>
        <p>x</p>
      </Section>,
    );
    expect(container.querySelector('[data-slot="section-heading"]')).toBeNull();
  });
});

describe("Stack", () => {
  it("applies gap-4 by default", () => {
    const { container } = render(
      <Stack>
        <p>x</p>
      </Stack>,
    );
    const stack = container.querySelector('[data-slot="stack"]');
    expect(stack?.className).toContain("gap-4");
    expect(stack?.className).toContain("flex-col");
  });

  it("applies gap-6 when specified", () => {
    const { container } = render(
      <Stack gap={6}>
        <p>x</p>
      </Stack>,
    );
    const stack = container.querySelector('[data-slot="stack"]');
    expect(stack?.className).toContain("gap-6");
  });
});

describe("Grid", () => {
  it("applies single column at base by default", () => {
    const { container } = render(
      <Grid cols={{ base: 1 }}>
        <p>x</p>
      </Grid>,
    );
    const grid = container.querySelector('[data-slot="grid"]');
    expect(grid?.className).toContain("grid-cols-1");
    expect(grid?.className).toContain("grid");
  });

  it("applies responsive cols across breakpoints", () => {
    const { container } = render(
      <Grid cols={{ base: 1, sm: 2, lg: 3 }}>
        <p>x</p>
      </Grid>,
    );
    const grid = container.querySelector('[data-slot="grid"]');
    expect(grid?.className).toContain("grid-cols-1");
    expect(grid?.className).toContain("sm:grid-cols-2");
    expect(grid?.className).toContain("lg:grid-cols-3");
  });

  it("honours custom gap", () => {
    const { container } = render(
      <Grid cols={{ base: 2 }} gap={6}>
        <p>x</p>
      </Grid>,
    );
    const grid = container.querySelector('[data-slot="grid"]');
    expect(grid?.className).toContain("gap-6");
  });
});
