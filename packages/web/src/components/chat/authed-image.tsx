/**
 * F4 T16 (D-F4-X-authedimage-f2-promotion) — strangler-fig re-export shim.
 *
 * The canonical source-of-truth moved to
 * `packages/web/src/components/ui/authed-image.tsx` per F2 promotion
 * convention. This shim preserves the existing import path
 * (`@/components/chat/authed-image` and relative `./authed-image`) so
 * F3 + F4 + future consumers see no churn during the strangler-fig
 * transition.
 *
 * Removable when all in-repo imports migrate to `@/components/ui/authed-image`
 * (no urgency — the shim cost is one re-export line).
 */
export {
  AuthedImage,
  type AuthedImageProps,
} from "@/components/ui/authed-image";
