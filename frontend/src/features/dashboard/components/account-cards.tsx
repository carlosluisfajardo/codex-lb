import { Users } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { AccountCard, type AccountCardProps } from "@/features/dashboard/components/account-card";
import type { AccountLifecycleMap } from "@/features/dashboard/hooks/use-account-lifecycle-map";
import type { AccountSummary } from "@/features/dashboard/schemas";

const ACCOUNT_CARD_VISIBLE_ROWS = 2;

export type AccountCardsProps = {
  accounts: AccountSummary[];
  readOnly?: boolean;
  /** Saved subscription dates by account id; `null` or omitted when the session may not read them. */
  lifecycle?: AccountLifecycleMap | null;
  onAction?: AccountCardProps["onAction"];
};

export function AccountCards({ accounts, readOnly = false, lifecycle, onAction }: AccountCardsProps) {
  const { t } = useTranslation();
  const gridRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const cards = Array.from(grid.children) as HTMLElement[];
    const measureRows = () => {
      let rowTop = -1;
      let rows = 0;
      let height = 0;
      for (const card of cards) {
        // Offset geometry ignores the staggered fade-in transforms.
        if (card.offsetTop !== rowTop) {
          if (++rows > ACCOUNT_CARD_VISIBLE_ROWS) break;
          rowTop = card.offsetTop;
        }
        height = Math.max(height, card.offsetTop - cards[0].offsetTop + card.offsetHeight);
      }
      if (height > 0) grid.style.maxHeight = `${height}px`;
    };
    measureRows();
    const observer = new ResizeObserver(measureRows);
    observer.observe(grid);
    for (const card of cards) observer.observe(card);
    return () => observer.disconnect();
  }, [accounts]);

  if (accounts.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title={t("dashboard.accounts.emptyTitle")}
        description={t("dashboard.accounts.emptyDescription")}
        action={
          <Button asChild size="sm">
            <Link to="/accounts">{t("dashboard.accounts.emptyAction")}</Link>
          </Button>
        }
      />
    );
  }

  return (
    <div
      ref={gridRef}
      data-testid="dashboard-account-cards"
      role="region"
      aria-label={t("accounts.page.title")}
      tabIndex={0}
      className="relative grid gap-4 overflow-y-auto pr-1 focus-visible:outline-2 focus-visible:outline-ring sm:grid-cols-2 lg:grid-cols-3"
    >
      {accounts.map((account, index) => (
        <div key={account.accountId} className="animate-fade-in-up" style={{ animationDelay: `${index * 75}ms` }}>
          <AccountCard
            account={account}
            showAccountId={account.isEmailDuplicate === true}
            readOnly={readOnly}
            lifecycle={lifecycle ? (lifecycle.get(account.accountId) ?? { status: "loading" }) : undefined}
            onAction={onAction}
          />
        </div>
      ))}
    </div>
  );
}
