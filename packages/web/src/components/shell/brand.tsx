import Image from "next/image";
import { cn } from "@/lib/utils";

// The wordmark: the Open Persona memory-sphere mark + "Open Persona" in the
// Fraunces display face. The mark is theme-aware — the orbit-ring strokes are
// tuned per surface, so we ship the on-dark mark for dark mode and the
// paper mark for light mode and swap via the `dark:` variant (next-themes
// toggles `.dark` on <html>). Brand name is a proper noun — intentionally
// not run through i18n. alt is empty because the adjacent text already names
// the product (the mark is decorative in this lockup).
export function Brand({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <span className="relative size-7 shrink-0">
        <Image
          src="/brand/logo-mark-light.svg"
          alt=""
          width={28}
          height={28}
          className="size-7 dark:hidden"
          priority
        />
        <Image
          src="/brand/logo-mark-ondark.svg"
          alt=""
          width={28}
          height={28}
          className="hidden size-7 dark:block"
          priority
        />
      </span>
      <span className="font-heading text-lg font-semibold tracking-tight">
        Open Persona
      </span>
    </span>
  );
}
