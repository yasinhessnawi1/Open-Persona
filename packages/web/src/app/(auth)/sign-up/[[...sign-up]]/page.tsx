import { SignUp } from "@clerk/nextjs";
import Image from "next/image";

export default function SignUpPage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-8 p-6">
      {/* Brand lockup above the auth card. Theme-aware stacked lockup: the
       * -light variant (dark ink wordmark) on light surfaces, the -dark variant
       * (warm-paper wordmark) on dark surfaces; swapped via the `dark:`
       * variant. Linked to the product root. */}
      <a href="/" aria-label="Open Persona">
        <Image
          src="/brand/logo-lockup-stacked-light.svg"
          alt="Open Persona"
          width={156}
          height={101}
          className="h-auto w-[140px] dark:hidden"
          priority
        />
        <Image
          src="/brand/logo-lockup-stacked-dark.svg"
          alt="Open Persona"
          width={156}
          height={101}
          className="hidden h-auto w-[140px] dark:block"
          priority
        />
      </a>
      <SignUp />
    </main>
  );
}
