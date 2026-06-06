import { PageBody, Stack } from "@/components/layout";
import {
  SkeletonAvatar,
  SkeletonBlock,
  SkeletonLine,
} from "@/components/patterns/loading";
import { Card } from "@/components/ui/card";

/**
 * Spec F2 T28 — Persona detail loading skeleton.
 *
 * Renders the same `<PageBody>` width + identity-header silhouette as the
 * live detail page so the layout does not reflow when the persona doc lands.
 * Three skeleton section cards mirror the typical first-fold content stack
 * (run-task callout + background + constraints).
 */
export default function PersonaDetailLoading() {
  return (
    <PageBody>
      <SkeletonLine className="mb-6 w-24" />
      <div className="mb-8 flex items-center gap-4">
        <SkeletonAvatar size="lg" />
        <div className="space-y-2">
          <SkeletonLine className="w-48" />
          <SkeletonLine className="w-32" />
        </div>
      </div>
      <Stack gap={5}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Card
            // biome-ignore lint/suspicious/noArrayIndexKey: synthetic skeletons, no ids
            key={i}
            className="p-5"
            data-slot="persona-detail-section-skeleton"
          >
            <SkeletonBlock lines={3} />
          </Card>
        ))}
      </Stack>
    </PageBody>
  );
}
