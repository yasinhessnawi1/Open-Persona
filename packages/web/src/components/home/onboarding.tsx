import {
  ArrowRight,
  FileText,
  Gauge,
  type LucideIcon,
  Sparkles,
  Workflow,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { Grid, Section, Stack } from "@/components/layout";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * New-user onboarding (home empty state).
 *
 * Repurposes the four feature beats from the retired in-app marketing landing
 * (typed memory · tools/MCP · agentic runs · tier routing) as quickstart cards.
 * Every card funnels to the same first action — create your first persona —
 * because that single step unlocks all four features. The marketing copy lives
 * on under the `home.features.*` i18n keys (migrated verbatim from the old
 * `landing.features.*`).
 */
const FEATURES: ReadonlyArray<{ icon: LucideIcon; key: string }> = [
  { icon: FileText, key: "identity" },
  { icon: Wrench, key: "tools" },
  { icon: Workflow, key: "runs" },
  { icon: Gauge, key: "routing" },
];

export async function Onboarding() {
  const t = await getTranslations("home");

  return (
    <Stack gap={8}>
      {/* Primary invitation — the one action that matters for a new user. */}
      <Card className="gap-4 border-primary/20 p-6" data-slot="onboarding-hero">
        <p className="type-caption font-mono uppercase text-primary">
          {t("onboarding.kicker")}
        </p>
        <h2 className="type-heading max-w-xl text-balance">
          {t("onboarding.title")}
        </h2>
        <p className="type-ui max-w-xl text-muted-foreground">
          {t("onboarding.body")}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href="/personas/new"
            className={cn(buttonVariants(), "gap-2")}
            data-slot="onboarding-create"
          >
            <Sparkles className="size-4" aria-hidden="true" />
            {t("onboarding.create")}
          </Link>
          {/* Docs placeholder — carried over from the retired landing. Points at
           * the marketing site's docs surface; falls back to a relative anchor
           * until that exists. */}
          <a
            href={process.env.NEXT_PUBLIC_DOCS_URL?.trim() || "#"}
            className={cn(buttonVariants({ variant: "outline" }), "gap-2")}
            data-slot="onboarding-docs"
          >
            {t("onboarding.docs")}
          </a>
        </div>
      </Card>

      <Section heading={t("onboarding.featuresHeading")}>
        <Grid cols={{ base: 1, sm: 2 }} gap={4}>
          {FEATURES.map(({ icon: Icon, key }) => (
            <Link
              key={key}
              href="/personas/new"
              className="group/quickstart block"
              data-slot="onboarding-feature"
            >
              <Card className="h-full gap-2 p-5 transition-colors duration-[var(--motion-duration-fast)] hover:bg-accent/40">
                <Icon className="size-5 text-primary" aria-hidden="true" />
                <h3 className="type-heading">{t(`features.${key}.title`)}</h3>
                <p className="type-ui text-muted-foreground">
                  {t(`features.${key}.body`)}
                </p>
                <span className="type-ui mt-2 inline-flex items-center gap-1.5 text-primary">
                  {t("onboarding.featureCta")}
                  <ArrowRight
                    className="size-4 transition-transform duration-[var(--motion-duration-fast)] group-hover/quickstart:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover/quickstart:translate-x-0"
                    aria-hidden="true"
                  />
                </span>
              </Card>
            </Link>
          ))}
        </Grid>
      </Section>

      {/*
        MARKETPLACE SEAM — DO NOT BUILD YET.

        Future "community / starter personas" gallery slots here: a grid of
        clonable starter personas a new user can adopt instead of authoring
        from scratch. This is explicitly deferred (roadmap Oct–Nov; see
        docs/ARCHITECTURE.md §2.2 / §9.4) and needs backend that does not
        exist: bundled-persona definitions, a clone/import endpoint, and a
        public-visibility model. Render a "Starter personas (coming soon)"
        Section here once that backend lands. Nothing user-facing ships now.
      */}
    </Stack>
  );
}
