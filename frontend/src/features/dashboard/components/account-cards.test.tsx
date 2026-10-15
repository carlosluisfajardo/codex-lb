import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AccountCards } from "@/features/dashboard/components/account-cards";
import type { AccountLifecycleEntry } from "@/features/dashboard/lifecycle";
import { createAccountSummary } from "@/test/mocks/factories";

describe("AccountCards", () => {
  it("fits two measured rows and remeasures when content or columns change", () => {
    let resize: ResizeObserverCallback | undefined;
    const observe = vi.fn();
    const disconnect = vi.fn();
    let columns = 3;
    let heights = [411, 356, 300, 300];
    const childIndex = (element: HTMLElement) =>
      Array.from(element.parentElement?.children ?? []).indexOf(element);
    const offsetTop = vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(function (this: HTMLElement) {
      const row = Math.floor(childIndex(this) / columns);
      return heights.slice(0, row).reduce((sum, height) => sum + height + 16, 0);
    });
    const offsetHeight = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
      return heights[Math.floor(childIndex(this) / columns)] ?? 0;
    });
    const observer = vi.spyOn(globalThis, "ResizeObserver").mockImplementation(class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback;
      }
      observe = observe;
      unobserve = vi.fn();
      disconnect = disconnect;
    });

    try {
      const { unmount } = render(
        <AccountCards
          accounts={Array.from({ length: 7 }, (_, index) =>
            createAccountSummary({
              accountId: `acc-${index + 1}`,
              email: `account-${index + 1}@example.com`,
              displayName: `Account ${index + 1}`,
            }),
          )}
          onAction={vi.fn()}
        />,
      );
      const grid = screen.getByTestId("dashboard-account-cards");
      expect(grid.style.maxHeight).toBe("783px");
      expect(observe).toHaveBeenCalledWith(grid);
      for (const child of grid.children) expect(observe).toHaveBeenCalledWith(child);

      // A late lifecycle response grows one row without changing the account count.
      heights = [450, 380, 300, 300];
      act(() => resize?.([], {} as ResizeObserver));
      expect(grid.style.maxHeight).toBe("846px");

      // One-column cards flow in the page rather than creating a tall internal scroller.
      columns = 1;
      heights = [520, 460, 400, 400, 400, 400, 400];
      act(() => resize?.([], {} as ResizeObserver));
      expect(grid.style.maxHeight).toBe("none");

      // Widening again must restore the original measured two-row protection.
      columns = 3;
      heights = [411, 356, 300];
      act(() => resize?.([], {} as ResizeObserver));
      expect(grid.style.maxHeight).toBe("783px");

      unmount();
      expect(disconnect).toHaveBeenCalledOnce();
    } finally {
      observer.mockRestore();
      offsetTop.mockRestore();
      offsetHeight.mockRestore();
    }
  });

  it("names a keyboard-focusable scroll region without suppressing native scrollbars", () => {
    render(
      <AccountCards
        accounts={[createAccountSummary(), createAccountSummary({ accountId: "acc-2", email: "two@example.com" })]}
        onAction={vi.fn()}
      />,
    );

    const grid = screen.getByRole("region", { name: "Accounts" });
    expect(grid).toHaveAttribute("tabindex", "0");
    expect(grid).toHaveClass("overflow-y-auto");
    expect(grid).not.toHaveClass("[scrollbar-width:none]");
    expect(grid).not.toHaveClass("[&::-webkit-scrollbar]:hidden");
  });

  it("gives each warm-up toggle a descriptive account-specific name", () => {
    render(
      <AccountCards
        accounts={[
          createAccountSummary({
            accountId: "acc-1",
            email: "one@example.com",
            displayName: "One Account",
            limitWarmupEnabled: false,
          }),
          createAccountSummary({
            accountId: "acc-2",
            email: "two@example.com",
            displayName: "Two Account",
            limitWarmupEnabled: true,
          }),
        ]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Enable limit warm-up for One Account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable limit warm-up for Two Account" })).toBeInTheDocument();
  });

  it("shows account ids only for backend-marked duplicate account slots", () => {
    render(
      <AccountCards
        accounts={[
          createAccountSummary({
            accountId: "d48f0bfc-8ea6-48a7-8d76-d0e5ef1816c5_6f12b5d5",
            email: "dup@example.com",
            displayName: "Same email, different workspace",
            isEmailDuplicate: false,
          }),
          createAccountSummary({
            accountId: "7f9de2ad-7621-4a6f-88bc-ec7f3d914701_91a95cee",
            email: "dup@example.com",
            displayName: "Same email, duplicate slot",
            isEmailDuplicate: true,
          }),
        ]}
        onAction={vi.fn()}
      />,
    );

    expect(screen.queryByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com .* ID d48f0bfc\.\.\.12b5d5/))).not.toBeInTheDocument();
    expect(screen.getByText((_content, el) => el?.tagName === "P" && !!el.textContent?.match(/dup@example\.com .* ID 7f9de2ad\.\.\.a95cee/))).toBeInTheDocument();
  });

  it("gives each card the subscription dates read for its own account id", () => {
    const lifecycle = new Map<string, AccountLifecycleEntry>([
      ["acc-1", { status: "unavailable" }],
      ["acc-2", { status: "ready", endsOn: null, renewsOn: null }],
    ]);
    render(
      <AccountCards
        accounts={[
          createAccountSummary({ accountId: "acc-1", email: "one@example.com", displayName: "One Account" }),
          createAccountSummary({ accountId: "acc-2", email: "two@example.com", displayName: "Two Account" }),
          createAccountSummary({ accountId: "acc-3", email: "three@example.com", displayName: "Three Account" }),
        ]}
        lifecycle={lifecycle}
      />,
    );

    const card = (name: string) =>
      within(
        Array.from(screen.getByTestId("dashboard-account-cards").children).find((element) =>
          within(element as HTMLElement).queryByText(name),
        ) as HTMLElement,
      );
    expect(card("One Account").getAllByText("Unavailable")).toHaveLength(2);
    expect(card("Two Account").getAllByText("Not set")).toHaveLength(2);
    expect(card("Three Account").getAllByText("Loading…")).toHaveLength(2);
  });

  it("shows no subscription dates without a lifecycle map", () => {
    render(<AccountCards accounts={[createAccountSummary()]} lifecycle={null} />);

    expect(screen.queryByLabelText("Subscription dates")).not.toBeInTheDocument();
  });

  it("links the empty-account state to the Accounts page", () => {
    render(
      <MemoryRouter>
        <AccountCards accounts={[]} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Add accounts" })).toHaveAttribute("href", "/accounts");
  });
});
