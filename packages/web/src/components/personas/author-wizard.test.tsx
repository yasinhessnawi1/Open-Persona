/**
 * AuthorWizard — the Spec 36 new-persona flow.
 *
 * Three create paths converge on the shared editor + one direct-create assembly:
 *   1. pick a prebuilt starter → editable structured draft (no `/author` call);
 *   2. start from scratch → empty structured draft;
 *   3. describe your own → the drafter.
 * The gallery now LEADS (starters are primary) and picking a card opens the
 * editor directly rather than seeding the describe textarea.
 *
 * `PersonaEditor` is mocked (its form ⇄ YAML internals have their own tests); we
 * capture its props to assert the wizard hands it the right draft + wiring, and
 * drive its `onSave` to prove the direct-create assembly reaches `createPersona`
 * with the safety constraint intact. `useAuthor` + `createPersona` are mocked so
 * no Clerk provider or network is needed.
 */

import { fireEvent, render, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import messages from "@/i18n/messages/en.json";
import { PERSONA_EXAMPLE_CATEGORIES } from "@/lib/persona-examples";
import { SAFETY_CONSTRAINT } from "@/lib/persona-safety";
import { AuthorWizard } from "./author-wizard";

const author = vi.fn(async () => ({ yaml: "", questions: [] }));
const createPersona = vi.fn(
  async (_yaml: string): Promise<{ error: string } | undefined> => undefined,
);

// Capture the props the wizard hands the editor + expose a save trigger.
const captured: { props: Record<string, unknown> | null } = { props: null };

vi.mock("@/lib/hooks/use-author", () => ({
  useAuthor: () => ({ author, refine: vi.fn() }),
}));
vi.mock("@/lib/persona-actions", () => ({
  createPersona: (yaml: string) => createPersona(yaml),
}));
vi.mock("./persona-editor", () => ({
  PersonaEditor: (props: Record<string, unknown>) => {
    captured.props = props;
    return (
      <div data-slot="mock-editor">
        <button
          type="button"
          data-slot="mock-save"
          onClick={async () => {
            const { docToYaml } = await import("@/lib/persona-draft");
            await (props.onSave as (y: string) => Promise<unknown>)(
              docToYaml(props.initialDoc as Record<string, unknown>),
            );
          }}
        >
          save
        </button>
      </div>
    );
  },
}));

beforeEach(() => {
  author.mockClear();
  createPersona.mockClear();
  captured.props = null;
});

function renderWizard() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <AuthorWizard tools={[]} skills={[]} />
    </NextIntlClientProvider>,
  );
}

function bySlot(container: HTMLElement, slot: string): Element {
  const el = container.querySelector(`[data-slot="${slot}"]`);
  expect(el, `missing [data-slot="${slot}"]`).not.toBeNull();
  return el as Element;
}

function isBefore(a: Element, b: Element): boolean {
  return Boolean(
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

const firstStarter = PERSONA_EXAMPLE_CATEGORIES[0].examples[0];

describe("AuthorWizard — describe-phase layout", () => {
  it("LEADS with describe + start-from-scratch, above the starter gallery", () => {
    const { container } = renderWizard();
    const textarea = bySlot(container, "author-wizard-description");
    const scratch = bySlot(container, "author-wizard-scratch");
    const gallery = bySlot(container, "example-gallery");
    // Layout: the describe box + scratch button are on top; the starter
    // suggestions sit underneath.
    expect(isBefore(textarea, gallery)).toBe(true);
    expect(isBefore(scratch, gallery)).toBe(true);
  });

  it("offers a start-from-scratch path", () => {
    const { container } = renderWizard();
    expect(bySlot(container, "author-wizard-scratch")).toBeTruthy();
  });
});

describe("AuthorWizard — prebuilt starter (direct create)", () => {
  it("opens the editor directly on the structured starter, NOT the textarea", () => {
    const { container, getByLabelText } = renderWizard();
    const label = messages.author.gallery.useNamed.replace(
      "{name}",
      firstStarter.name,
    );
    fireEvent.click(getByLabelText(label));

    // The editor is shown; the describe textarea is gone (no drafter seeding).
    expect(bySlot(container, "mock-editor")).toBeTruthy();
    expect(
      container.querySelector('[data-slot="author-wizard-description"]'),
    ).toBeNull();

    // The editor received the starter's structure, no refinement seam (direct).
    const doc = captured.props?.initialDoc as {
      identity: { name: string; constraints: string[] };
    };
    expect(doc.identity.name).toBe(firstStarter.name);
    expect(captured.props?.refinement).toBeUndefined();
    // The safety constraint is present + first (pinned).
    expect(doc.identity.constraints[0]).toBe(SAFETY_CONSTRAINT);
  });

  it("creates directly: onSave assembles guarded YAML and calls createPersona", async () => {
    const { getByLabelText, container } = renderWizard();
    fireEvent.click(
      getByLabelText(
        messages.author.gallery.useNamed.replace("{name}", firstStarter.name),
      ),
    );
    fireEvent.click(bySlot(container, "mock-save"));

    await waitFor(() => expect(createPersona).toHaveBeenCalledTimes(1));
    const yaml = createPersona.mock.calls[0][0] as string;
    expect(yaml).toContain(SAFETY_CONSTRAINT);
    // Direct create never invokes the drafter.
    expect(author).not.toHaveBeenCalled();
  });
});

describe("AuthorWizard — start from scratch", () => {
  it("opens an empty editable draft with the safety constraint pinned", () => {
    const { container } = renderWizard();
    fireEvent.click(bySlot(container, "author-wizard-scratch"));
    expect(bySlot(container, "mock-editor")).toBeTruthy();
    const doc = captured.props?.initialDoc as {
      identity: { constraints: string[] };
    };
    expect(doc.identity.constraints[0]).toBe(SAFETY_CONSTRAINT);
    expect(author).not.toHaveBeenCalled();
  });
});

describe("AuthorWizard — describe your own (drafter preserved)", () => {
  it("still calls the drafter on Generate", async () => {
    const { container } = renderWizard();
    const textarea = bySlot(
      container,
      "author-wizard-description",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, {
      target: { value: "a tenancy law assistant" },
    });
    fireEvent.click(bySlot(container, "author-wizard-generate"));
    await waitFor(() => expect(author).toHaveBeenCalledTimes(1));
  });
});
