import { Card } from "@/components/ui/card";

/**
 * Spec F? L6a — Low-balance inline warning.
 *
 * Renders an inline warning Card when the backend `CreditsResponse.low_balance`
 * flag is true AND `balance > 0`. The credits-exhausted cliff (balance === 0)
 * is a separate surface, handled by the page's existing
 * `<ErrorState status={402}>` branch.
 *
 * Pure presentational — strings are pre-resolved by the (server) page caller
 * via `getTranslations("settings")`, keeping this component client-safe and
 * trivially testable.
 */
export interface LowBalanceWarningCardProps {
  readonly credits: { readonly balance: number; readonly low_balance: boolean };
  readonly title: string;
  readonly hint: string;
}

export function LowBalanceWarningCard({
  credits,
  title,
  hint,
}: LowBalanceWarningCardProps) {
  if (!credits.low_balance || credits.balance <= 0) {
    return null;
  }
  return (
    <Card
      className="gap-1 p-4 ring-tier-mid/40"
      data-slot="settings-low-balance-warning"
    >
      <p className="type-body font-medium">{title}</p>
      <p className="type-caption text-muted-foreground">{hint}</p>
    </Card>
  );
}
