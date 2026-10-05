import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountLifecyclePanel } from "@/features/accounts/components/account-lifecycle-panel";
import { browserTimeZone } from "@/features/accounts/lifecycle";
import type { AccountLifecycle, AccountSummary } from "@/features/accounts/schemas";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";
import { ApiError } from "@/lib/api-client";
import { createAccountLifecycle, createAccountSummary } from "@/test/mocks/factories";

const FORBIDDEN_CONTROLS = /reset|redeem|schedule|rank/i;

type PanelProps = {
  account?: AccountSummary;
  lifecycle?: AccountLifecycle | null;
  loading?: boolean;
  error?: string | null;
  busy?: boolean;
  readOnly?: boolean;
  onSave?: (accountId: string, payload: unknown) => Promise<unknown>;
};

function savedLifecycle(overrides: Partial<AccountLifecycle> = {}): AccountLifecycle {
  return createAccountLifecycle({
    accountId: "acc_primary",
    endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
    renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "America/New_York" },
    cancellationStatus: "not_cancelled",
    revision: 2,
    updatedAt: "2026-10-05T10:00:00Z",
    ...overrides,
  });
}

function panelElement(props: PanelProps = {}) {
  return (
    <AccountLifecyclePanel
      account={props.account ?? createAccountSummary({ accountId: "acc_primary", routingPolicy: "burn_first" })}
      lifecycle={"lifecycle" in props ? props.lifecycle : savedLifecycle()}
      loading={props.loading ?? false}
      error={props.error ?? null}
      busy={props.busy ?? false}
      readOnly={props.readOnly ?? false}
      onSave={props.onSave ?? vi.fn().mockResolvedValue(undefined)}
    />
  );
}

function renderPanel(props: PanelProps = {}) {
  return render(panelElement(props));
}

function panel() {
  return screen.getByRole("region", { name: "Subscription lifecycle" });
}

function row(label: string): HTMLElement {
  const term = within(panel()).getByText(label, { selector: "dt" });
  const container = term.parentElement;
  if (!container) {
    throw new Error(`No row for ${label}`);
  }
  return container;
}

beforeEach(() => {
  useDateDisplayFormatStore.setState({ dateDisplayFormat: "iso8601" });
});

afterEach(() => {
  useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
  vi.restoreAllMocks();
});

describe("AccountLifecyclePanel read view", () => {
  it("shows a civil date marked date only and unset fields as not set", () => {
    renderPanel({
      lifecycle: savedLifecycle({ renewsOn: null, cancellationStatus: null }),
    });

    expect(within(row("Ends on")).getByText("2026-10-12")).toBeInTheDocument();
    expect(within(row("Ends on")).getByText("Date only")).toBeInTheDocument();
    expect(within(row("Renews on")).getByText("Not set")).toBeInTheDocument();
    expect(within(row("Cancellation")).getByText("Not set")).toBeInTheDocument();
  });

  it("follows the default date display preference without shifting the civil date", () => {
    useDateDisplayFormatStore.setState({ dateDisplayFormat: "default" });
    renderPanel({ lifecycle: savedLifecycle({ renewsOn: null }) });

    expect(within(row("Ends on")).getByText("Oct 12, 2026")).toBeInTheDocument();
    expect(within(row("Ends on")).getByText("Date only")).toBeInTheDocument();
  });

  it("shows a local date and time with the time as entered and its timezone", () => {
    renderPanel({
      lifecycle: savedLifecycle({
        endsOn: { precision: "datetime", date: "2026-11-03", time: "09:30:15", timezone: "+02:00" },
        cancellationStatus: "cancelled",
      }),
    });

    expect(within(row("Renews on")).getByText("2026-11-03 09:30 America/New_York")).toBeInTheDocument();
    expect(within(row("Renews on")).queryByText("Date only")).not.toBeInTheDocument();
    expect(within(row("Ends on")).getByText("2026-11-03 09:30:15 +02:00")).toBeInTheDocument();
    expect(within(row("Cancellation")).getByText("Cancelled")).toBeInTheDocument();
  });

  it("shows the current routing policy read-only with a truthful note", () => {
    renderPanel();

    expect(within(row("Routing policy")).getByText("Burn first")).toBeInTheDocument();
    expect(
      within(panel()).getByText(
        "Routing policy decides the order in which eligible accounts take new requests. It does not override pause, quota, health or cooldown limits, and changing it does not move requests already in progress. Burn first accounts keep their sticky sessions when their budget runs low; Preserve accounts can be held back from opportunistic API keys. Lifecycle dates are notes and are not read by routing.",
      ),
    ).toBeInTheDocument();
    expect(within(panel()).queryByRole("combobox", { name: "Routing policy" })).not.toBeInTheDocument();
  });

  it("falls back to the normal routing policy label", () => {
    renderPanel({ account: createAccountSummary({ accountId: "acc_primary", routingPolicy: undefined }) });

    expect(within(row("Routing policy")).getByText("Normal")).toBeInTheDocument();
  });

  it("shows a loading line and keeps editing closed until values arrive", () => {
    renderPanel({ lifecycle: undefined, loading: true });

    expect(within(panel()).getByText("Loading lifecycle details…")).toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Edit lifecycle" })).toBeDisabled();
  });

  it("shows a load error", () => {
    renderPanel({ lifecycle: undefined, error: "Account not found" });

    expect(within(panel()).getByText("Lifecycle details could not be loaded: Account not found")).toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Edit lifecycle" })).toBeDisabled();
  });

  it("never shows another account's values", () => {
    renderPanel({ lifecycle: savedLifecycle({ accountId: "acc_other" }), loading: true });

    expect(within(panel()).queryByText("2026-10-12")).not.toBeInTheDocument();
    expect(within(panel()).getByText("Loading lifecycle details…")).toBeInTheDocument();
  });

  it("offers no edit control to viewers", () => {
    renderPanel({ readOnly: true });

    expect(within(row("Ends on")).getByText("2026-10-12")).toBeInTheDocument();
    expect(within(panel()).queryByRole("button", { name: "Edit lifecycle" })).not.toBeInTheDocument();
    expect(within(panel()).queryAllByRole("button")).toHaveLength(0);
  });
});

