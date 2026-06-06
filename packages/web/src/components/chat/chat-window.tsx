"use client";

import { ArrowUp } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import type { AvatarPersona } from "@/components/persona/persona-avatar";
import { buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChat } from "@/lib/hooks/use-chat";
import { cn } from "@/lib/utils";
import { type ChatMessageView, MessageElement } from "./message-element";

/**
 * T26: chat window swaps the scaffold's <MessageBubble> for <MessageElement>
 * (T15 + D-F2-15 interleaved layout). The `useChat` plumbing + SSE consumption
 * + composer textarea + auto-scroll behaviour stay verbatim per the strangler-
 * fig discipline; only the per-message rendering changes.
 */
export function ChatWindow({
  conversationId,
  persona,
  initialMessages,
}: {
  conversationId: string;
  persona: AvatarPersona;
  initialMessages: ChatMessageView[];
}) {
  const t = useTranslations("chat");
  const { messages, streaming, error, send } = useChat(
    conversationId,
    initialMessages,
  );
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on new messages
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function submit() {
    const value = input.trim();
    if (!value || streaming) return;
    setInput("");
    void send(value);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-5 px-4 py-6">
          {messages.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {t("empty")}
            </p>
          ) : null}
          {messages.map((m, i) => (
            <MessageElement
              key={m.id}
              message={m}
              persona={persona}
              prevMessage={i > 0 ? messages[i - 1] : undefined}
            />
          ))}
          {error ? (
            <p className="text-sm text-destructive">{t("error")}</p>
          ) : null}
          <div ref={endRef} />
        </div>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="border-t bg-background/80 backdrop-blur"
      >
        <div className="mx-auto flex w-full max-w-2xl items-end gap-2 px-4 py-3">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={t("placeholder", { name: persona.name })}
            rows={1}
            className="max-h-40 min-h-10 flex-1 resize-none field-sizing-content"
          />
          <button
            type="submit"
            disabled={streaming || !input.trim()}
            aria-label={t("send")}
            className={cn(buttonVariants({ size: "icon" }))}
          >
            <ArrowUp className="size-4" />
          </button>
        </div>
      </form>
    </div>
  );
}
