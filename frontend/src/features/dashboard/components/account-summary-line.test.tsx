import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountLifecycleDate } from "@/features/accounts/schemas";
import { AccountSummaryLine } from "@/features/dashboard/components/account-summary-line";
import { accountLifecycleEntry, type AccountLifecycleEntry } from "@/features/dashboard/lifecycle";
import { createAccountLifecycle, createAccountSummary } from "@/test/mocks/factories";

const NOW = new Date("2026-10-10T12:00:00Z");

function civil(date: string): AccountLifecycleDate {
  return { precision: "date", date, time: null, timezone: null };
}

function ready(accountId: string, endsOn: AccountLifecycleDate | null, renewsOn: AccountLifecycleDate | null) {
  return accountLifecycleEntry(
    accountId,
    { data: createAccountLifecycle({ accountId, endsOn, renewsOn }), failed: false },
    NOW,
  );
}

describe("AccountSummaryLine", () => {
  it("renders registered, active, and unavailable counts for mixed statuses", () => {
    render(
      <AccountSummaryLine
        accounts={[
          createAccountSummary({ accountId: "acc-1", status: "active" }),
          createAccountSummary({ accountId: "acc-2", status: "paused" }),
          createAccountSummary({ accountId: "acc-3", status: "rate_limited" }),
        ]}
      />,
    );

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /3.*registered.*1.*active.*2.*unavailable/,
    );
  });

  it("shows zero unavailable when all accounts are active", () => {
    render(
      <AccountSummaryLine
        accounts={[
          createAccountSummary({ accountId: "acc-1", status: "active" }),
          createAccountSummary({ accountId: "acc-2", status: "active" }),
        ]}
      />,
    );

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /2.*registered.*2.*active.*0.*unavailable/,
    );
  });

  it("renders zero counts for an empty dashboard account list", () => {
    render(<AccountSummaryLine accounts={[]} />);

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /0.*registered.*0.*active.*0.*unavailable/,
    );
  });
});

describe("AccountSummaryLine subscription dates", () => {
  const accounts = ["acc-1", "acc-2", "acc-3"].map((accountId) => createAccountSummary({ accountId, status: "active" }));

  beforeEach(() => {
    vi.stubEnv("TZ", "UTC");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("counts each account once by its most urgent date and keeps the availability counts", () => {
    const lifecycle = new Map<string, AccountLifecycleEntry>([
      ["acc-1", ready("acc-1", civil("2026-10-01"), civil("2026-10-13"))],
      ["acc-2", ready("acc-2", null, civil("2026-10-10"))],
      ["acc-3", ready("acc-3", civil("2026-10-12"), null)],
    ]);
    render(<AccountSummaryLine accounts={accounts} lifecycle={lifecycle} />);

    expect(screen.getByTestId("dashboard-account-summary-line")).toHaveTextContent(
      /3.*registered.*3.*active.*0.*unavailable/,
    );
    const summary = screen.getByTestId("dashboard-account-date-summary");
    expect(summary).toHaveTextContent("Subscription dates:");
    expect(summary).toHaveTextContent(/1\s*passed/);
    expect(summary).toHaveTextContent(/1\s*due today/);
    expect(summary).toHaveTextContent(/1\s*due within 7 days/);
    expect(summary).not.toHaveTextContent(/unknown/);
  });

  it("says nothing is due once every read settled without attention", () => {
    const lifecycle = new Map<string, AccountLifecycleEntry>([
      ["acc-1", ready("acc-1", null, civil("2026-10-31"))],
      ["acc-2", ready("acc-2", null, null)],
      ["acc-3", ready("acc-3", civil("2026-10-18"), null)],
    ]);
    render(<AccountSummaryLine accounts={accounts} lifecycle={lifecycle} />);

    expect(screen.getByTestId("dashboard-account-date-summary")).toHaveTextContent(
      "Subscription dates:none due within 7 days",
    );
  });

  it("counts unreadable notes as unknown and waits for reads still loading", () => {
    const { rerender } = render(
      <AccountSummaryLine
        accounts={accounts}
        lifecycle={
          new Map<string, AccountLifecycleEntry>([
            ["acc-1", { status: "loading" }],
            ["acc-2", { status: "loading" }],
            ["acc-3", ready("acc-3", null, null)],
          ])
        }
      />,
    );

    expect(screen.queryByTestId("dashboard-account-date-summary")).not.toBeInTheDocument();

    rerender(
      <AccountSummaryLine
        accounts={accounts}
        lifecycle={
          new Map<string, AccountLifecycleEntry>([
            ["acc-1", { status: "unavailable" }],
            ["acc-2", { status: "loading" }],
            ["acc-3", ready("acc-3", null, null)],
          ])
        }
      />,
    );

    const summary = screen.getByTestId("dashboard-account-date-summary");
    expect(summary).toHaveTextContent(/1\s*unknown/);
    expect(summary).not.toHaveTextContent(/none due/);
  });

  it("shows no date summary without a lifecycle map", () => {
    render(<AccountSummaryLine accounts={accounts} lifecycle={null} />);

    expect(screen.queryByTestId("dashboard-account-date-summary")).not.toBeInTheDocument();
  });
});
