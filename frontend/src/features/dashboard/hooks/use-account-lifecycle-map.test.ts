import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { importAccount } from "@/features/accounts/api";
import { useAccountMutations } from "@/features/accounts/hooks/use-accounts";
import { accountLifecycleQueryKey } from "@/features/accounts/query-invalidation";
import type { AccountLifecycle } from "@/features/accounts/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useAccountLifecycleMap, type AccountLifecycleMap } from "@/features/dashboard/hooks/use-account-lifecycle-map";
import type { AccountLifecycleEntry } from "@/features/dashboard/lifecycle";
import { ADMIN_PERMISSIONS, VIEWER_PERMISSIONS, createAccountLifecycle } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const REMOVED_ROW_DATE = "2026-10-12";

function createClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function wrapperFor(client: QueryClient) {
  return ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
}

function lifecycleReads(): string[] {
  const reads: string[] = [];
  server.events.on("request:start", ({ request }) => {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && /^\/api\/accounts\/[^/]+\/lifecycle$/.test(path)) {
      reads.push(decodeURIComponent(path.split("/")[3]));
    }
  });
  return reads;
}

function serveLifecycles(lifecycles: Record<string, AccountLifecycle>) {
  server.use(
    http.get("/api/accounts/:accountId/lifecycle", ({ params }) => {
      const lifecycle = lifecycles[String(params.accountId)];
      return lifecycle
        ? HttpResponse.json(lifecycle)
        : HttpResponse.json({ error: { code: "account_not_found", message: "Account not found" } }, { status: 404 });
    }),
  );
}

function renewsOn(entry: AccountLifecycleEntry | undefined): string | null | undefined {
  return entry?.status === "ready" ? (entry.renewsOn?.value.date ?? null) : undefined;
}

function endsOn(entry: AccountLifecycleEntry | undefined): string | null | undefined {
  return entry?.status === "ready" ? (entry.endsOn?.value.date ?? null) : undefined;
}

beforeEach(() => {
  useAuthStore.setState({ role: "admin", permissions: ADMIN_PERMISSIONS, canWrite: true, initialized: true });
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  useAuthStore.setState({ role: "admin", permissions: ADMIN_PERMISSIONS, canWrite: true, initialized: false });
});

describe("useAccountLifecycleMap access", () => {
  it("reads nothing until the session is initialized", async () => {
    useAuthStore.setState({ initialized: false });
    serveLifecycles({ acc_a: createAccountLifecycle({ accountId: "acc_a" }) });
    const reads = lifecycleReads();
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current).toBeNull();
    expect(reads).toEqual([]);

    act(() => {
      useAuthStore.setState({ initialized: true });
    });
    await waitFor(() => expect(result.current?.get("acc_a")?.status).toBe("ready"));
    expect(reads).toEqual(["acc_a"]);
  });

  it("neither reads nor renders cached notes without accounts:read", async () => {
    useAuthStore.setState({ role: "guest", permissions: ["read", "dashboard:read:all"], canWrite: false });
    const reads = lifecycleReads();
    const client = createClient();
    client.setQueryData(
      accountLifecycleQueryKey("acc_a"),
      createAccountLifecycle({ accountId: "acc_a", renewsOn: { precision: "date", date: "2026-10-31", time: null, timezone: null } }),
    );
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(result.current).toBeNull();
    expect(reads).toEqual([]);
  });

  it("reads with accounts:read alone, without write access", async () => {
    useAuthStore.setState({ role: "guest", permissions: VIEWER_PERMISSIONS, canWrite: false });
    serveLifecycles({
      acc_a: createAccountLifecycle({
        accountId: "acc_a",
        renewsOn: { precision: "date", date: "2026-10-31", time: null, timezone: null },
      }),
    });
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });

    await waitFor(() => expect(renewsOn(result.current?.get("acc_a"))).toBe("2026-10-31"));
  });

  it("stops showing notes when accounts:read is withdrawn", async () => {
    serveLifecycles({ acc_a: createAccountLifecycle({ accountId: "acc_a" }) });
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(result.current?.get("acc_a")?.status).toBe("ready"));

    act(() => {
      useAuthStore.setState({ role: "guest", permissions: ["read", "dashboard:read:all"], canWrite: false });
    });

    expect(result.current).toBeNull();
  });
});

