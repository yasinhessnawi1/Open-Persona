"use client";

import { Plus, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
// Spec V6 C2 — the voice-selector contribution (F5/Spec 10 own the screen).
import { VoiceSelector } from "@/components/voice/voice-selector";
import {
  EPISTEMIC_OPTIONS,
  type PersonaDoc,
  readIdentity,
  readSelfFacts,
  readStringList,
  readWorldview,
  writeIdentityField,
  writeSelfFacts,
  writeStringList,
  writeWorldview,
} from "@/lib/persona-draft";
import { cn } from "@/lib/utils";

// The structured persona editor (T08). Controlled: it renders from `doc` and
// emits a new `doc` on every edit. The parent keeps the YAML buffer in sync.
export function PersonaForm({
  doc,
  onChange,
  tools,
  skills,
}: {
  doc: PersonaDoc;
  onChange: (doc: PersonaDoc) => void;
  tools: string[];
  skills: string[];
}) {
  const t = useTranslations("author");
  const identity = readIdentity(doc);
  // The persona's current voice id (identity.voice.voice_id), if set — V6 C2.
  const identityRecord = doc.identity as Record<string, unknown> | undefined;
  const voiceRecord = identityRecord?.voice as
    | { voice_id?: unknown }
    | null
    | undefined;
  const currentVoiceId =
    voiceRecord && typeof voiceRecord.voice_id === "string"
      ? voiceRecord.voice_id
      : null;
  const selfFacts = readSelfFacts(doc);
  const worldview = readWorldview(doc);
  const declaredTools = readStringList(doc, "tools");
  const declaredSkills = readStringList(doc, "skills");

  return (
    <div className="flex flex-col gap-5">
      {/* Identity */}
      <Section title={t("identityTitle")}>
        <Field label={t("name")}>
          <Input
            value={identity.name}
            onChange={(e) =>
              onChange(writeIdentityField(doc, "name", e.target.value))
            }
          />
        </Field>
        <Field label={t("role")}>
          <Input
            value={identity.role}
            onChange={(e) =>
              onChange(writeIdentityField(doc, "role", e.target.value))
            }
          />
        </Field>
        <Field label={t("background")}>
          <Textarea
            value={identity.background}
            rows={4}
            onChange={(e) =>
              onChange(writeIdentityField(doc, "background", e.target.value))
            }
          />
        </Field>
        <Field label={t("language")}>
          <Input
            value={identity.language_default}
            className="max-w-28"
            onChange={(e) =>
              onChange(
                writeIdentityField(doc, "language_default", e.target.value),
              )
            }
          />
        </Field>
        <Field label={t("constraints")}>
          <ListEditor
            items={identity.constraints}
            placeholder={t("constraintPlaceholder")}
            addLabel={t("addConstraint")}
            onChange={(list) =>
              onChange(writeIdentityField(doc, "constraints", list))
            }
          />
        </Field>
        <Field label={t("voice")}>
          <VoiceSelector
            value={currentVoiceId}
            onChange={(voice) =>
              onChange({
                ...doc,
                identity: {
                  ...((doc.identity ?? {}) as Record<string, unknown>),
                  voice,
                },
              })
            }
          />
        </Field>
      </Section>

      {/* Self-facts */}
      <Section title={t("selfFactsTitle")}>
        {selfFacts.map((f, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
          <div key={i} className="flex items-start gap-2">
            <Input
              value={f.fact}
              placeholder={t("fact")}
              className="flex-1"
              onChange={(e) =>
                onChange(
                  writeSelfFacts(
                    doc,
                    selfFacts.map((x, j) =>
                      j === i ? { ...x, fact: e.target.value } : x,
                    ),
                  ),
                )
              }
            />
            <Confidence
              value={f.confidence}
              onChange={(v) =>
                onChange(
                  writeSelfFacts(
                    doc,
                    selfFacts.map((x, j) =>
                      j === i ? { ...x, confidence: v } : x,
                    ),
                  ),
                )
              }
            />
            <RemoveButton
              label={t("remove")}
              onClick={() =>
                onChange(
                  writeSelfFacts(
                    doc,
                    selfFacts.filter((_, j) => j !== i),
                  ),
                )
              }
            />
          </div>
        ))}
        <AddButton
          label={t("addSelfFact")}
          onClick={() =>
            onChange(
              writeSelfFacts(doc, [...selfFacts, { fact: "", confidence: 1 }]),
            )
          }
        />
      </Section>

      {/* Worldview */}
      <Section title={t("worldviewTitle")}>
        {worldview.map((w, i) => {
          const set = (patch: Partial<typeof w>) =>
            onChange(
              writeWorldview(
                doc,
                worldview.map((x, j) => (j === i ? { ...x, ...patch } : x)),
              ),
            );
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
            <div key={i} className="flex flex-col gap-2 rounded-md border p-3">
              <div className="flex items-start gap-2">
                <Input
                  value={w.claim}
                  placeholder={t("claim")}
                  className="flex-1"
                  onChange={(e) => set({ claim: e.target.value })}
                />
                <RemoveButton
                  label={t("remove")}
                  onClick={() =>
                    onChange(
                      writeWorldview(
                        doc,
                        worldview.filter((_, j) => j !== i),
                      ),
                    )
                  }
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  value={w.domain}
                  placeholder={t("domain")}
                  className="w-32"
                  onChange={(e) => set({ domain: e.target.value })}
                />
                <select
                  value={w.epistemic}
                  onChange={(e) => set({ epistemic: e.target.value })}
                  className="h-9 rounded-md border border-input bg-transparent px-2 font-mono text-xs uppercase shadow-xs"
                >
                  {EPISTEMIC_OPTIONS.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
                <Input
                  value={w.valid_time}
                  placeholder={t("validTime")}
                  className="w-28"
                  onChange={(e) => set({ valid_time: e.target.value })}
                />
                <Confidence
                  value={w.confidence}
                  onChange={(v) => set({ confidence: v })}
                />
              </div>
            </div>
          );
        })}
        <AddButton
          label={t("addClaim")}
          onClick={() =>
            onChange(
              writeWorldview(doc, [
                ...worldview,
                {
                  claim: "",
                  domain: "",
                  epistemic: "belief",
                  confidence: 0.8,
                  valid_time: "always",
                },
              ]),
            )
          }
        />
      </Section>

      {/* Tools + skills */}
      <div className="grid gap-5 sm:grid-cols-2">
        <Section title={t("toolsTitle")}>
          <ChipToggle
            available={tools}
            selected={declaredTools}
            empty={t("noTools")}
            onChange={(list) => onChange(writeStringList(doc, "tools", list))}
          />
        </Section>
        <Section title={t("skillsTitle")}>
          <ChipToggle
            available={skills}
            selected={declaredSkills}
            empty={t("noSkills")}
            onChange={(list) => onChange(writeStringList(doc, "skills", list))}
          />
        </Section>
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="gap-3 p-5">
      <h2 className="font-heading text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h2>
      {children}
    </Card>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the form control is passed as children
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Confidence({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-20 accent-primary"
        aria-label="confidence"
      />
      <span className="w-8 font-mono text-xs text-muted-foreground tabular-nums">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

function ListEditor({
  items,
  placeholder,
  addLabel,
  onChange,
}: {
  items: string[];
  placeholder: string;
  addLabel: string;
  onChange: (list: string[]) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      {items.map((item, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
        <div key={i} className="flex items-center gap-2">
          <Input
            value={item}
            placeholder={placeholder}
            className="flex-1"
            onChange={(e) =>
              onChange(items.map((x, j) => (j === i ? e.target.value : x)))
            }
          />
          <RemoveButton
            label="remove"
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          />
        </div>
      ))}
      <AddButton label={addLabel} onClick={() => onChange([...items, ""])} />
    </div>
  );
}

function ChipToggle({
  available,
  selected,
  empty,
  onChange,
}: {
  available: string[];
  selected: string[];
  empty: string;
  onChange: (list: string[]) => void;
}) {
  if (available.length === 0) {
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {available.map((name) => {
        const on = selected.includes(name);
        return (
          <button
            key={name}
            type="button"
            onClick={() =>
              onChange(
                on ? selected.filter((x) => x !== name) : [...selected, name],
              )
            }
            className={cn(
              "rounded border px-2 py-1 font-mono text-xs transition-colors",
              on
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:border-primary/30",
            )}
          >
            {name}
          </button>
        );
      })}
    </div>
  );
}

function AddButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex w-fit items-center gap-1.5 text-sm text-primary hover:underline"
    >
      <Plus className="size-3.5" />
      {label}
    </button>
  );
}

function RemoveButton({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="mt-1 shrink-0 text-muted-foreground hover:text-destructive"
    >
      <X className="size-4" />
    </button>
  );
}
