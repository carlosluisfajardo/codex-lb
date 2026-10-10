import { useTranslation } from "react-i18next";

import type { AccountLifecycleMap } from "@/features/dashboard/hooks/use-account-lifecycle-map";
import { LIFECYCLE_DUE_SOON_DAYS, summarizeLifecycleAttention } from "@/features/dashboard/lifecycle";
import type { AccountSummary } from "@/features/dashboard/schemas";
import { normalizeStatus } from "@/utils/account-status";

type AccountSummaryLineProps = {
  accounts: AccountSummary[];
  /** Saved subscription dates by account id; `null` or omitted when the session may not read them. */
  lifecycle?: AccountLifecycleMap | null;
};

export function AccountSummaryLine({ accounts, lifecycle }: AccountSummaryLineProps) {
  const { t } = useTranslation();
  const registeredCount = accounts.length;
  const activeCount = accounts.filter((account) => normalizeStatus(account.status) === "active").length;
  const unavailableCount = registeredCount - activeCount;

  return (
    <>
      <div
        data-testid="dashboard-account-summary-line"
        className="flex items-center gap-1.5 whitespace-nowrap text-xs"
      >
        <span className="font-semibold tabular-nums text-foreground">{registeredCount}</span>
        <span className="text-muted-foreground">{t("dashboard.accounts.summary.registered")}</span>
        <span className="text-border">·</span>
        <span className="font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{activeCount}</span>
        <span className="text-muted-foreground">{t("dashboard.accounts.summary.active")}</span>
        <span className="text-border">·</span>
        <span className="font-semibold tabular-nums text-red-600 dark:text-red-400">{unavailableCount}</span>
        <span className="text-muted-foreground">{t("dashboard.accounts.summary.unavailable")}</span>
      </div>
      {lifecycle && accounts.length > 0 ? <LifecycleAttentionSummary lifecycle={lifecycle} /> : null}
    </>
  );
}

function LifecycleAttentionSummary({ lifecycle }: { lifecycle: AccountLifecycleMap }) {
  const { t } = useTranslation();
  const summary = summarizeLifecycleAttention(lifecycle.values());
  const segments = [
    { key: "passed", count: summary.passed, className: "text-red-600 dark:text-red-400", label: t("dashboard.accounts.lifecycleSummary.passed") },
    { key: "today", count: summary.today, className: "text-amber-600 dark:text-amber-400", label: t("dashboard.accounts.lifecycleSummary.today") },
    {
      key: "dueSoon",
      count: summary.dueSoon,
      className: "text-amber-600 dark:text-amber-400",
      label: t("dashboard.accounts.lifecycleSummary.dueSoon", { days: LIFECYCLE_DUE_SOON_DAYS }),
    },
    { key: "unknown", count: summary.unknown, className: "text-foreground", label: t("dashboard.accounts.lifecycleSummary.unknown") },
  ].filter((segment) => segment.count > 0);
  if (segments.length === 0 && summary.pending > 0) {
    return null;
  }

  return (
    <div
      data-testid="dashboard-account-date-summary"
      className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs"
    >
      <span className="text-muted-foreground">{t("dashboard.accounts.lifecycleSummary.label")}</span>
      {segments.length === 0 ? (
        <span className="text-muted-foreground">
          {t("dashboard.accounts.lifecycleSummary.none", { days: LIFECYCLE_DUE_SOON_DAYS })}
        </span>
      ) : (
        segments.map((segment, index) => (
          <span key={segment.key} className="inline-flex items-center gap-1.5 whitespace-nowrap">
            {index > 0 ? <span className="text-border">·</span> : null}
            <span className={`font-semibold tabular-nums ${segment.className}`}>{segment.count}</span>
            <span className="text-muted-foreground">{segment.label}</span>
          </span>
        ))
      )}
    </div>
  );
}
