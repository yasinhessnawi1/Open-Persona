import { currentUser } from "@clerk/nextjs/server";
import { getTranslations } from "next-intl/server";
import { PageBody, PageHeader, Stack } from "@/components/layout";
import { ErrorState } from "@/components/patterns/error-state";
import { LowBalanceWarningCard } from "@/components/settings/low-balance-warning-card";
import { PreferencesCard } from "@/components/settings/preferences-card";
import { Card } from "@/components/ui/card";
import { unwrap } from "@/lib/api";
import { serverApi } from "@/lib/api/server";

/**
 * Spec F2 T31 — Settings page (rebuilt presentation).
 *
 * DO NOT TOUCH (per audit.md §settings.plumbing):
 *   - `currentUser()` from Clerk + `serverApi()` GET `/v1/me/credits` + GET
 *     `/v1/me/usage` (parallel fetch);
 *   - `credits.balance` (D-11-12 zero-guard surfaces via the `exhausted`
 *     branch below);
 *   - `<PreferencesCard>` consumer of `useTheme` + `useBoolSetting` + `LOCALE_COOKIE`.
 *
 * Low-balance (D-11-12): `credits.low_balance` is surfaced inline via
 * `<LowBalanceWarningCard>` above the credits card when the backend flips the
 * flag and balance > 0. The credits-exhausted (balance === 0) cliff stays on
 * the existing T22 `<ErrorState status={402}>` branch below.
 *
 * REPLACED:
 *   - hand-rolled `mx-auto max-w-3xl` → T20 `<PageBody>`;
 *   - hand-rolled h1 `font-heading text-3xl` → T20 `<PageHeader>`;
 *   - section h2 `font-heading text-sm tracking-wide uppercase` →
 *     `.type-caption font-mono uppercase` (consistent with the F2 byline
 *     pattern seen in run viewer + authoring);
 *   - body `text-sm` / `text-xs` → `.type-body` / `.type-ui` / `.type-caption`;
 *   - `text-3xl` credit balance → `.type-display` (the Fraunces hero scale);
 *   - credits-exhausted (balance === 0) now surfaces via T22 `<ErrorState
 *     status={402}>` with `creditsExhausted` + `creditsExhaustedHint` copy.
 */
export default async function SettingsPage() {
  const t = await getTranslations("settings");
  const api = await serverApi();
  const [user, credits, usage] = await Promise.all([
    currentUser(),
    unwrap(await api.GET("/v1/me/credits")),
    unwrap(await api.GET("/v1/me/usage")),
  ]);

  const email = user?.primaryEmailAddress?.emailAddress ?? "";
  const name =
    [user?.firstName, user?.lastName].filter(Boolean).join(" ") || email;
  const exhausted = credits.balance === 0;

  return (
    <PageBody>
      <PageHeader title={t("title")} />

      <div className="lg:grid lg:grid-cols-[14rem_1fr] lg:gap-6">
        <nav
          aria-label={t("sectionsNav")}
          className="sticky top-20 hidden self-start lg:block"
          data-slot="settings-anchor-nav"
        >
          <ul className="flex flex-col gap-1 border-l text-muted-foreground">
            <li>
              <a
                href="#profile"
                className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              >
                {t("profileLabel")}
              </a>
            </li>
            <li>
              <a
                href="#credits"
                className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              >
                {t("credits")}
              </a>
            </li>
            <li>
              <a
                href="#preferences"
                className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              >
                {t("preferences")}
              </a>
            </li>
            <li>
              <a
                href="#usage"
                className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              >
                {t("usage")}
              </a>
            </li>
            <li>
              <a
                href="#about"
                className="type-ui block border-l-2 border-transparent px-3 py-1 hover:border-primary hover:text-foreground"
              >
                {t("aboutLabel")}
              </a>
            </li>
          </ul>
        </nav>

        <Stack gap={5}>
          <Card
            id="profile"
            className="gap-2 p-5 scroll-mt-20"
            data-slot="settings-account"
          >
            <h2 className="type-caption font-mono text-muted-foreground uppercase">
              {t("account")}
            </h2>
            <p className="type-body font-medium">{name}</p>
            {email && name !== email ? (
              <p className="type-ui text-muted-foreground">{email}</p>
            ) : null}
            <p className="type-caption text-muted-foreground">
              {t("accountHint")}
            </p>
          </Card>

          <LowBalanceWarningCard
            credits={credits}
            title={t("lowBalance")}
            hint={t("creditsHint")}
          />

          {exhausted ? (
            <ErrorState
              status={402}
              copy={{
                title: t("creditsExhausted"),
                description: t("creditsExhaustedHint"),
              }}
            />
          ) : (
            <Card
              id="credits"
              className="gap-2 p-5 scroll-mt-20"
              data-slot="settings-credits"
            >
              <h2 className="type-caption font-mono text-muted-foreground uppercase">
                {t("credits")}
              </h2>
              <p
                className="type-display tabular-nums"
                data-slot="settings-credits-balance"
              >
                {credits.balance.toLocaleString()}
              </p>
              <p className="type-caption text-muted-foreground">
                {t("creditsHint")}
              </p>
            </Card>
          )}

          <div id="preferences" className="scroll-mt-20">
            <PreferencesCard />
          </div>

          <Card
            id="usage"
            className="gap-3 p-5 scroll-mt-20"
            data-slot="settings-usage"
          >
            <h2 className="type-caption font-mono text-muted-foreground uppercase">
              {t("usage")}
            </h2>
            {usage.length === 0 ? (
              <p className="type-ui text-muted-foreground">{t("usageEmpty")}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="type-ui w-full">
                  <thead>
                    <tr className="type-caption border-b text-left text-muted-foreground uppercase">
                      <th className="py-2 pr-3 font-medium">{t("colWhen")}</th>
                      <th className="py-2 pr-3 font-medium">{t("colTier")}</th>
                      <th className="py-2 pr-3 font-medium">{t("colModel")}</th>
                      <th className="py-2 pr-3 text-right font-medium">
                        {t("colTokens")}
                      </th>
                      <th className="py-2 text-right font-medium">
                        {t("colCost")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.map((row, i) => (
                      <tr
                        // biome-ignore lint/suspicious/noArrayIndexKey: usage rows have no id
                        key={i}
                        className="border-b last:border-0"
                      >
                        <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                          {new Date(row.created_at).toLocaleString()}
                        </td>
                        <td className="py-2 pr-3">
                          <span className="type-caption font-mono uppercase">
                            {row.tier_used}
                          </span>
                        </td>
                        <td className="type-caption py-2 pr-3 font-mono">
                          {row.model_name}
                        </td>
                        <td className="py-2 pr-3 text-right tabular-nums">
                          {(
                            row.prompt_tokens + row.completion_tokens
                          ).toLocaleString()}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          ${(row.cost_cents / 100).toFixed(4)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card
            id="about"
            className="gap-3 p-5 scroll-mt-20"
            data-slot="settings-about"
          >
            <h2 className="type-caption font-mono text-muted-foreground uppercase">
              {t("aboutLabel")}
            </h2>
            <dl className="type-body grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">{t("aboutVersion")}</dt>
              <dd className="font-mono">persona-web 0.13.0+f5</dd>
              <dt className="text-muted-foreground">{t("aboutLicense")}</dt>
              <dd>Apache 2.0 (persona-core)</dd>
            </dl>
            <p className="type-caption text-muted-foreground">
              {t("aboutAttribution")}
            </p>
          </Card>
        </Stack>
      </div>
    </PageBody>
  );
}