describe("useAccountLifecycleMap reads", () => {
  it("never shows notes returned for another account id", async () => {
    server.use(
      http.get("/api/accounts/:accountId/lifecycle", () =>
        HttpResponse.json(
          createAccountLifecycle({
            accountId: "acc_other",
            endsOn: { precision: "date", date: REMOVED_ROW_DATE, time: null, timezone: null },
          }),
        ),
      ),
    );
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });

    await waitFor(() => expect(result.current?.get("acc_a")?.status).toBe("unavailable"));
    expect(endsOn(result.current?.get("acc_a"))).toBeUndefined();
  });

  it("keeps a failed read apart from unset dates", async () => {
    server.use(
      http.get("/api/accounts/:accountId/lifecycle", ({ params }) =>
        String(params.accountId) === "acc_failing"
          ? HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 })
          : HttpResponse.json(createAccountLifecycle({ accountId: String(params.accountId) })),
      ),
    );
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_failing", "acc_unset"]), {
      wrapper: wrapperFor(client),
    });

    await waitFor(() => expect(result.current?.get("acc_failing")?.status).toBe("unavailable"));
    await waitFor(() => expect(result.current?.get("acc_unset")).toEqual({ status: "ready", endsOn: null, renewsOn: null }));
  });

  it("shows a failed refetch as unavailable instead of the cached dates", async () => {
    let failing = false;
    server.use(
      http.get("/api/accounts/:accountId/lifecycle", ({ params }) =>
        failing
          ? HttpResponse.json({ error: { code: "account_not_found", message: "Account not found" } }, { status: 404 })
          : HttpResponse.json(
              createAccountLifecycle({
                accountId: String(params.accountId),
                renewsOn: { precision: "date", date: "2026-10-31", time: null, timezone: null },
              }),
            ),
      ),
    );
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });
    await waitFor(() => expect(renewsOn(result.current?.get("acc_a"))).toBe("2026-10-31"));

    failing = true;
    await act(async () => {
      await client.refetchQueries({ queryKey: accountLifecycleQueryKey("acc_a") });
    });

    await waitFor(() => expect(result.current?.get("acc_a")).toEqual({ status: "unavailable" }));
    expect(client.getQueryData<AccountLifecycle>(accountLifecycleQueryKey("acc_a"))?.renewsOn?.date).toBe("2026-10-31");
  });

  it("reads each account once and keeps the same map while nothing changes, in any order", async () => {
    serveLifecycles({
      acc_a: createAccountLifecycle({ accountId: "acc_a" }),
      acc_b: createAccountLifecycle({ accountId: "acc_b" }),
    });
    const reads = lifecycleReads();
    const client = createClient();
    const { result, rerender } = renderHook(({ ids }: { ids: string[] }) => useAccountLifecycleMap(ids), {
      wrapper: wrapperFor(client),
      initialProps: { ids: ["acc_a", "acc_b"] },
    });
    await waitFor(() => expect(result.current?.get("acc_b")?.status).toBe("ready"));
    const settled = result.current;

    rerender({ ids: ["acc_a", "acc_b"] });
    rerender({ ids: ["acc_b", "acc_a"] });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(result.current).toBe(settled);
    expect([...reads].sort()).toEqual(["acc_a", "acc_b"]);
  });
});

