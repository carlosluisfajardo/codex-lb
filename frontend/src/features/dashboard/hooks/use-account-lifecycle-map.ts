import { useQueries, type QueryObserverResult } from "@tanstack/react-query";
import { useLayoutEffect, useMemo, useState } from "react";

import { getAccountLifecycle } from "@/features/accounts/api";
import { accountLifecycleQueryKey } from "@/features/accounts/query-invalidation";
import type { AccountLifecycle } from "@/features/accounts/schemas";
import { useAuthStore, usePermission } from "@/features/auth/hooks/use-auth";
import { accountLifecycleEntry, lifecycleDayKey, type AccountLifecycleEntry } from "@/features/dashboard/lifecycle";

export type AccountLifecycleMap = ReadonlyMap<string, AccountLifecycleEntry>;

type LifecycleRead = { data: AccountLifecycle | undefined; failed: boolean };

// Day counts change only at a date boundary of the viewer or of a recorded zone;
// this local check finds one within a minute and sends no request.
const DAY_CHECK_INTERVAL_MS = 60_000;

function combineReads(results: QueryObserverResult<AccountLifecycle>[]): LifecycleRead[] {
  return results.map((result) => ({ data: result.data, failed: result.isError }));
}

function currentDate(): Date {
  return new Date();
}

/**
 * The current date, replaced only when a displayed day count would change. Notes that arrive
 * are checked against the current date before paint, so a date kept through a quiet day
 * boundary never miscounts them.
 */
function useLifecycleNow(lifecycles: readonly AccountLifecycle[]): Date {
  const [now, setNow] = useState(currentDate);
  useLayoutEffect(() => {
    const refresh = () => {
      setNow((current) => {
        const next = currentDate();
        return lifecycleDayKey(lifecycles, current) === lifecycleDayKey(lifecycles, next) ? current : next;
      });
    };
    refresh();
    const intervalId = window.setInterval(refresh, DAY_CHECK_INTERVAL_MS);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(intervalId);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [lifecycles]);
  return now;
}

/**
 * One read of the saved lifecycle notes per dashboard account, shared by the card grid,
 * the list and the attention summary. It reuses the account detail panel's cache entry
 * and staleness and adds no polling. Returns `null`, and reads nothing, until the session
 * is initialized and holds `accounts:read`.
 */
export function useAccountLifecycleMap(accountIds: readonly string[]): AccountLifecycleMap | null {
  const initialized = useAuthStore((state) => state.initialized);
  const canReadAccounts = usePermission("accounts:read");
  const enabled = initialized && canReadAccounts;
  // Order-independent: the overview re-sorts accounts as their usage changes.
  const idsKey = JSON.stringify(enabled ? [...accountIds].sort() : []);
  const ids = useMemo(() => JSON.parse(idsKey) as string[], [idsKey]);
  const reads = useQueries({
    queries: ids.map((accountId) => ({
      queryKey: accountLifecycleQueryKey(accountId),
      queryFn: () => getAccountLifecycle(accountId),
      staleTime: 60_000,
    })),
    combine: combineReads,
  });
  const lifecycles = useMemo(
    () => reads.flatMap((read, index) => (read.data?.accountId === ids[index] ? [read.data] : [])),
    [reads, ids],
  );
  const now = useLifecycleNow(lifecycles);
  return useMemo(() => {
    if (!enabled) {
      return null;
    }
    return new Map(ids.map((accountId, index) => [accountId, accountLifecycleEntry(accountId, reads[index], now)]));
  }, [enabled, ids, reads, now]);
}
