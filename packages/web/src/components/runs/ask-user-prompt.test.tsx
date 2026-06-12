/**
 * Spec 21 T12 — <AskUserPrompt> 3+1 rendering (D-21-9).
 *
 * With `options`: three option buttons + a free-form field; clicking an option
 * submits its label. Without `options`: the pre-spec-21 free-text prompt
 * (back-compat). `allowFreeForm={false}` hides the free-form field.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { AskUserPrompt } from "./ask-user-prompt";

const MESSAGES = {
  runs: { answer: "Answer", answerPlaceholder: "Type your answer" },
};

function renderPrompt(props: Parameters<typeof AskUserPrompt>[0]) {
  return render(
    <NextIntlClientProvider locale="en" messages={MESSAGES}>
      <AskUserPrompt {...props} />
    </NextIntlClientProvider>,
  );
}

const OPTIONS = [
  { label: "Maintenance", description: "mould, leaks" },
  { label: "Deposit", description: "withheld" },
  { label: "Harassment" },
];

describe("AskUserPrompt — 3+1 options (D-21-9)", () => {
  it("renders three option buttons plus a free-form field", () => {
    renderPrompt({
      question: "What's the focus?",
      options: OPTIONS,
      onAnswer: vi.fn(),
    });
    expect(screen.getByText("Maintenance")).toBeInTheDocument();
    expect(screen.getByText("Deposit")).toBeInTheDocument();
    expect(screen.getByText("Harassment")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Type your answer")).toBeInTheDocument();
  });

  it("submits the option label when an option is clicked", async () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    renderPrompt({ question: "q", options: OPTIONS, onAnswer });
    fireEvent.click(screen.getByText("Deposit"));
    expect(onAnswer).toHaveBeenCalledWith("Deposit");
  });

  it("hides the free-form field when allowFreeForm is false", () => {
    renderPrompt({
      question: "q",
      options: OPTIONS,
      allowFreeForm: false,
      onAnswer: vi.fn(),
    });
    expect(
      screen.queryByPlaceholderText("Type your answer"),
    ).not.toBeInTheDocument();
  });
});

describe("AskUserPrompt — back-compat free-text (no options)", () => {
  it("renders only the free-text field when options are absent", () => {
    renderPrompt({ question: "Which apartment?", onAnswer: vi.fn() });
    expect(screen.getByPlaceholderText("Type your answer")).toBeInTheDocument();
    expect(screen.queryByText("Maintenance")).not.toBeInTheDocument();
  });

  it("submits the typed free-form answer", () => {
    const onAnswer = vi.fn().mockResolvedValue(undefined);
    renderPrompt({ question: "q", onAnswer });
    fireEvent.change(screen.getByPlaceholderText("Type your answer"), {
      target: { value: "the blue one" },
    });
    fireEvent.click(screen.getByText("Answer"));
    expect(onAnswer).toHaveBeenCalledWith("the blue one");
  });
});
