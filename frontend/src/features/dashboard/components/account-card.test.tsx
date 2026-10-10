import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountLifecycleDate } from "@/features/accounts/schemas";
import { AccountCard } from "@/features/dashboard/components/account-card";
import { accountLifecycleEntry, type AccountLifecycleEntry } from "@/features/dashboard/lifecycle";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { usePrivacyStore } from "@/hooks/use-privacy";
import { createAccountLifecycle, createAccountSummary } from "@/test/mocks/factories";

afterEach(() => {
  act(() => {
    usePrivacyStore.setState({ blurred: false });
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
  });
  vi.unstubAllEnvs();
});

describe("AccountCard", () => {
  it("renders both 5h and weekly quota bars for regular accounts", () => {
    const account = createAccountSummary();
    render(<AccountCard account={account} />);

    expect(screen.getByText("Plus")).toBeInTheDocument();
    expect(screen.getByText("5h")).toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
  });

  it("keeps the last quota visible while a refreshed value is temporarily unknown", () => {
    const account = createAccountSummary({
      usage: { primaryRemainingPercent: 64, secondaryRemainingPercent: 73 },
      windowMinutesPrimary: 300,
      windowMinutesSecondary: 10_080,
    });
    const { rerender } = render(<AccountCard account={account} />);

    expect(screen.getByText("64%")).toBeInTheDocument();

    rerender(
      <AccountCard
        account={{
          ...account,
          usage: { primaryRemainingPercent: null, secondaryRemainingPercent: 73 },
          windowMinutesPrimary: null,
          resetAtPrimary: null,
        }}
      />,
    );

    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText("5h")).toBeInTheDocument();
  });

  it("hides 5h quota bar for weekly-only accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: 76,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: 10_080,
    });

    render(<AccountCard account={account} />);

    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.getByText("Weekly")).toBeInTheDocument();
  });

  it("shows Monthly only for monthly-only free accounts", () => {
    const account = createAccountSummary({
      planType: "free",
      usage: {
        primaryRemainingPercent: null,
        secondaryRemainingPercent: null,
        monthlyRemainingPercent: 76,
      },
      windowMinutesPrimary: null,
      windowMinutesSecondary: null,
      windowMinutesMonthly: 43_200,
      resetAtPrimary: null,
      resetAtSecondary: null,
      resetAtMonthly: "2026-01-31T00:00:00.000Z",
    });

    render(<AccountCard account={account} />);

    expect(screen.getByText("Monthly")).toBeInTheDocument();
    expect(screen.queryByText("5h")).not.toBeInTheDocument();
    expect(screen.queryByText("Weekly")).not.toBeInTheDocument();
  });

  it("labels staggered idle warm-up attempts as 5h", () => {
    const attemptedAt = new Date("2026-06-03T12:00:00Z").toISOString();
    const account = createAccountSummary({
      limitWarmupEnabled: true,
      limitWarmup: {
        window: "primary_idle",
        resetAt: 18_000,
        status: "succeeded",
        model: "gpt-5.1-codex-mini",
        attemptedAt,
        completedAt: attemptedAt,
        errorCode: null,
        errorMessage: null,
      },
    });

    render(<AccountCard account={account} />);

    expect(
      screen.getByText((text) => text.includes("Succeeded | 5h | Gpt-5.1-codex-mini")),
    ).toBeInTheDocument();
  });

  it("blurs the dashboard card title when privacy mode is enabled", () => {
    act(() => {
      usePrivacyStore.setState({ blurred: true });
    });
    const account = createAccountSummary({
      displayName: "AWS Account MSP",
      email: "aws-account@example.com",
    });

    const { container } = render(<AccountCard account={account} />);

    expect(screen.getByText("AWS Account MSP")).toBeInTheDocument();
    expect(container.querySelector(".privacy-blur")).not.toBeNull();
  });

  it("renders subscription and purchased credits separately", () => {
    const account = createAccountSummary({
      creditsBalance: 0,
      remainingCreditsSecondary: 5_065.2,
    });

    render(<AccountCard account={account} />);

    expect(screen.getByText("Subscription quota:")).toBeInTheDocument();
    expect(screen.getByText("5065.20")).toBeInTheDocument();
    expect(screen.getByText("Purchased credits:")).toBeInTheDocument();
    expect(screen.getByText("0.00")).toBeInTheDocument();
  });

  it("applies unlimited only to purchased credits", () => {
    const account = createAccountSummary({
      creditsUnlimited: true,
      creditsBalance: null,
      remainingCreditsSecondary: 5_065.2,
    });

    render(<AccountCard account={account} />);

    expect(screen.getByText("5065.20")).toBeInTheDocument();
    expect(screen.getByText("Unlimited")).toBeInTheDocument();
  });

  it("renders re-auth status and action for re-auth required accounts", () => {
    const account = createAccountSummary({ status: "reauth_required" });

    render(<AccountCard account={account} />);

    expect(screen.getByText("Re-auth required")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-auth" })).toBeInTheDocument();
  });

  it("disables the limit warm-up toggle for read-only guests", () => {
    const account = createAccountSummary({
      displayName: "Read Only Account",
      limitWarmupEnabled: false,
    });

    render(<AccountCard account={account} readOnly />);

    expect(screen.getByRole("button", { name: "Enable limit warm-up for Read Only Account" })).toBeDisabled();
  });

  it("shows reset action when reset credits are available", () => {
    const account = createAccountSummary({
      availableResetCredits: 2,
      resetCreditNearestExpiresAt: "2026-01-03T12:00:00.000Z",
    });

    render(<AccountCard account={account} />);

    expect(screen.getByRole("button", { name: "Reset (2)" })).toBeInTheDocument();
  });

  it("hides reset action when no reset credits are available", () => {
    const account = createAccountSummary({ availableResetCredits: 0 });

    render(<AccountCard account={account} />);

    expect(screen.queryByRole("button", { name: /Reset \(/ })).not.toBeInTheDocument();
  });

  it("disables reset action for paused accounts", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const account = createAccountSummary({
      accountId: "acc-paused",
      displayName: "Paused Account",
      status: "paused",
      availableResetCredits: 1,
      resetCreditNearestExpiresAt: "2026-01-03T12:00:00.000Z",
    });

    render(<AccountCard account={account} onAction={onAction} />);

    const resetButton = screen.getByRole("button", { name: "Reset (1)" });
    expect(resetButton).toBeDisabled();

    await user.click(resetButton);
    expect(onAction).not.toHaveBeenCalledWith(account, "reset-credit");
  });
});

