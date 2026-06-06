import { getTranslations } from "next-intl/server";
import { Grid, PageBody, PageHeader } from "@/components/layout";
import { SkeletonAvatar, SkeletonLine } from "@/components/patterns/loading";
import { Card } from "@/components/ui/card";

/**
 * Spec F2 T27 — Persona list loading skeleton.
 *
 * Next.js 16 convention: a sibling `loading.tsx` is rendered as the
 * Suspense fallback during the server fetch of `page.tsx`. We render the
 * same `<PageBody>` + `<PageHeader>` shape as the real page so the layout
 * does not reflow when data lands — only the card grid swaps from
 * skeletons to live cards.
 *
 * Six skeleton cards mirror the F2 T14 `<PersonaCard>` row layout:
 * avatar circle + 2-line stack (name 60% width / role 35% width). Aria-
 * hidden is handled inside the skeleton primitives; the page header
 * carries the announced "Personas" title so screen readers know what's
 * loading.
 */
export default async function PersonasLoading() {
  const t = await getTranslations("personas");

  return (
    <PageBody>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Grid cols={{ base: 1, sm: 2, lg: 3 }} gap={4}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Card
            // biome-ignore lint/suspicious/noArrayIndexKey: synthetic skeletons, no ids
            key={i}
            size="sm"
            className="flex flex-row items-center gap-4 p-4"
            data-slot="persona-card-skeleton"
          >
            <SkeletonAvatar size="md" />
            <div className="min-w-0 flex-1 space-y-2">
              <SkeletonLine className="w-2/3" />
              <SkeletonLine className="w-1/3" />
            </div>
          </Card>
        ))}
      </Grid>
    </PageBody>
  );
}
