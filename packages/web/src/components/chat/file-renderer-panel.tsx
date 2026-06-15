"use client";

import { Check, Code2, Copy, Eye, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useAuthedArtifactText } from "@/lib/hooks/use-authed-artifact-text";
import { cn } from "@/lib/utils";
import { useFileRenderer } from "./file-renderer-context";
import { ArtifactView } from "./renderers";
import { isBinaryKind, rendererKindFor } from "./renderers/types";

type Mode = "rendered" | "raw";

const MODE_KEY_PREFIX = "persona.renderer.mode:";

function loadMode(workspacePath: string): Mode {
  if (typeof window === "undefined") return "rendered";
  return window.localStorage.getItem(MODE_KEY_PREFIX + workspacePath) === "raw"
    ? "raw"
    : "rendered";
}

/**
 * Spec 28 — the sliding right-panel renderer (D-28-5/6, §2.3). Reads the
 * conversation-scoped {@link useFileRenderer} state; persistent across messages,
 * closes on conversation change (fresh provider). Header: rendered/raw toggle
 * (eye / `</>`), copy, refresh, close. Esc closes (Sheet); Cmd/Ctrl+/ toggles.
 */
export function FileRendererPanel({ personaId }: { personaId: string }) {
  const t = useTranslations("chat.output.renderer");
  const { current, close } = useFileRenderer();
  const [mode, setMode] = useState<Mode>("rendered");
  const [refreshKey, setRefreshKey] = useState(0);
  const [copied, setCopied] = useState(false);

  const workspacePath = current?.workspacePath ?? "";
  const kind = current
    ? rendererKindFor(current.mediaType, current.name)
    : "plaintext";
  const binary = isBinaryKind(kind);

  // Per-file mode preference (localStorage), reloaded when the file changes.
  useEffect(() => {
    if (current) setMode(loadMode(current.workspacePath));
  }, [current]);

  const setModePersisted = useCallback(
    (next: Mode) => {
      setMode(next);
      if (typeof window !== "undefined" && current) {
        window.localStorage.setItem(
          MODE_KEY_PREFIX + current.workspacePath,
          next,
        );
      }
    },
    [current],
  );

  // Source text backs Copy (text formats only).
  const { text } = useAuthedArtifactText(
    personaId,
    binary ? "" : workspacePath,
  );

  // Cmd/Ctrl+/ toggles rendered↔raw (binary has no raw view).
  useEffect(() => {
    if (!current || binary) return;
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        setModePersisted(mode === "rendered" ? "raw" : "rendered");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, binary, mode, setModePersisted]);

  const onCopy = useCallback(() => {
    if (text === null) return;
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);

  if (!current) return null;

  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <SheetContent
        side="right"
        showCloseButton={false}
        className="w-full gap-0 p-0 sm:w-[45vw] sm:!max-w-3xl"
        data-slot="file-renderer-panel"
      >
        <header className="flex items-center gap-2 border-b border-border px-3 py-2">
          <SheetTitle
            className="min-w-0 flex-1 truncate type-ui"
            title={current.name}
          >
            {current.name}
          </SheetTitle>

          {!binary && (
            <div className="flex items-center rounded-md border border-border">
              <PanelButton
                active={mode === "rendered"}
                label={t("showRendered")}
                onClick={() => setModePersisted("rendered")}
              >
                <Eye className="size-4" aria-hidden />
              </PanelButton>
              <PanelButton
                active={mode === "raw"}
                label={t("showRaw")}
                onClick={() => setModePersisted("raw")}
              >
                <Code2 className="size-4" aria-hidden />
              </PanelButton>
            </div>
          )}

          {!binary && (
            <PanelButton
              label={copied ? t("copied") : t("copy")}
              onClick={onCopy}
            >
              {copied ? (
                <Check className="size-4" aria-hidden />
              ) : (
                <Copy className="size-4" aria-hidden />
              )}
            </PanelButton>
          )}
          <PanelButton
            label={t("refresh")}
            onClick={() => setRefreshKey((k) => k + 1)}
          >
            <RefreshCw className="size-4" aria-hidden />
          </PanelButton>
          <PanelButton label={t("close")} onClick={close}>
            <span aria-hidden className="text-base leading-none">
              ✕
            </span>
          </PanelButton>
        </header>

        <div className="min-h-0 flex-1 overflow-auto">
          <ArtifactView
            key={`${current.workspacePath}:${mode}:${refreshKey}`}
            kind={kind}
            mode={mode}
            personaId={personaId}
            workspacePath={current.workspacePath}
            mediaType={current.mediaType}
          />
        </div>
      </SheetContent>
    </Sheet>
  );
}

function PanelButton({
  children,
  label,
  active,
  onClick,
}: {
  children: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={active}
      className={cn(
        "grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground",
        "hover:bg-muted hover:text-foreground",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        active && "bg-muted text-foreground",
      )}
    >
      {children}
    </button>
  );
}
