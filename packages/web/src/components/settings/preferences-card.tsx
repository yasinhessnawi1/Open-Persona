"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { LOCALE_COOKIE } from "@/i18n/config";
import { TIER_BADGE_SETTING, useBoolSetting } from "@/lib/hooks/use-setting";
import { cn } from "@/lib/utils";

/**
 * Spec F2 T31 — PreferencesCard (retokenised).
 *
 * DO NOT TOUCH (per audit.md §settings.plumbing):
 *   - `useTheme` + `useBoolSetting(TIER_BADGE_SETTING, true)` + `LOCALE_COOKIE`;
 *   - the `mounted` SSR-gate (no hydration warning);
 *   - the inline `<Switch>` thumb `left-[1.125rem]` positional pixel (the
 *     audit notes it as switch-thumb positional, not a design value).
 *
 * REPLACED:
 *   - section h2 `font-heading text-sm tracking-wide uppercase` →
 *     `.type-caption font-mono uppercase`;
 *   - Row label `text-sm font-medium` → `.type-body font-medium`;
 *   - Row hint `text-xs text-muted-foreground` → `.type-caption text-muted-foreground`;
 *   - Theme/language toggle buttons `text-xs` → `.type-caption`.
 */
const THEMES = [
  { value: "light", icon: Sun },
  { value: "dark", icon: Moon },
  { value: "system", icon: Monitor },
] as const;

export function PreferencesCard() {
  const t = useTranslations("settings");
  const tTheme = useTranslations("theme");
  const { theme, setTheme } = useTheme();
  const [tierVisible, setTierVisible] = useBoolSetting(
    TIER_BADGE_SETTING,
    true,
  );
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <Card className="gap-4 p-5" data-slot="settings-preferences">
      <h2 className="type-caption font-mono text-muted-foreground uppercase">
        {t("preferences")}
      </h2>

      <Row label={t("theme")} hint={t("themeHint")}>
        <div className="flex gap-1 rounded-md border p-0.5">
          {THEMES.map(({ value, icon: Icon }) => {
            const active = mounted && theme === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setTheme(value)}
                aria-pressed={active}
                className={cn(
                  "type-caption inline-flex items-center gap-1.5 rounded px-2.5 py-1",
                  active
                    ? "bg-secondary text-secondary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
                data-slot="settings-theme-option"
              >
                <Icon className="size-3.5" aria-hidden="true" />
                {tTheme(value)}
              </button>
            );
          })}
        </div>
      </Row>

      <Row label={t("tierBadges")} hint={t("tierBadgesHint")}>
        <Switch
          checked={tierVisible}
          onChange={setTierVisible}
          label={t("tierBadges")}
        />
      </Row>

      <Row label={t("language")} hint={t("languageHint")}>
        <LanguageToggle mounted={mounted} />
      </Row>
    </Card>
  );
}

function LanguageToggle({ mounted }: { mounted: boolean }) {
  const t = useTranslations("settings");
  const current =
    mounted && document.cookie.includes(`${LOCALE_COOKIE}=xx`) ? "xx" : "en";
  const choose = (value: "en" | "xx") => {
    // biome-ignore lint/suspicious/noDocumentCookie: portable write (cookieStore is Chromium-only)
    document.cookie = `${LOCALE_COOKIE}=${value}; path=/; max-age=31536000`;
    window.location.reload();
  };
  const langs = [
    { value: "en", label: t("langEnglish") },
    { value: "xx", label: t("langPseudo") },
  ] as const;
  return (
    <div className="flex gap-1 rounded-md border p-0.5">
      {langs.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          onClick={() => choose(value)}
          aria-pressed={current === value}
          className={cn(
            "type-caption rounded px-2.5 py-1",
            current === value
              ? "bg-secondary text-secondary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
          data-slot="settings-language-option"
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="type-body font-medium">{label}</p>
        <p className="type-caption text-muted-foreground">{hint}</p>
      </div>
      {children}
    </div>
  );
}

function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-10 shrink-0 rounded-full border transition-colors",
        checked ? "bg-primary" : "bg-muted",
      )}
      data-slot="settings-switch"
    >
      <span
        className={cn(
          "absolute top-0.5 size-4 rounded-full bg-background transition-all",
          checked ? "left-[1.125rem]" : "left-0.5",
        )}
      />
    </button>
  );
}
