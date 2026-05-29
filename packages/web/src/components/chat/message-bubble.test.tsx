import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageBubble } from "./message-bubble";

describe("MessageBubble", () => {
  it("renders a completed assistant message as Markdown", () => {
    const { container } = render(
      <MessageBubble
        message={{
          id: "1",
          role: "assistant",
          content: "**bold** and `code`",
          streaming: false,
        }}
      />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("code")?.textContent).toBe("code");
    // The raw markers must not survive as literal text.
    expect(container.textContent).not.toContain("**");
  });

  it("renders a fenced code block in a completed assistant message", () => {
    const { container } = render(
      <MessageBubble
        message={{
          id: "2",
          role: "assistant",
          content: "```python\nprint('hi')\n```",
          streaming: false,
        }}
      />,
    );
    expect(container.querySelector("pre code")).toBeTruthy();
    expect(container.textContent).toContain("print('hi')");
  });

  it("shows raw text (no Markdown) while streaming", () => {
    const { container } = render(
      <MessageBubble
        message={{
          id: "3",
          role: "assistant",
          content: "**bold**",
          streaming: true,
        }}
      />,
    );
    expect(container.querySelector("strong")).toBeNull();
    expect(container.textContent).toContain("**bold**");
  });

  it("renders user messages as plain text (never Markdown)", () => {
    const { container } = render(
      <MessageBubble
        message={{ id: "4", role: "user", content: "**not bold**" }}
      />,
    );
    expect(container.querySelector("strong")).toBeNull();
    expect(container.textContent).toContain("**not bold**");
  });
});
