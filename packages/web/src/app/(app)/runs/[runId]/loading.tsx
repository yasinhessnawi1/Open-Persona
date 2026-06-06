import { PageBody, Stack } from "@/components/layout";
import {
  SkeletonAvatar,
  SkeletonBlock,
  SkeletonLine,
} from "@/components/patterns/loading";
import { Card } from "@/components/ui/card";

/**
 * Spec F2 T30 — Run viewer loading skeleton.
 *
 * Same `<PageBody>` width + identity-header silhouette as the live run page
 * so the layout does not reflow when the run snapshot lands. Three skeleton
 * step cards approximate the typical "fetching the run" first-fold.
 */
export default function RunLoading() {
  return (
    <PageBody>
      <SkeletonLine className="mb-6 w-24" />
      <div className="mb-8 flex items-start gap-4">
        <SkeletonAvatar size="lg" />
        <div className="min-w-0 flex-1 space-y-2">
          <SkeletonLine className="w-24" />
          <SkeletonLine className="w-2/3" />
        </div>
      </div>
      <Stack gap={3}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Card
            // biome-ignore lint/suspicious/noArrayIndexKey: synthetic skeletons, no ids
            key={i}
            className="p-4"
            data-slot="run-step-skeleton"
          >
            <SkeletonBlock lines={3} />
          </Card>
        ))}
      </Stack>
    </PageBody>
  );
}
