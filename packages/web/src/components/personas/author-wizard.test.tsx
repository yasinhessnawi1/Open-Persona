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

function pickFirstStarter(getByLabelText: (t: string) => HTMLElement): void {
  fireEvent.click(
    getByLabelText(
      messages.author.gallery.useNamed.replace("{name}", firstStarter.name),
    ),
  );
}

describe("AuthorWizard — prebuilt starter → quick-edit", () => {
  it("reveals the quick-edit card (not the full editor) seeded with the starter", () => {
    const { container, getByLabelText, getByDisplayValue } = renderWizard();
    pickFirstStarter(getByLabelText);

    // Quick-edit card appears; the full editor does NOT (it's behind "Open full
    // editor"). The starter's name is in the quick-edit name field.
    expect(bySlot(container, "quick-edit-card")).toBeTruthy();
    expect(container.querySelector('[data-slot="mock-editor"]')).toBeNull();
    expect(getByDisplayValue(firstStarter.name)).toBeTruthy();
    // The safety constraint shows pinned (a read-only line).
    const safety = getByDisplayValue(SAFETY_CONSTRAINT) as HTMLInputElement;
    expect(safety.readOnly).toBe(true);
  });

  it("creates DIRECTLY from quick-edit: guarded YAML → createPersona, no drafter", async () => {
    const { getByLabelText, container } = renderWizard();
    pickFirstStarter(getByLabelText);
    fireEvent.click(bySlot(container, "quick-create"));

    await waitFor(() => expect(createPersona).toHaveBeenCalledTimes(1));
    const yaml = createPersona.mock.calls[0][0] as string;
    expect(yaml).toContain(SAFETY_CONSTRAINT);
    expect(yaml).toContain(firstStarter.name);
    expect(author).not.toHaveBeenCalled();
  });

  it("carries quick edits into the full editor (Open full editor)", () => {
    const { getByLabelText, container } = renderWizard();
    pickFirstStarter(getByLabelText);

    // Edit the name in the quick-edit card, THEN open the full editor.
    const nameInput = bySlot(container, "quick-name") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Renamed Persona" } });
    fireEvent.click(bySlot(container, "quick-open-full"));

    // The full editor mounts with the EDITED doc (carry-over), no refinement.
    expect(bySlot(container, "mock-editor")).toBeTruthy();
    const doc = captured.props?.initialDoc as { identity: { name: string } };
    expect(doc.identity.name).toBe("Renamed Persona");
    expect(captured.props?.refinement).toBeUndefined();
  });
});

describe("AuthorWizard — start from scratch", () => {
  it("reveals an empty quick-edit draft with the safety constraint pinned", () => {
    const { container, getByDisplayValue } = renderWizard();
    fireEvent.click(bySlot(container, "author-wizard-scratch"));
    expect(bySlot(container, "quick-edit-card")).toBeTruthy();
    expect(getByDisplayValue(SAFETY_CONSTRAINT)).toBeTruthy();
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
