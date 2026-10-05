import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountLifecyclePanel } from "@/features/accounts/components/account-lifecycle-panel";
import { createAccountLifecycle, createAccountSummary } from "@/test/mocks/factories";

function panel(readOnly: boolean, onSave: (accountId: string, payload: unknown) => Promise<unknown>) {
  return (
    <AccountLifecyclePanel
      account={createAccountSummary({ accountId: "acc_primary" })}
      lifecycle={createAccountLifecycle({
        accountId: "acc_primary",
        endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
        revision: 1,
        updatedAt: "2026-10-05T10:00:00Z",
      })}
      loading={false}
      error={null}
      busy={false}
      readOnly={readOnly}
      onSave={onSave}
    />
  );
}

describe("AccountLifecyclePanel when write access is removed during an edit", () => {
  it("closes the open draft, offers no save and never calls onSave", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(panel(false, onSave));
    await user.click(screen.getByRole("button", { name: "Edit lifecycle" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();

    rerender(panel(true, onSave));

    const section = screen.getByRole("region", { name: "Subscription lifecycle" });
    expect(within(section).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("combobox", { name: "Ends on precision" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Edit lifecycle" })).not.toBeInTheDocument();
    expect(within(section).getByText("Date only")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not bring the discarded draft back when write access returns", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(panel(false, onSave));
    await user.click(screen.getByRole("button", { name: "Edit lifecycle" }));

    rerender(panel(true, onSave));
    rerender(panel(false, onSave));

    const section = screen.getByRole("region", { name: "Subscription lifecycle" });
    expect(within(section).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Edit lifecycle" })).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
});
