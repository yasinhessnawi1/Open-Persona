"use client";

import { Play } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useFormStatus } from "react-dom";
import { buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface PersonaOption {
  id: string;
  name: string;
}

function SubmitButton({ disabled }: { disabled: boolean }) {
  const { pending } = useFormStatus();
  const t = useTranslations("tasks");
  return (
    <button
      type="submit"
      disabled={pending || disabled}
      className={cn(buttonVariants(), "gap-2")}
    >
      <Play className="size-4" aria-hidden />
      {pending ? t("dispatching") : t("dispatch")}
    </button>
  );
}

/**
 * Spec 35 — the Tasks-page dispatch form. Pick a persona + describe a task →
 * server action POSTs the run → redirects to the run viewer. Mirrors
 * `StartRunForm` but adds the persona selector (the Tasks page is not bound to
 * a single persona).
 */
export function NewTaskForm({
  action,
  personas,
}: {
  action: (formData: FormData) => void | Promise<void>;
  personas: PersonaOption[];
}) {
  const t = useTranslations("tasks");
  const [task, setTask] = useState("");
  const [personaId, setPersonaId] = useState(personas[0]?.id ?? "");

  if (personas.length === 0) {
    return <p className="type-ui text-muted-foreground">{t("noPersonas")}</p>;
  }

  return (
    <form action={action} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="task-persona"
          className="type-caption font-medium text-muted-foreground"
        >
          {t("persona")}
        </label>
        <select
          id="task-persona"
          name="persona_id"
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {personas.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <Textarea
        name="task"
        value={task}
        onChange={(e) => setTask(e.target.value)}
        rows={2}
        placeholder={t("taskPlaceholder")}
        className="min-h-16 resize-none field-sizing-content"
      />
      <div className="flex justify-end">
        <SubmitButton disabled={!task.trim() || !personaId} />
      </div>
    </form>
  );
}
