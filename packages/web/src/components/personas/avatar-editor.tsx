"use client";

import { useAuth } from "@clerk/nextjs";
import { ImageUp } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";
import { PersonaAvatar } from "@/components/persona/persona-avatar";
import { buttonVariants } from "@/components/ui/button";
import { uploadImage } from "@/lib/upload";
import { cn } from "@/lib/utils";

/**
 * AvatarEditor — show + replace a persona's avatar on the edit page.
 *
 * The persona auto-generates an avatar at creation (Spec 29); here the owner can
 * replace it with an uploaded image (a user-supplied avatar always wins). The
 * upload goes to the shared `POST /v1/personas/:id/uploads`; the returned
 * workspace ref is reported up via `onChange` and persisted on the editor's Save
 * (the persona PATCH carries `avatar_url`). The preview updates immediately.
 */
export function AvatarEditor({
  personaId,
  name,
  avatarUrl,
  onChange,
}: {
  personaId: string;
  name: string;
  avatarUrl: string | null;
  onChange: (workspacePath: string) => void;
}) {
  const t = useTranslations("author");
  const { getToken } = useAuth();
  const inputId = useId();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setUploading(true);
    setError(null);
    try {
      const res = await uploadImage(personaId, file, {
        getToken: () => getToken(),
      });
      onChange(res.workspace_path);
    } catch {
      setError(t("avatarUploadError"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div
      className="flex items-center gap-4 rounded-lg border bg-card p-4"
      data-slot="avatar-editor"
    >
      <PersonaAvatar
        persona={{ id: personaId, name, avatar_url: avatarUrl }}
        size="lg"
      />
      <div className="flex min-w-0 flex-col gap-1.5">
        <label
          htmlFor={inputId}
          className={cn(
            buttonVariants({ variant: "outline", size: "sm" }),
            "w-fit cursor-pointer gap-1.5",
          )}
        >
          <ImageUp className="size-4" aria-hidden />
          {uploading ? t("avatarUploading") : t("avatarReplace")}
        </label>
        <input
          id={inputId}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          disabled={uploading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Reset so re-selecting the same file fires change again.
            e.target.value = "";
            if (file) void handleFile(file);
          }}
        />
        <p className="type-caption text-muted-foreground">{t("avatarHint")}</p>
        {error ? (
          <p className="type-caption text-destructive" data-slot="avatar-error">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}
