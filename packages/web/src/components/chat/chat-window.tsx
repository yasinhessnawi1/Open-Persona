"use client";

import { useAuth } from "@clerk/nextjs";
import { ArrowUp } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "@/components/patterns/toast";
import type { AvatarPersona } from "@/components/persona/persona-avatar";
import { buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ApiError } from "@/lib/api/client";
import { removeDocument } from "@/lib/document-actions";
import { useChat } from "@/lib/hooks/use-chat";
import { useConversationDocuments } from "@/lib/hooks/use-conversation-documents";
import { cn } from "@/lib/utils";
import { ComposerAttachControl } from "./composer/attach-control";
import {
  attachmentsBlockSend,
  type ValidationReason,
  validateBeforeUpload,
} from "./composer/attach-state";
import { ConversationDocumentList } from "./composer/conversation-document-list";
import { ComposerImagePreview } from "./composer/image-preview";
import { NoVisionErrorBanner } from "./composer/no-vision-error-banner";
import { useDragTarget, usePasteImage } from "./composer/use-attach-non-click";
import { useComposerAttachments } from "./composer/use-composer-attachments";
import { surfaceValidationFailure } from "./composer/validation-toast";
import { type ChatMessageView, MessageElement } from "./message-element";

const TEMPLATE = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

/**
 * F3 (T19) — strangler-fig composer wiring.
 *
 * Extends F2's ChatWindow with the file-attach surface: attach control +
 * image preview tray + conversation document panel + at-send no-vision
 * safety banner. The existing send/streaming/auto-scroll plumbing
 * (`useChat`, `MessageElement` per-message render, the textarea form)
 * is UNCHANGED — F3 ADDS adjacent surfaces, not refactors.
 *
 * **Decision touchpoints honoured:**
 *   - D-F3-X-capabilities-prop-drill-shape: `capabilities` arrives as a
 *     plain prop from the page (no context, no global). Prop chain is
 *     PersonaDetail → ChatPage → ChatWindow → ComposerAttachControl.
 *   - D-F3-X-document-attach-conversation-binding: `documentsDisabled` is
 *     always `false` here because ChatWindow is mounted only inside a
 *     conversation context. The persona-detail surface (which lacks a
 *     conversationId) never mounts ChatWindow.
 *   - D-F3-X-cap-attached-state-on-conversation-switch: enforced inside
 *     `useComposerAttachments` (sole-dep `[conversationId]` reset).
 *   - D-F3-X-partial-upload-failure-shape: `attachmentsBlockSend` predicate
 *     disables the send button while any image is uploading/error.
 *   - D-F3-X-no-vision-surface-shape: (a) attach disabled via
 *     `imageAttachDisabled`; (c) at-send `<NoVisionErrorBanner>` surfaces
 *     the runtime refusal.
 */
export interface PersonaCapabilities {
  vision: boolean;
  configured_tiers: readonly string[];
}

