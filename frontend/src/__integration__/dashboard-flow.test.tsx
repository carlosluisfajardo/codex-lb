import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { BrowserRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import type { AccountLifecycle } from "@/features/accounts/schemas";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { useDashboardPreferencesStore } from "@/hooks/use-dashboard-preferences";
import {
  ADMIN_PERMISSIONS,
  VIEWER_PERMISSIONS,
  createAccountLifecycle,
  createAccountSummary,
  createDashboardAuthSession,
  createDashboardOverview,
  createDashboardProjections,
  createConversationEntry,
  createConversationsResponse,
  createDefaultRequestLogs,
  createRequestLogEntry,
  createRequestLogFilterOptions,
  createRequestLogsResponse,
} from "@/test/mocks/factories";
import { queryClient } from "@/lib/query-client";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

if (!HTMLElement.prototype.scrollIntoView) {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => {},
  });
}

const REQUEST_LOG_OUTAGE_MESSAGE = "forced request-log outage";

afterEach(() => {
  queryClient.clear();
});

describe("dashboard flow integration", () => {
  it("loads dashboard, refetches overview on overview-timeframe changes, and keeps request-log refetches isolated", async () => {
    const user = userEvent.setup({ delay: null });
    const logs = createDefaultRequestLogs();

    let overviewCalls = 0;
    let requestLogCalls = 0;
    const overviewTimeframes: string[] = [];

    server.use(
      http.get("/api/dashboard/overview", ({ request }) => {
        overviewCalls += 1;
        const timeframe = (new URL(request.url).searchParams.get("timeframe") ?? "7d") as "1d" | "7d" | "30d";
        overviewTimeframes.push(timeframe);
        return HttpResponse.json(createDashboardOverview({
          timeframe:
            timeframe === "1d"
              ? { key: "1d", windowMinutes: 1440, bucketSeconds: 3600, bucketCount: 24 }
              : timeframe === "30d"
                ? { key: "30d", windowMinutes: 43200, bucketSeconds: 86400, bucketCount: 30 }
                : { key: "7d", windowMinutes: 10080, bucketSeconds: 21600, bucketCount: 28 },
        }));
      }),
      http.get("/api/request-logs", ({ request }) => {
        requestLogCalls += 1;
        const url = new URL(request.url);
        const limit = Number(url.searchParams.get("limit") ?? "25");
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const page = logs.slice(offset, Math.min(logs.length, offset + limit));
        return HttpResponse.json(createRequestLogsResponse(page, 100, true));
      }),
      http.get("/api/request-logs/options", () =>
        HttpResponse.json(createRequestLogFilterOptions()),
      ),
    );

    window.history.pushState({}, "", "/dashboard");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Request Logs" })).toBeInTheDocument();

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(0);
      expect(requestLogCalls).toBeGreaterThan(0);
    });

    const overviewAfterLoad = overviewCalls;
    const logsAfterLoad = requestLogCalls;
    expect(overviewTimeframes.at(-1)).toBe("7d");

    act(() => {
      window.history.pushState({}, "", "/dashboard?overviewTimeframe=30d");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(overviewAfterLoad);
    });
    expect(requestLogCalls).toBe(logsAfterLoad);
    expect(overviewTimeframes.at(-1)).toBe("30d");

    const overviewAfterTimeframe = overviewCalls;

    await user.type(
      screen.getByPlaceholderText("Search request id, account, API key, model, error..."),
      "quota",
    );

    await waitFor(() => {
      expect(requestLogCalls).toBeGreaterThan(logsAfterLoad);
    });
    expect(overviewCalls).toBe(overviewAfterTimeframe);

    const logsAfterFilter = requestLogCalls;
    await user.click(screen.getByRole("button", { name: "Next page" }));

    await waitFor(() => {
      expect(requestLogCalls).toBeGreaterThan(logsAfterFilter);
    });
    expect(overviewCalls).toBe(overviewAfterTimeframe);
  });

  it("preserves healthy overview through initial request-log failure and Retry recovery", async () => {
    const user = userEvent.setup({ delay: null });
    let overviewCalls = 0;
    let projectionsCalls = 0;
    let requestLogCalls = 0;
    let optionsCalls = 0;
    let requestLogsAvailable = false;
    let releaseRecoveredResponse = () => {};
    const recoveredResponseGate = new Promise<void>((resolve) => {
      releaseRecoveredResponse = resolve;
    });
    const recoveredLog = createRequestLogEntry({
      requestId: "req_recovered",
      accountId: "acc_healthy_overview",
      apiKeyName: "Recovered API Key",
    });

    const healthyAccount = createAccountSummary({
      accountId: "acc_healthy_overview",
      chatgptAccountId: "chatgpt_acc_healthy_overview",
      email: "healthy-overview@example.com",
      displayName: "Healthy Overview Account",
      usage: {
        primaryRemainingPercent: 61.3,
        secondaryRemainingPercent: 88.3,
        monthlyRemainingPercent: null,
      },
      capacityCreditsPrimary: 9_876,
      remainingCreditsPrimary: 6_055,
      remainingCreditsSecondary: 6_675.48,
    });
    const baseOverview = createDashboardOverview({ accounts: [healthyAccount] });
    const overview = createDashboardOverview({
      accounts: [healthyAccount],
      summary: {
        ...baseOverview.summary,
        primaryWindow: {
          ...baseOverview.summary.primaryWindow,
          remainingPercent: 61.3,
          capacityCredits: 9_876,
          remainingCredits: 6_055,
        },
        metrics: {
          ...baseOverview.summary.metrics!,
          requests: 424_242,
        },
      },
      windows: {
        ...baseOverview.windows,
        primary: {
          ...baseOverview.windows.primary,
          accounts: [
            {
              accountId: healthyAccount.accountId,
              remainingPercentAvg: 61.3,
              capacityCredits: 9_876,
              remainingCredits: 6_055,
            },
          ],
        },
      },
    });

    server.use(
      http.get("/api/dashboard/overview", () => {
        overviewCalls += 1;
        return HttpResponse.json(overview);
      }),
      http.get("/api/dashboard/projections", () => {
        projectionsCalls += 1;
        return HttpResponse.json(createDashboardProjections());
      }),
      http.get("/api/request-logs/options", () => {
        optionsCalls += 1;
        return HttpResponse.json(createRequestLogFilterOptions());
      }),
      http.get("/api/request-logs", async () => {
        requestLogCalls += 1;
        if (!requestLogsAvailable) {
          return HttpResponse.json(
            {
              error: {
                code: "forced_request_log_outage",
                message: REQUEST_LOG_OUTAGE_MESSAGE,
              },
            },
            { status: 500 },
          );
        }

        await recoveredResponseGate;
        return HttpResponse.json(createRequestLogsResponse([recoveredLog], 1, false));
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    const requestLogsHeading = await screen.findByRole("heading", { name: "Request Logs" });
    const requestLogsSection = requestLogsHeading.closest("section");

    expect(requestLogsSection).not.toBeNull();
    const requestLogs = within(requestLogsSection as HTMLElement);
    const errorAlert = await requestLogs.findByRole("alert");

    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(0);
      expect(projectionsCalls).toBeGreaterThan(0);
      expect(requestLogCalls).toBeGreaterThan(1);
      expect(optionsCalls).toBeGreaterThan(0);
    });

    const expectHealthySurfaces = () => {
      expect(screen.getByText("Requests (7d)")).toBeInTheDocument();
      expect(screen.getByText("424.24K")).toBeInTheDocument();
      expect(screen.getByText("Account burn projection (5h/7d)")).toBeInTheDocument();
      expect(screen.getByText("0.4 / 0.1")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "5-Hour Credits" })).toBeInTheDocument();
      expect(screen.getByText("6,055")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Enable limit warm-up for Healthy Overview Account" }),
      ).toBeInTheDocument();
    };

    await waitFor(expectHealthySurfaces);
    expect(screen.getByRole("heading", { name: "Accounts" })).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(errorAlert).toHaveTextContent(REQUEST_LOG_OUTAGE_MESSAGE);
    expect(requestLogs.getByRole("button", { name: "Retry" })).toBeInTheDocument();

    const overviewCallsBeforeRetry = overviewCalls;
    const projectionsCallsBeforeRetry = projectionsCalls;
    const requestLogCallsBeforeRetry = requestLogCalls;
    const optionsCallsBeforeRetry = optionsCalls;
    const retryButton = requestLogs.getByRole("button", { name: "Retry" });
    requestLogsAvailable = true;

    retryButton.focus();
    expect(retryButton).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(requestLogCalls).toBe(requestLogCallsBeforeRetry + 1);
    });

    expectHealthySurfaces();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(overviewCalls).toBe(overviewCallsBeforeRetry);
    expect(projectionsCalls).toBe(projectionsCallsBeforeRetry);
    expect(optionsCalls).toBe(optionsCallsBeforeRetry);

    releaseRecoveredResponse();
    expect(await screen.findByText("Recovered API Key")).toBeInTheDocument();
    expect(screen.queryByText(REQUEST_LOG_OUTAGE_MESSAGE)).not.toBeInTheDocument();
    expectHealthySurfaces();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
    expect(overviewCalls).toBe(overviewCallsBeforeRetry);
    expect(projectionsCalls).toBe(projectionsCallsBeforeRetry);
    expect(optionsCalls).toBe(optionsCallsBeforeRetry);
  });

  it("keeps retained request-log rows through failed refresh and Retry recovery", async () => {
    const user = userEvent.setup({ delay: null });
    let requestLogsAvailable = true;
    let recovered = false;
    let requestLogCalls = 0;
    const retainedLog = createRequestLogEntry({
      requestId: "req_retained_refresh",
      apiKeyName: "Retained API Key",
    });
    const recoveredLog = createRequestLogEntry({
      requestId: "req_recovered_refresh",
      apiKeyName: "Recovered API Key",
    });

    server.use(
      http.get("/api/request-logs", () => {
        requestLogCalls += 1;
        if (!requestLogsAvailable) {
          return HttpResponse.json(
            {
              error: {
                code: "forced_background_refresh_failure",
                message: REQUEST_LOG_OUTAGE_MESSAGE,
              },
            },
            { status: 503 },
          );
        }
        return HttpResponse.json(
          createRequestLogsResponse([recovered ? recoveredLog : retainedLog], 1, false),
        );
      }),
    );

    window.history.pushState({}, "", "/dashboard");
    const { queryClient: testQueryClient } = renderWithProviders(<App />);

    expect(await screen.findByText("Retained API Key")).toBeInTheDocument();
    const section = screen.getByRole("heading", { name: "Request Logs" }).closest("section");
    expect(section).not.toBeNull();
    const requestLogs = within(section as HTMLElement);
    expect(requestLogs.getByRole("table")).toBeVisible();

    const callsBeforeRefresh = requestLogCalls;
    requestLogsAvailable = false;
    await act(async () => {
      await testQueryClient.invalidateQueries({
        queryKey: ["dashboard", "request-logs"],
      });
    });
    await waitFor(() => expect(requestLogCalls).toBeGreaterThan(callsBeforeRefresh));
    const alert = await requestLogs.findByRole("alert");

    expect(alert).toHaveTextContent(REQUEST_LOG_OUTAGE_MESSAGE);
    expect(requestLogs.getByRole("table")).toBeVisible();
    expect(requestLogs.getByText("Retained API Key")).toBeVisible();

    requestLogsAvailable = true;
    recovered = true;
    const retry = requestLogs.getByRole("button", { name: "Retry" });
    retry.focus();
    await user.keyboard("{Enter}");

    expect(await requestLogs.findByText("Recovered API Key")).toBeVisible();
    expect(requestLogs.queryByText("Retained API Key")).not.toBeInTheDocument();
    expect(requestLogs.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("switches to conversations without reinterpreting request-log URL state", async () => {
    const user = userEvent.setup({ delay: null });
    server.use(
      http.get("/api/conversations", () =>
        HttpResponse.json(
          createConversationsResponse([
            createConversationEntry({ conversationId: "opencode_conversation" }),
          ], 1, false),
        ),
      ),
    );
    window.history.pushState(
      {},
      "",
      "/dashboard?search=requestlog&limit=10&offset=25&conversationSearch=opencode&conversationLimit=15&conversationOffset=7",
    );

    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Request Logs" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Request Logs" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Conversations" }));

    expect(await screen.findByText("opencode_conversation")).toBeInTheDocument();
    expect(window.location.search).toContain("view=conversations");
    expect(window.location.search).toContain("search=requestlog");
    expect(window.location.search).toContain("limit=10");
    expect(window.location.search).toContain("offset=25");
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(window.location.search).toContain("conversationSearch=opencode");
    expect(window.location.search).toContain("conversationLimit=15");
    expect(window.location.search).toContain("conversationOffset=7");

    await user.click(screen.getByRole("button", { name: "Conversations" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Request Logs" }));

    await waitFor(() => expect(window.location.search).not.toContain("view=conversations"));
    expect(window.location.search).toContain("search=requestlog");
    expect(window.location.search).toContain("limit=10");
    expect(window.location.search).toContain("offset=25");
    expect(window.location.search).toContain("conversationSearch=opencode");
    expect(window.location.search).toContain("conversationLimit=15");
    expect(window.location.search).toContain("conversationOffset=7");
  });

  it("shows saved subscription dates with calendar-day countdowns in the cards, the list and the summary without writing", async () => {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    try {
      const user = userEvent.setup({ delay: null });
      const renewing = createAccountSummary({
        accountId: "acc_renewing",
        email: "renewing@example.com",
        displayName: "Renewing account",
      });
      const lapsed = createAccountSummary({
        accountId: "acc_lapsed",
        email: "lapsed@example.com",
        displayName: "Lapsed account",
        usage: { primaryRemainingPercent: 82, secondaryRemainingPercent: 64, monthlyRemainingPercent: null },
      });
      const lifecycles: Record<string, AccountLifecycle> = {
        acc_renewing: createAccountLifecycle({
          accountId: "acc_renewing",
          renewsOn: { precision: "date", date: "2026-10-31", time: null, timezone: null },
        }),
        acc_lapsed: createAccountLifecycle({
          accountId: "acc_lapsed",
          endsOn: { precision: "datetime", date: "2026-10-12", time: "09:30", timezone: "America/New_York" },
          renewsOn: { precision: "date", date: "2026-10-07", time: null, timezone: null },
        }),
      };
      const lifecycleReads: string[] = [];
      const lifecycleWrites: string[] = [];
      server.use(
        http.get("/api/dashboard/overview", () =>
          HttpResponse.json(createDashboardOverview({ accounts: [renewing, lapsed] })),
        ),
        http.get("/api/accounts/:accountId/lifecycle", ({ params }) => {
          const accountId = String(params.accountId);
          lifecycleReads.push(accountId);
          return HttpResponse.json(lifecycles[accountId]);
        }),
        http.put("/api/accounts/:accountId/lifecycle", ({ params }) => {
          lifecycleWrites.push(String(params.accountId));
          return HttpResponse.json({ error: { code: "unexpected", message: "unexpected write" } }, { status: 500 });
        }),
      );

      window.history.pushState({}, "", "/dashboard");
      renderWithProviders(<App />);

      const cards = await screen.findByTestId("dashboard-account-cards");
      const cardFor = (name: string) => {
        const card = Array.from(cards.children).find((element) =>
          within(element as HTMLElement).queryByText(name),
        );
        if (!card) {
          throw new Error(`No dashboard card for ${name}`);
        }
        return within(card as HTMLElement);
      };

      const renewingCard = cardFor("Renewing account");
      expect(await renewingCard.findByText("in 21 days")).toBeInTheDocument();
      expect(renewingCard.getByText("Renews on")).toBeInTheDocument();
      expect(renewingCard.getByText("Oct 31, 2026")).toBeInTheDocument();
      expect(renewingCard.getByText("Date only")).toBeInTheDocument();
      expect(renewingCard.getByText("Ends on")).toBeInTheDocument();
      expect(renewingCard.getByText("Not set")).toBeInTheDocument();

      const lapsedCard = cardFor("Lapsed account");
      expect(lapsedCard.getByText("Oct 12, 2026")).toBeInTheDocument();
      expect(lapsedCard.getByText("09:30 America/New_York")).toBeInTheDocument();
      expect(lapsedCard.getByText("in 2 days")).toBeInTheDocument();
      expect(lapsedCard.getByText("Oct 7, 2026")).toBeInTheDocument();
      expect(lapsedCard.getByText("passed 3 days ago · needs confirmation")).toBeInTheDocument();
      expect(lapsedCard.getByText("64%")).toBeInTheDocument();
      expect(lapsedCard.getByText("Active")).toBeInTheDocument();
      expect(cards).not.toHaveTextContent(/expired/i);

      expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
        /2.*registered.*2.*active.*0.*unavailable/,
      );
      const dateSummary = screen.getByTestId("dashboard-account-date-summary");
      expect(dateSummary).toHaveTextContent(/Subscription dates/);
      expect(dateSummary).toHaveTextContent(/1\s*passed/);
      expect(dateSummary).not.toHaveTextContent(/due/);

      await user.click(screen.getByRole("radio", { name: "View accounts as list" }));
      const list = await screen.findByTestId("dashboard-account-list");
      expect(within(list).getByText("Subscription dates")).toBeInTheDocument();
      const lapsedRow = within(
        within(list).getAllByTestId("account-list-row").find((row) => within(row).queryByText("Lapsed account"))!,
      );
      expect(lapsedRow.getByText("09:30 America/New_York")).toBeInTheDocument();
      expect(lapsedRow.getByText("in 2 days")).toBeInTheDocument();
      expect(lapsedRow.getByText("passed 3 days ago · needs confirmation")).toBeInTheDocument();

      expect([...lifecycleReads].sort()).toEqual(["acc_lapsed", "acc_renewing"]);
      expect(lifecycleWrites).toEqual([]);
    } finally {
      act(() => {
        useDashboardPreferencesStore.setState({ accountViewMode: "cards" });
      });
      vi.useRealTimers();
      vi.unstubAllEnvs();
    }
  });

  describe("subscription date access", () => {
    const account = createAccountSummary({
      accountId: "acc_noted",
      email: "noted@example.com",
      displayName: "Noted account",
    });

    function serveSession(permissions: string[]): string[] {
      const lifecycleReads: string[] = [];
      server.use(
        http.get("/api/dashboard-auth/session", () =>
          HttpResponse.json(
            createDashboardAuthSession({
              authenticated: true,
              passwordRequired: false,
              totpConfigured: false,
              role: "guest",
              permissions,
              guestAccessEnabled: true,
              guestPasswordRequired: false,
            }),
          ),
        ),
        http.get("/api/dashboard/overview", () => HttpResponse.json(createDashboardOverview({ accounts: [account] }))),
        http.get("/api/accounts/:accountId/lifecycle", ({ params }) => {
          lifecycleReads.push(String(params.accountId));
          return HttpResponse.json(
            createAccountLifecycle({
              accountId: String(params.accountId),
              renewsOn: { precision: "date", date: "2026-10-31", time: null, timezone: null },
            }),
          );
        }),
      );
      return lifecycleReads;
    }

    afterEach(() => {
      useAuthStore.setState({ role: "admin", permissions: ADMIN_PERMISSIONS, canWrite: true, initialized: false });
    });

    it("shows subscription dates to a viewer with accounts:read but no write access", async () => {
      const lifecycleReads = serveSession(VIEWER_PERMISSIONS);

      window.history.pushState({}, "", "/dashboard");
      renderWithProviders(<App />);

      const cards = await screen.findByTestId("dashboard-account-cards");
      expect(await within(cards).findByText("Oct 31, 2026")).toBeInTheDocument();
      expect(within(cards).getByText("Renews on")).toBeInTheDocument();
      expect(lifecycleReads).toEqual(["acc_noted"]);
    });

    it("neither reads nor shows subscription dates without accounts:read", async () => {
      const lifecycleReads = serveSession(["read", "dashboard:read:all"]);

      window.history.pushState({}, "", "/dashboard");
      renderWithProviders(<App />);

      const cards = await screen.findByTestId("dashboard-account-cards");
      expect(within(cards).getByText("Noted account")).toBeInTheDocument();
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(within(cards).queryByText("Renews on")).not.toBeInTheDocument();
      expect(screen.queryByTestId("dashboard-account-date-summary")).not.toBeInTheDocument();
      expect(lifecycleReads).toEqual([]);
    });
  });

  it("refetches the overview (stat boxes) when the conversation timeframe changes", async () => {
    const user = userEvent.setup({ delay: null });

    let overviewCalls = 0;
    const overviewTimeframes: string[] = [];

    server.use(
      http.get("/api/dashboard/overview", ({ request }) => {
        overviewCalls += 1;
        const timeframe = (new URL(request.url).searchParams.get("timeframe") ?? "7d") as string;
        overviewTimeframes.push(timeframe);
        return HttpResponse.json(createDashboardOverview({
          timeframe:
            timeframe === "1d"
              ? { key: "1d", windowMinutes: 1440, bucketSeconds: 3600, bucketCount: 24 }
              : timeframe === "30d"
                ? { key: "30d", windowMinutes: 43200, bucketSeconds: 86400, bucketCount: 30 }
                : { key: "7d", windowMinutes: 10080, bucketSeconds: 21600, bucketCount: 28 },
        }));
      }),
      http.get("/api/conversations", () =>
        HttpResponse.json(createConversationsResponse([
          createConversationEntry({ conversationId: "opencode_conversation" }),
        ], 1, false)),
      ),
    );

    window.history.pushState({}, "", "/dashboard?view=conversations&overviewTimeframe=1d");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    await waitFor(() => expect(overviewCalls).toBeGreaterThan(0));
    expect(overviewTimeframes.at(-1)).toBe("7d");

    const overviewAfterLoad = overviewCalls;

    // Change the date range from the conversations-mode selector (top right).
    const timeframeSelect = screen.getByRole("combobox", { name: "Conversation timeframe" });
    await user.click(timeframeSelect);
    await user.click(await screen.findByRole("option", { name: "30d" }));

    // Regression: the overview query MUST refetch with the new timeframe so the
    // stat boxes (requests/tokens/cost/etc.) update alongside the conversation list.
    await waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(overviewAfterLoad);
    });
    expect(overviewTimeframes.at(-1)).toBe("30d");
    expect(window.location.search).toContain("conversationTimeframe=30d");
    expect(window.location.search).toContain("overviewTimeframe=1d");
    expect(window.location.search).not.toContain("overviewTimeframe=30d");

    await user.click(screen.getByRole("button", { name: "Conversations" }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Request Logs" }));

    await waitFor(() => {
      expect(overviewTimeframes.at(-1)).toBe("1d");
    });
  });
});
