import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { getAccountLifecycle, updateAccountLifecycle } from "@/features/accounts/api";
import { isAccountLifecycleConflict } from "@/features/accounts/lifecycle";
import { accountLifecycleQueryKey } from "@/features/accounts/query-invalidation";
import type { AccountLifecycle, AccountLifecycleUpdateRequest } from "@/features/accounts/schemas";

/**
 * Lifecycle notes change only when an operator saves them, so this query adds no
 * polling of its own; the page's existing account-list and trends polls are unchanged.
 */
export function useAccountLifecycle(accountId: string | null) {
  return useQuery({
    queryKey: accountLifecycleQueryKey(accountId),
    queryFn: () => getAccountLifecycle(accountId as string),
    enabled: !!accountId,
    staleTime: 60_000,
  });
}

export function useAccountLifecycleMutation() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ accountId, payload }: { accountId: string; payload: AccountLifecycleUpdateRequest }) =>
      updateAccountLifecycle(accountId, payload),
    onSuccess: (data, variables) => {
      // Only replace notes read from the same account row: a response arriving after that row was
      // removed (cache evicted) or replaced (different token) must not repopulate the cache.
      queryClient.setQueryData<AccountLifecycle>(accountLifecycleQueryKey(variables.accountId), (current) =>
        current?.concurrencyToken === data.concurrencyToken ? data : current,
      );
      toast.success(t("accounts.lifecycle.toasts.saved"));
    },
    onError: (error: Error, variables) => {
      if (isAccountLifecycleConflict(error)) {
        // Nothing was written: reload the stored values so the operator can reapply the edit.
        void queryClient.invalidateQueries({ queryKey: accountLifecycleQueryKey(variables.accountId) });
        toast.error(t("accounts.lifecycle.toasts.conflict"));
        return;
      }
      toast.error(error.message || t("accounts.lifecycle.toasts.saveFailed"));
    },
  });
}
