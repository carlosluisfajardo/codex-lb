import type { QueryClient } from "@tanstack/react-query";

const ACCOUNT_LIFECYCLE_QUERY_ROOT = ["accounts", "lifecycle"] as const;

export function accountLifecycleQueryKey(accountId: string | null) {
  return [...ACCOUNT_LIFECYCLE_QUERY_ROOT, accountId];
}

/**
 * The account row behind ``accountId`` is gone. Its id may later name a new row, so drop the
 * cached lifecycle notes and discard any read still in flight instead of letting either land.
 */
export async function forgetAccountLifecycle(queryClient: QueryClient, accountId: string) {
  const queryKey = accountLifecycleQueryKey(accountId);
  await queryClient.cancelQueries({ queryKey });
  queryClient.removeQueries({ queryKey });
}

/**
 * An account row was created and may reuse an id cached for a removed row: clear those lifecycle
 * notes (all of them when the id is unknown). Open views reload instead of showing the old row.
 */
export async function resetAccountLifecycle(queryClient: QueryClient, accountId?: string) {
  const queryKey = accountId === undefined ? [...ACCOUNT_LIFECYCLE_QUERY_ROOT] : accountLifecycleQueryKey(accountId);
  await queryClient.cancelQueries({ queryKey });
  await queryClient.resetQueries({ queryKey });
}

export function invalidateAccountRelatedQueries(queryClient: QueryClient, accountId?: string) {
  void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
  void queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] });
  // OAuth sign-in can create a row that reuses a deleted account's id.
  void resetAccountLifecycle(queryClient);
  if (accountId) {
    void queryClient.invalidateQueries({ queryKey: ["accounts", "trends", accountId] });
  }
}