export function ChatWindow({
  conversationId,
  persona,
  initialMessages,
  capabilities,
}: {
  conversationId: string;
  persona: AvatarPersona;
  initialMessages: ChatMessageView[];
  /** Deployment capabilities surfaced by Spec 08 PersonaDetail (T02). */
  capabilities?: PersonaCapabilities | null;
}) {
  const t = useTranslations("chat");
  const toast = useToast();
  const { getToken } = useAuth();
  const { messages, streaming, error, send } = useChat(
    conversationId,
    initialMessages,
  );
  const [input, setInput] = useState("");
  const [sendError, setSendError] = useState<ApiError | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Conversation-scoped document state (separate slice from message-scoped
  // image attachments — D-F3-X-cap-attached-state-on-conversation-switch
  // explicitly notes the two lifecycles are distinct).
  const documents = useConversationDocuments(conversationId);

  // Image-attachment state + upload orchestration (T13 hook).
  // F3 follow-up — surface a success toast on document attach so the user
  // sees confirmation of "{filename} added to this conversation" (the chip
  // appearing in the panel is the persistent signal, but the toast makes
  // the per-message action visible — addresses the "did it do anything?"
  // ambiguity surfaced during Phase 6 operator testing).
  const attach = useComposerAttachments({
    conversationId,
    personaId: persona.id,
    onDocumentAttached: (ref) => {
      documents.addOptimistic(ref);
      toast.success(
        t("composer.attach.feedback.documentAttached", {
          filename: ref.filename,
        }),
      );
    },
    onDocumentError: (detail) => toast.error(detail),
  });

  // D-F3-X-no-vision-surface-shape (a): attach disabled when the
  // deployment has no vision tier configured. `null` (capabilities not
  // wired) leaves attach enabled — server stays authoritative per (c).
  const imageAttachDisabled = capabilities?.vision === false;

  const handleReject = useCallback(
    (reason: ValidationReason, detail: string) => {
      surfaceValidationFailure(reason, detail, toast, t);
    },
    [toast, t],
  );

  // T08 — drag-and-drop + paste handlers (desktop-only enhancements).
  useDragTarget(dropZoneRef, {
    onFiles: (files) => {
      for (const f of files) {
        const result = validateBeforeUpload(f, attach.attachedImages.length);
        if (!result.ok) {
          handleReject(result.reason, result.detail);
          continue;
        }
        if (result.kind === "image") {
          if (imageAttachDisabled) {
            handleReject(
              "unsupported_format",
              t("composer.attach.imageDisabled"),
            );
            continue;
          }
          attach.attachImage(f);
        } else {
          void attach.uploadDocumentFile(f);
        }
      }
    },
    onReject: (detail) => toast.error(detail),
  });
  usePasteImage(textareaRef, {
    onFile: (file) => {
      if (imageAttachDisabled) {
        handleReject("unsupported_format", t("composer.attach.imageDisabled"));
        return;
      }
      attach.attachImage(file);
    },
  });

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on new messages
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function submit() {
    const value = input.trim();
    if (!value || streaming) return;
    if (attachmentsBlockSend(attach.attachedImages)) return;

    // Collect ImageRef[] from the success-state attachments. The mediaType
    // came from the upload-service response (Spec 13 always returns one of
    // the 4 supported MIME types per the route's content-type dispatch),
    // so the literal-union cast is safe.
    const refs = attach.attachedImages
      .filter((a) => a.state === "success")
      .map((a) => {
        const success = a as { workspacePath: string; mediaType: string };
        return {
          workspace_path: success.workspacePath,
          media_type: success.mediaType as
            | "image/png"
            | "image/jpeg"
            | "image/webp"
            | "image/gif",
        };
      });

    setInput("");
    setSendError(null);
    try {
      await send(value, refs);
      // Successful send → clear attached images (message-scoped lifecycle).
      attach.clearImages();
    } catch (e) {
      // useChat already surfaces error state via its own setError; catch
      // ApiError specifically so the NoVision banner can pattern-match.
      if (e && typeof e === "object" && "status" in e) {
        setSendError(e as ApiError);
      }
    }
  }

  const sendBlocked =
    streaming || !input.trim() || attachmentsBlockSend(attach.attachedImages);

  const onDocumentRemove = useCallback(
    async (docRef: string) => {
      documents.removeOptimistic(docRef);
      try {
        await removeDocument(
          conversationId,
          docRef,
          async () =>
            (await getToken(TEMPLATE ? { template: TEMPLATE } : undefined)) ??
            "",
        );
      } catch {
        // On failure, refresh to reconcile with server state.
        void documents.refresh();
      }
    },
    [conversationId, documents, getToken],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col" ref={dropZoneRef}>
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

      {/* F3: NoVision at-send safety banner (D-F3-X-no-vision-surface-shape (c)). */}
      <NoVisionErrorBanner
        error={sendError}
        onDismiss={() => {
          setSendError(null);
          attach.clearImages();
        }}
      />

      {/* F3: conversation-scoped document panel. */}
      <ConversationDocumentList
        documents={documents.documents}
        onRemove={(docRef) => void onDocumentRemove(docRef)}
        className="mx-auto w-full max-w-2xl px-4"
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="border-t bg-background/80 backdrop-blur"
      >
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 px-4 py-3">
          {/* F3: composer image preview tray (T09). */}
          {attach.attachedImages.length > 0 ? (
            <div
              className="flex flex-wrap gap-2"
              data-slot="composer-image-tray"
            >
              {attach.attachedImages.map((a) => (
                <ComposerImagePreview
                  key={a.id}
                  attachment={a}
                  onRemove={attach.removeImage}
                />
              ))}
            </div>
          ) : null}

          <div className="flex items-end gap-2">
            {/* F3 T07 — attach control. documentsDisabled is always false
                here (we're inside a conversation context per
                D-F3-X-document-attach-conversation-binding). */}
            <ComposerAttachControl
              onImageFile={attach.attachImage}
              onDocumentFile={(file) => void attach.uploadDocumentFile(file)}
              onReject={handleReject}
              currentImageCount={attach.attachedImages.length}
              imageAttachDisabled={imageAttachDisabled}
              documentsDisabled={false}
            />
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit();
                }
              }}
              placeholder={t("placeholder", { name: persona.name })}
              rows={1}
              className="max-h-40 min-h-10 flex-1 resize-none field-sizing-content"
            />
            <button
              type="submit"
              disabled={sendBlocked}
              aria-label={t("send")}
              className={cn(buttonVariants({ size: "icon" }))}
            >
              <ArrowUp className="size-4" />
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
