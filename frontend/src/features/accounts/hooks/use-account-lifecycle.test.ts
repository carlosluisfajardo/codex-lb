import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useAccountLifecycle,
  useAccountLifecycleMutation,
} from "@/features/accounts/hooks/use-account-lifecycle";
import {
  deleteAccount,
  getAccountLifecycle,
  importAccount,
  updateAccountLifecycle,
} from "@/features/accounts/api";
import { ApiError } from "@/lib/api-client";
import { createAccountLifecycle } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMocks }));

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

afterEach(() => {
  vi.clearAllMocks();
});

const LIFECYCLE_KEY = ["accounts", "lifecycle", "acc_primary"];

describe("useAccountLifecycle", () => {
  it("reads an account's lifecycle by its exact id without polling", async () => {
    const queryClient = createTestQueryClient();
    const requestedIds: string[] = [];
    server.use(
      http.get("/api/accounts/:accountId/lifecycle", ({ params }) => {
        requestedIds.push(String(params.accountId));
        return HttpResponse.json(
          createAccountLifecycle({
            accountId: String(params.accountId),
            endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
            cancellationStatus: "cancelled",
            revision: 1,
            updatedAt: "2026-10-05T10:00:00Z",
          }),
        );
      }),
    );

    const { result } = renderHook(() => useAccountLifecycle("acc_primary"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestedIds).toEqual(["acc_primary"]);
    expect(result.current.data).toEqual({
      accountId: "acc_primary",
      endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
      renewsOn: null,
      cancellationStatus: "cancelled",
      revision: 1,
      updatedAt: "2026-10-05T10:00:00Z",
    });

    const query = queryClient.getQueryCache().find({ queryKey: LIFECYCLE_KEY });
    const options = query?.options as { refetchInterval?: unknown; staleTime?: unknown } | undefined;
    expect(options?.refetchInterval).toBeUndefined();
    expect(options?.staleTime).toBe(60_000);
  });

  it("reports never-saved metadata as unset at revision 0 from the mock server", async () => {
    const queryClient = createTestQueryClient();

    const { result } = renderHook(() => useAccountLifecycle("acc_secondary"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toMatchObject({
      accountId: "acc_secondary",
      endsOn: null,
      renewsOn: null,
      cancellationStatus: null,
      revision: 0,
    });
  });

  it("does not request anything without an account id", async () => {
    const queryClient = createTestQueryClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const { result } = renderHook(() => useAccountLifecycle(null), {
      wrapper: createWrapper(queryClient),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("useAccountLifecycleMutation", () => {
  it("sends the full replacement and stores the saved revision in the cache", async () => {
    const queryClient = createTestQueryClient();
    const bodies: unknown[] = [];
    server.use(
      http.put("/api/accounts/:accountId/lifecycle", async ({ params, request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          createAccountLifecycle({
            accountId: String(params.accountId),
            endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
            renewsOn: null,
            cancellationStatus: "cancelled",
            revision: 1,
            updatedAt: "2026-10-05T10:00:00Z",
          }),
        );
      }),
    );

    const { result } = renderHook(() => useAccountLifecycleMutation(), {
      wrapper: createWrapper(queryClient),
    });

    const saved = await result.current.mutateAsync({
      accountId: "acc_primary",
      payload: {
        endsOn: { precision: "date", date: "2026-10-12" },
        renewsOn: null,
        cancellationStatus: "cancelled",
        expectedRevision: 0,
      },
    });

    expect(bodies).toEqual([
      {
        endsOn: { precision: "date", date: "2026-10-12" },
        renewsOn: null,
        cancellationStatus: "cancelled",
        expectedRevision: 0,
      },
    ]);
    expect(saved.revision).toBe(1);
    expect(queryClient.getQueryData(LIFECYCLE_KEY)).toEqual(saved);
    expect(toastMocks.success).toHaveBeenCalledWith("Lifecycle details saved");
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("reloads the stored values when the save was based on a stale revision", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAccountLifecycleMutation(), {
      wrapper: createWrapper(queryClient),
    });
    const payload = {
      endsOn: { precision: "date" as const, date: "2026-10-12" },
      renewsOn: null,
      cancellationStatus: null,
      expectedRevision: 0,
    };

    const first = await result.current.mutateAsync({ accountId: "acc_primary", payload });
    expect(first.revision).toBe(1);

    const stale = result.current.mutateAsync({ accountId: "acc_primary", payload });
    await expect(stale).rejects.toBeInstanceOf(ApiError);
    await expect(stale).rejects.toMatchObject({ status: 409, code: "account_lifecycle_conflict" });

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: LIFECYCLE_KEY });
    });
    expect(toastMocks.error).toHaveBeenCalledWith(
      "Lifecycle details changed elsewhere. The latest values were reloaded.",
    );
  });

  it("surfaces other failures without reloading", async () => {
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useAccountLifecycleMutation(), {
      wrapper: createWrapper(queryClient),
    });

    await expect(
      result.current.mutateAsync({
        accountId: "acc_missing",
        payload: { endsOn: null, renewsOn: null, cancellationStatus: null, expectedRevision: 0 },
      }),
    ).rejects.toMatchObject({ status: 404, code: "account_not_found" });

    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith("Account not found"));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("rejects a revision beyond the stored range before it reaches the server", async () => {
    const queryClient = createTestQueryClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useAccountLifecycleMutation(), {
      wrapper: createWrapper(queryClient),
    });

    await expect(
      result.current.mutateAsync({
        accountId: "acc_primary",
        payload: { endsOn: null, renewsOn: null, cancellationStatus: null, expectedRevision: 2147483647 },
      }),
    ).rejects.toThrow();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("gives a re-created account id of the mock server empty notes, like the API", async () => {
    const imported = await importAccount(new File(["{}"], "auth.json"));
    await updateAccountLifecycle(imported.accountId, {
      endsOn: { precision: "date", date: "2026-10-12" },
      renewsOn: null,
      cancellationStatus: null,
      expectedRevision: 0,
    });
    await deleteAccount(imported.accountId);

    const reimported = await importAccount(new File(["{}"], "auth.json"));
    expect(reimported.accountId).toBe(imported.accountId);

    const lifecycle = await getAccountLifecycle(reimported.accountId);
    expect(lifecycle.revision).toBe(0);
    expect(lifecycle.endsOn).toBeNull();
  });

  it("rejects an invalid payload before it reaches the server", async () => {
    const queryClient = createTestQueryClient();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useAccountLifecycleMutation(), {
      wrapper: createWrapper(queryClient),
    });

    await expect(
      result.current.mutateAsync({
        accountId: "acc_primary",
        payload: {
          endsOn: { precision: "date", date: "2026-02-30" },
          renewsOn: null,
          cancellationStatus: null,
          expectedRevision: 0,
        },
      }),
    ).rejects.toThrow();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});