describe("AccountCard subscription dates", () => {
  const now = new Date("2026-10-10T12:00:00Z");
  const civil = (date: string): AccountLifecycleDate => ({ precision: "date", date, time: null, timezone: null });
  const timed = (date: string, time: string, timezone: string): AccountLifecycleDate => ({
    precision: "datetime",
    date,
    time,
    timezone,
  });
  const entry = (endsOn: AccountLifecycleDate | null, renewsOn: AccountLifecycleDate | null): AccountLifecycleEntry =>
    accountLifecycleEntry(
      "acc_primary",
      { data: createAccountLifecycle({ accountId: "acc_primary", endsOn, renewsOn }), failed: false },
      now,
    );

  beforeEach(() => {
    vi.stubEnv("TZ", "UTC");
  });

  it("shows no subscription dates when the session may not read them", () => {
    render(<AccountCard account={createAccountSummary()} />);

    expect(screen.queryByLabelText("Subscription dates")).not.toBeInTheDocument();
    expect(screen.queryByText("Ends on")).not.toBeInTheDocument();
  });

  it("shows a date-only renewal with its marker and day count, and an unset end", () => {
    render(<AccountCard account={createAccountSummary()} lifecycle={entry(null, civil("2026-10-31"))} />);

    const dates = within(screen.getByLabelText("Subscription dates"));
    expect(dates.getByText("Ends on")).toBeInTheDocument();
    expect(dates.getByText("Not set")).toBeInTheDocument();
    expect(dates.getByText("Renews on")).toBeInTheDocument();
    expect(dates.getByText("Oct 31, 2026")).toBeInTheDocument();
    expect(dates.getByText("Date only")).toBeInTheDocument();
    expect(dates.getByText("in 21 days")).toHaveClass("text-muted-foreground");
  });

  it("keeps a timed end's time and timezone and marks a renewal date that passed", () => {
    render(
      <AccountCard
        account={createAccountSummary()}
        lifecycle={entry(timed("2026-10-12", "09:30", "America/New_York"), civil("2026-10-07"))}
      />,
    );

    const dates = within(screen.getByLabelText("Subscription dates"));
    expect(dates.getByText("Oct 12, 2026")).toBeInTheDocument();
    expect(dates.getByText("09:30 America/New_York")).toBeInTheDocument();
    expect(dates.getByText("in 2 days")).toHaveClass("text-amber-600");
    expect(dates.getByText("passed 3 days ago · needs confirmation")).toHaveClass("text-red-600");
    expect(screen.getByLabelText("Subscription dates")).not.toHaveTextContent(/expired/i);
  });

  it("marks today and an end that passed, in the ISO date format when chosen", () => {
    act(() => {
      useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
    });
    render(<AccountCard account={createAccountSummary()} lifecycle={entry(civil("2026-10-09"), civil("2026-10-10"))} />);

    const dates = within(screen.getByLabelText("Subscription dates"));
    expect(dates.getByText("2026-10-09")).toBeInTheDocument();
    expect(dates.getByText("passed 1 day ago")).toHaveClass("text-red-600");
    expect(dates.getByText("2026-10-10")).toBeInTheDocument();
    expect(dates.getByText("today")).toHaveClass("text-amber-600");
  });

  it("shows a value it cannot evaluate as entered with an unknown day count", () => {
    render(
      <AccountCard
        account={createAccountSummary()}
        lifecycle={entry(timed("2026-10-12", "09:30", "Mars/Olympus_Mons"), null)}
      />,
    );

    const dates = within(screen.getByLabelText("Subscription dates"));
    expect(dates.getByText("09:30 Mars/Olympus_Mons")).toBeInTheDocument();
    expect(dates.getByText("day count unknown")).toHaveClass("text-muted-foreground");
  });

  it("shows loading and unavailable reads instead of not set", () => {
    const { rerender } = render(<AccountCard account={createAccountSummary()} lifecycle={{ status: "loading" }} />);

    expect(screen.getAllByText("Loading…")).toHaveLength(2);
    expect(screen.queryByText("Not set")).not.toBeInTheDocument();

    rerender(<AccountCard account={createAccountSummary()} lifecycle={{ status: "unavailable" }} />);

    expect(screen.getAllByText("Unavailable")).toHaveLength(2);
    expect(screen.queryByText("Not set")).not.toBeInTheDocument();
  });

  it("leaves the status badge and quota as reported when a date passed", () => {
    render(
      <AccountCard
        account={createAccountSummary({
          status: "active",
          usage: { primaryRemainingPercent: 82, secondaryRemainingPercent: 64, monthlyRemainingPercent: null },
        })}
        lifecycle={entry(civil("2026-09-30"), null)}
      />,
    );

    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
  });
});
