import { Markdown } from "@/components/ui/markdown";
import { TierBadge } from "./tier-badge";
import { ToolCallCard, type ToolEntry } from "./tool-call-card";

export interface ChatMessageView {
  id: string;
  role: string;
  content: string;
  tier?: string;
  tools?: ToolEntry[];
  streaming?: boolean;
}

export function MessageBubble({ message }: { message: ChatMessageView }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-secondary px-4 py-2.5 text-sm whitespace-pre-wrap text-secondary-foreground">
          {message.content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {message.tools && message.tools.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          {message.tools.map((tool, i) => (
            <ToolCallCard key={`${tool.toolName}-${i}`} entry={tool} />
          ))}
        </div>
      ) : null}
      {message.content || message.streaming ? (
        message.streaming ? (
          // Plain text while streaming — avoids half-typed `**`/unclosed ``` fences
          // flickering; the caret stays inline. Renders to Markdown once complete.
          <div className="text-sm leading-relaxed whitespace-pre-wrap">
            {message.content}
            <span className="ml-0.5 inline-block h-4 w-[3px] translate-y-0.5 animate-pulse rounded-full bg-primary" />
          </div>
        ) : (
          <div className="text-sm leading-relaxed">
            <Markdown>{message.content}</Markdown>
          </div>
        )
      ) : null}
      {message.tier && !message.streaming ? (
        <TierBadge tier={message.tier} />
      ) : null}
    </div>
  );
}
