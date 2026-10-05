import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { deleteAccount, getAccountLifecycle, importAccount, updateAccountLifecycle } from "@/features/accounts/api";
import { useAccountLifecycle, useAccountLifecycleMutation } from "@/features/accounts/hooks/use-account-lifecycle";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { accountLifecycleQueryKey, invalidateAccountRelatedQueries } from "@/features/accounts/query-invalidation";
import type { AccountLifecycle } from "@/features/accounts/schemas";
import { createAccountLifecycle } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const REMOVED_ROW_DATE = "2026-10-12";

function createClient(): QueryClient {
  // Default gcTime: removed rows' data would otherwise stay cached for minutes.
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function removedRowNotes(accountId: string): AccountLifecycle {
  return createAccountLifecycle({
    accountId,
    endsOn: { precision: "date", date: REMOVED_ROW_DATE, time: null, timezone: null },
    cancellationStatus: "cancelled",
    revision: 1,
    updatedAt: "2026-10-05T10:00:00Z",
  });
}

function renderLifecycleWithMutations(client: QueryClient, accountId: string) {
  const rendered: Array<AccountLifecycle | undefined> = [];
  const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
  const hook = renderHook(
    ({ id }: { id: string | null }) => {
      const lifecycle = useAccountLifecycle(id);
      rendered.push(id === null ? undefined : lifecycle.data);
      return { lifecycle, mutations: useAccountMutations() };
    },
    { wrapper, initialProps: { id: accountId as string | null } },
  );
  return { ...hook, rendered };
}

async function deleteThenReimportSameId(
  result: ReturnType<typeof renderLifecycleWithMutations>["result"],
  rerender: ReturnType<typeof renderLifecycleWithMutations>["rerender"],
  accountId: string,
) {
  await act(async () => {
    await result.current.mutations.deleteMutation.mutateAsync({ accountId, deleteHistory: false });
  });
  rerender({ id: null });
  let reimported: Awaited<ReturnType<typeof importAccount>> | undefined;
  await act(async () => {
    reimported = await result.current.mutations.importMutation.mutateAsync(new File(["{}"], "auth.json"));
  });
  expect(reimported?.accountId).toBe(accountId);
}

describe("lifecycle cache across a recycled account id", () => {
  it("never renders the removed row's notes for a new row given the same id", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    server.use(
      http.get(`/api/accounts/${first.accountId}/lifecycle`, () => HttpResponse.json(removedRowNotes(first.accountId)), {
        once: true,
      }),
    );
    const client = createClient();
    const { result, rerender, rendered, unmount } = renderLifecycleWithMutations(client, first.accountId);
    await waitFor(() => expect(result.current.lifecycle.data?.endsOn?.date).toBe(REMOVED_ROW_DATE));

    await deleteThenReimportSameId(result, rerender, first.accountId);
    const reselectedFrom = rendered.length;
    rerender({ id: first.accountId });

    expect(result.current.lifecycle.data?.endsOn?.date).not.toBe(REMOVED_ROW_DATE);
    await waitFor(() => expect(result.current.lifecycle.data?.revision).toBe(0));
    expect(result.current.lifecycle.data?.endsOn).toBeNull();
    expect(rendered.slice(reselectedFrom).filter((data) => data?.endsOn?.date === REMOVED_ROW_DATE)).toEqual([]);
    unmount();
    client.clear();
  });

  it("drops a read that was still in flight when the account was deleted", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    let releaseRead: () => void = () => undefined;
    const readReleased = new Promise<void>((resolve) => {
      releaseRead = resolve;
    });
    server.use(
      http.get(
        `/api/accounts/${first.accountId}/lifecycle`,
        async () => {
          await readReleased;
          return HttpResponse.json(removedRowNotes(first.accountId));
        },
        { once: true },
      ),
    );
    const client = createClient();
    const { result, rerender, rendered, unmount } = renderLifecycleWithMutations(client, first.accountId);
    await waitFor(() => expect(result.current.lifecycle.fetchStatus).toBe("fetching"));

    await deleteThenReimportSameId(result, rerender, first.accountId);
    releaseRead();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const reselectedFrom = rendered.length;
    rerender({ id: first.accountId });

    expect(result.current.lifecycle.data?.endsOn?.date).not.toBe(REMOVED_ROW_DATE);
    await waitFor(() => expect(result.current.lifecycle.data?.revision).toBe(0));
    expect(rendered.slice(reselectedFrom).filter((data) => data?.endsOn?.date === REMOVED_ROW_DATE)).toEqual([]);
    unmount();
    client.clear();
  });

  it("clears cached notes of an id that this client's import reuses after a deletion made elsewhere", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    const client = createClient();
    client.setQueryData(accountLifecycleQueryKey(first.accountId), removedRowNotes(first.accountId));
    await deleteAccount(first.accountId);
    const { result, rerender, rendered, unmount } = renderLifecycleWithMutations(client, first.accountId);
    rerender({ id: null });

    let reimported: Awaited<ReturnType<typeof importAccount>> | undefined;
    await act(async () => {
      reimported = await result.current.mutations.importMutation.mutateAsync(new File(["{}"], "auth.json"));
    });
    expect(reimported?.accountId).toBe(first.accountId);
    const reselectedFrom = rendered.length;
    rerender({ id: first.accountId });

    expect(result.current.lifecycle.data?.endsOn?.date).not.toBe(REMOVED_ROW_DATE);
    await waitFor(() => expect(result.current.lifecycle.data?.revision).toBe(0));
    expect(rendered.slice(reselectedFrom).filter((data) => data?.endsOn?.date === REMOVED_ROW_DATE)).toEqual([]);
    unmount();
    client.clear();
  });

  it("does not let a save response that arrives after the delete repopulate the cache", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    const { concurrencyToken: token } = await getAccountLifecycle(first.accountId);
    let releaseSave: () => void = () => undefined;
    const saveReleased = new Promise<void>((resolve) => {
      releaseSave = resolve;
    });
    server.use(
      http.put(
        `/api/accounts/${first.accountId}/lifecycle`,
        async () => {
          await saveReleased;
          // Accepted before the delete, so it carries the removed row's own token.
          return HttpResponse.json({ ...removedRowNotes(first.accountId), concurrencyToken: token });
        },
        { once: true },
      ),
    );
    const client = createClient();
    const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
    const { result, unmount } = renderHook(
      () => ({
        lifecycle: useAccountLifecycle(first.accountId),
        save: useAccountLifecycleMutation(),
        mutations: useAccountMutations(),
      }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.lifecycle.data?.concurrencyToken).toBe(token));

    let pendingSave: Promise<unknown> = Promise.resolve();
    act(() => {
      pendingSave = result.current.save.mutateAsync({
        accountId: first.accountId,
        payload: {
          endsOn: { precision: "date", date: REMOVED_ROW_DATE },
          renewsOn: null,
          cancellationStatus: "cancelled",
          expectedRevision: 0,
          expectedConcurrencyToken: token,
        },
      });
    });
    await act(async () => {
      await result.current.mutations.deleteMutation.mutateAsync({ accountId: first.accountId, deleteHistory: false });
    });
    releaseSave();
    await act(async () => {
      await pendingSave;
    });

    expect(client.getQueryData(accountLifecycleQueryKey(first.accountId))).toBeUndefined();
    unmount();
    client.clear();
  });

  it("keeps showing the same row's notes when a sign-in completes for that row", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    const read = await getAccountLifecycle(first.accountId);
    await updateAccountLifecycle(first.accountId, {
      endsOn: { precision: "date", date: "2026-11-30" },
      renewsOn: null,
      cancellationStatus: null,
      expectedRevision: read.revision,
      expectedConcurrencyToken: read.concurrencyToken,
    });
    const client = createClient();
    const { result, rendered, unmount } = renderLifecycleWithMutations(client, first.accountId);
    await waitFor(() => expect(result.current.lifecycle.data?.endsOn?.date).toBe("2026-11-30"));
    const from = rendered.length;

    act(() => {
      invalidateAccountRelatedQueries(client);
    });

    await waitFor(() => expect(result.current.lifecycle.data?.endsOn?.date).toBe("2026-11-30"));
    expect(result.current.lifecycle.data?.concurrencyToken).toBe(read.concurrencyToken);
    expect(rendered.slice(from).filter((data) => data !== undefined && data.endsOn?.date !== "2026-11-30")).toEqual(
      [],
    );
    unmount();
    client.clear();
  });
});