describe("AccountLifecyclePanel editing", () => {
  it("saves the full replacement with the revision read when editing started", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(savedLifecycle({ revision: 3 }));
    const { rerender } = renderPanel({ onSave });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    // A background refetch delivers a newer revision while the draft is open.
    rerender(panelElement({ onSave, lifecycle: savedLifecycle({ revision: 5 }) }));

    fireEvent.change(within(panel()).getByLabelText("Ends on date"), { target: { value: "2026-10-20" } });
    await user.click(within(panel()).getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith("acc_primary", {
      endsOn: { precision: "date", date: "2026-10-20" },
      renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "America/New_York" },
      cancellationStatus: "not_cancelled",
      expectedRevision: 2,
    });
    expect(await within(panel()).findByRole("button", { name: "Edit lifecycle" })).toBeInTheDocument();
    expect(within(panel()).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("edits precision, time and cancellation through labelled controls", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderPanel({
      onSave,
      lifecycle: savedLifecycle({ renewsOn: null, cancellationStatus: null, revision: 0 }),
    });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    await user.click(within(panel()).getByRole("combobox", { name: "Renews on precision" }));
    await user.click(await screen.findByRole("option", { name: "Date and time" }));

    expect(within(panel()).getByLabelText("Renews on timezone")).toHaveValue(browserTimeZone());
    fireEvent.change(within(panel()).getByLabelText("Renews on date"), { target: { value: "2026-11-03" } });
    fireEvent.change(within(panel()).getByLabelText("Renews on time"), { target: { value: "09:30" } });
    fireEvent.change(within(panel()).getByLabelText("Renews on timezone"), {
      target: { value: "America/New_York" },
    });

    await user.click(within(panel()).getByRole("combobox", { name: "Cancellation status" }));
    await user.click(await screen.findByRole("option", { name: "Cancelled" }));

    await user.click(within(panel()).getByRole("combobox", { name: "Ends on precision" }));
    await user.click(await screen.findByRole("option", { name: "Not set" }));

    await user.click(within(panel()).getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith("acc_primary", {
      endsOn: null,
      renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "America/New_York" },
      cancellationStatus: "cancelled",
      expectedRevision: 0,
    });
  });

  it("blocks saving and explains why while a value is invalid", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderPanel({ onSave });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    fireEvent.change(within(panel()).getByLabelText("Renews on timezone"), { target: { value: "" } });

    expect(within(panel()).getByText("Enter a timezone.")).toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(within(panel()).getByLabelText("Renews on timezone"), { target: { value: "+15:00" } });
    expect(
      within(panel()).getByText("Enter an IANA timezone such as America/New_York or an offset from -12:00 to +14:00."),
    ).toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("uses a seconds-capable time input only for values entered with seconds", async () => {
    const user = userEvent.setup();
    renderPanel({
      lifecycle: savedLifecycle({
        endsOn: { precision: "datetime", date: "2026-11-03", time: "09:30:15", timezone: "+02:00" },
      }),
    });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));

    expect(within(panel()).getByLabelText("Ends on time")).toHaveAttribute("step", "1");
    expect(within(panel()).getByLabelText("Ends on time")).toHaveValue("09:30:15");
    expect(within(panel()).getByLabelText("Renews on time")).toHaveAttribute("step", "60");
  });

  it("discards the draft on Cancel without sending anything", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderPanel({ onSave });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    fireEvent.change(within(panel()).getByLabelText("Ends on date"), { target: { value: "2026-12-31" } });
    await user.click(within(panel()).getByRole("button", { name: "Cancel" }));

    expect(onSave).not.toHaveBeenCalled();
    expect(within(row("Ends on")).getByText("2026-10-12")).toBeInTheDocument();
    expect(within(panel()).queryByText("2026-12-31")).not.toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Edit lifecycle" })).toBeInTheDocument();

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    expect(within(panel()).getByLabelText("Ends on date")).toHaveValue("2026-10-12");
  });

  it("keeps the draft and shows the error when a save is rejected", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(
      new ApiError({ status: 422, code: "validation_error", message: "Invalid request payload" }),
    );
    renderPanel({ onSave });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    fireEvent.change(within(panel()).getByLabelText("Ends on date"), { target: { value: "2026-10-20" } });
    await user.click(within(panel()).getByRole("button", { name: "Save" }));

    expect(await within(panel()).findByText("Invalid request payload")).toBeInTheDocument();
    expect(within(panel()).getByRole("button", { name: "Save" })).toBeEnabled();
    expect(within(panel()).getByLabelText("Ends on date")).toHaveValue("2026-10-20");
  });

  it("closes the draft and explains a conflicting save", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockRejectedValue(
      new ApiError({ status: 409, code: "account_lifecycle_conflict", message: "Lifecycle revision mismatch" }),
    );
    renderPanel({ onSave });

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));
    fireEvent.change(within(panel()).getByLabelText("Ends on date"), { target: { value: "2026-10-20" } });
    await user.click(within(panel()).getByRole("button", { name: "Save" }));

    expect(
      await within(panel()).findByText("Lifecycle details changed elsewhere. The latest values were reloaded; edit again."),
    ).toBeInTheDocument();
    expect(within(panel()).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(row("Ends on")).getByText("2026-10-12")).toBeInTheDocument();
  });

  it("disables editing while another account action is busy", () => {
    renderPanel({ busy: true });

    expect(within(panel()).getByRole("button", { name: "Edit lifecycle" })).toBeDisabled();
  });

  it("offers no reset, redemption, scheduling or ranking control", async () => {
    const user = userEvent.setup();
    renderPanel();

    const readButtons = within(panel()).queryAllByRole("button");
    expect(readButtons.filter((button) => FORBIDDEN_CONTROLS.test(button.textContent ?? ""))).toEqual([]);
    expect(readButtons.filter((button) => FORBIDDEN_CONTROLS.test(button.getAttribute("aria-label") ?? ""))).toEqual(
      [],
    );
    expect(panel().textContent).not.toMatch(FORBIDDEN_CONTROLS);

    await user.click(within(panel()).getByRole("button", { name: "Edit lifecycle" }));

    const controls = [
      ...within(panel()).queryAllByRole("button"),
      ...within(panel()).queryAllByRole("combobox"),
      ...within(panel()).queryAllByRole("textbox"),
    ];
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      expect(control.textContent ?? "").not.toMatch(FORBIDDEN_CONTROLS);
      expect(control.getAttribute("aria-label") ?? "").not.toMatch(FORBIDDEN_CONTROLS);
    }
    expect(panel().textContent).not.toMatch(FORBIDDEN_CONTROLS);
  });
});
