"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  presetToWeights,
  ROUTING_PRESET_ORDER,
  type RoutingPreset,
  type RoutingView,
  weightsToPreset,
} from "@/lib/persona-draft";
import { cn } from "@/lib/utils";
import { CollapsibleSection } from "./collapsible-section";

/**
 * RoutingSection (Spec 31, T2) — the routing-controls surface in the persona
 * editor. Controlled/presentational: it renders from a {@link RoutingView} and
 * emits a new one on every edit; the parent owns persistence (the persona YAML
 * PATCH via persona-draft's writeRouting).
 *
 * Non-experts pick an intent PRESET (cost / balanced / quality / speed, D-31-3)
 * that maps to the underlying cost/quality/latency weights; raw weights live
 * behind an Advanced disclosure that auto-opens when the stored vector matches
 * no preset ("Custom"). Budget caps are explicit cents inputs; a blank input is
 * "unset" (null), never 0 (D-31-X-empty-cap-input). The per-day cap carries the
 * Spec-23 fail-loud warning (it isn't enforced yet — D-31-2).
 */
export function RoutingSection({
  value,
  onChange,
}: {
  value: RoutingView;
  onChange: (next: RoutingView) => void;
}) {
  const t = useTranslations("author.routing");
  const preset = weightsToPreset(value.weights);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const advancedOpen = showAdvanced || preset === "custom";

  return (
    <CollapsibleSection
      id="routing"
      title={t("title")}
      headerAccessory={
        <button
          type="button"
          role="switch"
          aria-checked={value.intelligentEnabled}
          aria-label={t("enableLabel")}
          onClick={() =>
            onChange({
              ...value,
              intelligentEnabled: !value.intelligentEnabled,
            })
          }
          className={cn(
            buttonVariants({
              variant: value.intelligentEnabled ? "default" : "outline",
              size: "sm",
            }),
          )}
          data-slot="routing-enable-switch"
        >
          {value.intelligentEnabled ? t("on") : t("off")}
        </button>
      }
    >
      <p
        className="type-caption text-muted-foreground"
        data-slot="routing-section"
      >
        {t("enableHint")}
      </p>

      {value.intelligentEnabled ? (
        <div className="flex flex-col gap-4" data-slot="routing-config">
          {/* Intent presets */}
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              {t("priorityLabel")}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {ROUTING_PRESET_ORDER.map((p) => (
                <PresetChip
                  key={p}
                  preset={p}
                  label={t(presetLabelKey(p))}
                  title={t(presetHintKey(p))}
                  active={preset === p}
                  onClick={() =>
                    onChange({ ...value, weights: presetToWeights(p) })
                  }
                />
              ))}
              {preset === "custom" ? (
                <span
                  className="rounded border border-primary/40 bg-primary/10 px-2 py-1 font-mono text-xs text-primary"
                  data-slot="routing-preset-custom"
                  title={t("presetCustomHint")}
                >
                  {t("presetCustom")}
                </span>
              ) : null}
            </div>
          </div>

          {/* Advanced raw weights */}
          <div className="flex flex-col gap-2">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={advancedOpen}
              className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
              data-slot="routing-advanced-toggle"
            >
              {t("advanced")}
            </button>
            {advancedOpen ? (
              <div
                className="flex flex-col gap-2 rounded-md border p-3"
                data-slot="routing-weights"
              >
                <WeightSlider
                  label={t("weightCost")}
                  value={value.weights.cost}
                  onChange={(cost) =>
                    onChange({ ...value, weights: { ...value.weights, cost } })
                  }
                />
                <WeightSlider
                  label={t("weightQuality")}
                  value={value.weights.quality}
                  onChange={(quality) =>
                    onChange({
                      ...value,
                      weights: { ...value.weights, quality },
                    })
                  }
                />
                <WeightSlider
                  label={t("weightLatency")}
                  value={value.weights.latency}
                  onChange={(latency) =>
                    onChange({
                      ...value,
                      weights: { ...value.weights, latency },
                    })
                  }
                />
              </div>
            ) : null}
          </div>

          {/* Budget caps */}
          <div className="flex flex-col gap-2" data-slot="routing-budget">
            <span className="text-xs font-medium text-muted-foreground">
              {t("budgetTitle")}
            </span>
            <p className="type-caption text-muted-foreground">
              {t("budgetHint")}
            </p>
            <CapInput
              label={t("perTurn")}
              hint={t("perTurnHint")}
              placeholder={t("capPlaceholder")}
              unit={t("cents")}
              value={value.budget.maxCentsPerTurn}
              onChange={(c) =>
                onChange({
                  ...value,
                  budget: { ...value.budget, maxCentsPerTurn: c },
                })
              }
            />
            <CapInput
              label={t("perSession")}
              hint={t("perSessionHint")}
              placeholder={t("capPlaceholder")}
              unit={t("cents")}
              value={value.budget.maxCentsPerSession}
              onChange={(c) =>
                onChange({
                  ...value,
                  budget: { ...value.budget, maxCentsPerSession: c },
                })
              }
            />
            <CapInput
              label={t("perDay")}
              hint={t("perDay")}
              placeholder={t("capPlaceholder")}
              unit={t("cents")}
              value={value.budget.maxCentsPerDay}
              onChange={(c) =>
                onChange({
                  ...value,
                  budget: { ...value.budget, maxCentsPerDay: c },
                })
              }
            />
            {value.budget.maxCentsPerDay !== null ? (
              <p
                className="type-caption rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-muted-foreground"
                data-slot="routing-perday-warning"
              >
                {t("perDayWarning")}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </CollapsibleSection>
  );
}

function presetLabelKey(p: RoutingPreset): string {
  return `preset${p[0].toUpperCase()}${p.slice(1)}`;
}
function presetHintKey(p: RoutingPreset): string {
  return `preset${p[0].toUpperCase()}${p.slice(1)}Hint`;
}

function PresetChip({
  preset,
  label,
  title,
  active,
  onClick,
}: {
  preset: RoutingPreset;
  label: string;
  title: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      title={title}
      onClick={onClick}
      className={cn(
        "rounded border px-2 py-1 font-mono text-xs transition-colors",
        active
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:border-primary/30",
      )}
      data-slot="routing-preset"
      data-preset={preset}
      data-active={active}
    >
      {label}
    </button>
  );
}

function WeightSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-3">
      <span className="w-16 shrink-0 text-xs text-muted-foreground">
        {label}
      </span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 accent-primary"
        aria-label={label}
      />
      <span className="w-9 text-right font-mono text-xs text-muted-foreground tabular-nums">
        {value.toFixed(2)}
      </span>
    </label>
  );
}

/** Parse a cents input: blank ⇒ null (unset), valid non-negative ⇒ that number. */
function parseCap(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const n = Number.parseFloat(trimmed);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function CapInput({
  label,
  hint,
  placeholder,
  unit,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  placeholder: string;
  unit: string;
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the Input is nested in the label
    <label className="flex items-center justify-between gap-3">
      <span className="flex flex-col">
        <span className="text-xs font-medium">{label}</span>
        <span className="type-caption text-muted-foreground">{hint}</span>
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        <Input
          type="number"
          min={0}
          step={0.1}
          inputMode="decimal"
          placeholder={placeholder}
          value={value === null ? "" : String(value)}
          onChange={(e) => onChange(parseCap(e.target.value))}
          className="w-24 text-right font-mono tabular-nums"
          aria-label={`${label} (${unit})`}
        />
        <span className="w-10 text-xs text-muted-foreground">{unit}</span>
      </span>
    </label>
  );
}