describe("useAccountLifecycleMap across a recycled account id", () => {
  function renderWithMutations(client: QueryClient, accountId: string) {
    const rendered: Array<AccountLifecycleMap | null> = [];
    const hook = renderHook(
      ({ ids }: { ids: string[] }) => {
        const lifecycle = useAccountLifecycleMap(ids);
        rendered.push(lifecycle);
        return { lifecycle, mutations: useAccountMutations() };
      },
      { wrapper: wrapperFor(client), initialProps: { ids: [accountId] } },
    );
    return { ...hook, rendered };
  }

  async function deleteThenReimport(
    hook: ReturnType<typeof renderWithMutations>,
    accountId: string,
  ) {
    await act(async () => {
      await hook.result.current.mutations.deleteMutation.mutateAsync({ accountId, deleteHistory: false });
    });
    hook.rerender({ ids: [] });
    let reimported: Awaited<ReturnType<typeof importAccount>> | undefined;
    await act(async () => {
      reimported = await hook.result.current.mutations.importMutation.mutateAsync(new File(["{}"], "auth.json"));
    });
    expect(reimported?.accountId).toBe(accountId);
  }

  function removedRowNotes(accountId: string): AccountLifecycle {
    return createAccountLifecycle({
      accountId,
      endsOn: { precision: "date", date: REMOVED_ROW_DATE, time: null, timezone: null },
      revision: 1,
    });
  }

  it("never shows the removed row's dates for a new row given the same id", async () => {
    const first = await importAccount(new File(["{}"], "auth.json"));
    server.use(
      http.get(`/api/accounts/${first.accountId}/lifecycle`, () => HttpResponse.json(removedRowNotes(first.accountId)), {
        once: true,
      }),
    );
    const client = createClient();
    const hook = renderWithMutations(client, first.accountId);
    await waitFor(() => expect(endsOn(hook.result.current.lifecycle?.get(first.accountId))).toBe(REMOVED_ROW_DATE));

    await deleteThenReimport(hook, first.accountId);
    const reselectedFrom = hook.rendered.length;
    hook.rerender({ ids: [first.accountId] });

    await waitFor(() => expect(endsOn(hook.result.current.lifecycle?.get(first.accountId))).toBeNull());
    expect(
      hook.rendered.slice(reselectedFrom).filter((map) => endsOn(map?.get(first.accountId)) === REMOVED_ROW_DATE),
    ).toEqual([]);
    hook.unmount();
    client.clear();
  });

  it("drops a read still in flight when the account is deleted", async () => {
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
    const hook = renderWithMutations(client, first.accountId);
    await waitFor(() => expect(hook.result.current.lifecycle?.get(first.accountId)?.status).toBe("loading"));

    await deleteThenReimport(hook, first.accountId);
    releaseRead();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const reselectedFrom = hook.rendered.length;
    hook.rerender({ ids: [first.accountId] });

    await waitFor(() => expect(endsOn(hook.result.current.lifecycle?.get(first.accountId))).toBeNull());
    expect(
      hook.rendered.slice(reselectedFrom).filter((map) => endsOn(map?.get(first.accountId)) === REMOVED_ROW_DATE),
    ).toEqual([]);
    hook.unmount();
    client.clear();
  });
});

describe("useAccountLifecycleMap day boundaries", () => {
  it("counts notes that arrive after a quiet day boundary against the current date", async () => {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"], shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
    serveLifecycles({
      acc_unset: createAccountLifecycle({ accountId: "acc_unset" }),
      acc_new: createAccountLifecycle({
        accountId: "acc_new",
        renewsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
      }),
    });
    const client = createClient();
    const { result, rerender } = renderHook(({ ids }: { ids: string[] }) => useAccountLifecycleMap(ids), {
      wrapper: wrapperFor(client),
      initialProps: { ids: ["acc_unset"] },
    });
    await waitFor(() => expect(result.current?.get("acc_unset")?.status).toBe("ready"));

    // Nine days pass without a check firing; no displayed count could change meanwhile.
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    rerender({ ids: ["acc_unset", "acc_new"] });
    await waitFor(() => expect(result.current?.get("acc_new")?.status).toBe("ready"));

    const entry = result.current?.get("acc_new");
    const count = entry?.status === "ready" ? entry.renewsOn?.count : undefined;
    expect(count).toEqual({ known: true, days: 2, attention: "dueSoon" });
  });

  it("follows the date across midnight and on visibility without new reads", async () => {
    vi.stubEnv("TZ", "UTC");
    // The clock keeps moving so waitFor can poll; the jumps below cross the date boundaries.
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"], shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-10-10T23:58:00Z"));
    serveLifecycles({
      acc_a: createAccountLifecycle({
        accountId: "acc_a",
        renewsOn: { precision: "date", date: "2026-10-11", time: null, timezone: null },
      }),
    });
    const reads = lifecycleReads();
    const client = createClient();
    const { result } = renderHook(() => useAccountLifecycleMap(["acc_a"]), { wrapper: wrapperFor(client) });
    const days = () => {
      const entry = result.current?.get("acc_a");
      return entry?.status === "ready" && entry.renewsOn?.count.known ? entry.renewsOn.count.days : null;
    };
    await waitFor(() => expect(days()).toBe(1));

    const beforeMidnight = result.current;
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current).toBe(beforeMidnight);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(days()).toBe(0);

    act(() => {
      vi.setSystemTime(new Date("2026-10-12T08:00:00Z"));
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(days()).toBe(-1);
    expect(reads).toEqual(["acc_a"]);
  });
});
