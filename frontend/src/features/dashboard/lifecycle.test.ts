import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountLifecycleDate } from "@/features/accounts/schemas";
import {
  LIFECYCLE_DUE_SOON_DAYS,
  accountLifecycleEntry,
  civilDayNumber,
  lifecycleDayCount,
  lifecycleDayKey,
  summarizeLifecycleAttention,
  type AccountLifecycleEntry,
} from "@/features/dashboard/lifecycle";
import { createAccountLifecycle } from "@/test/mocks/factories";

afterEach(() => {
  vi.unstubAllEnvs();
});

function civil(date: string): AccountLifecycleDate {
  return { precision: "date", date, time: null, timezone: null };
}

function timed(date: string, time: string, timezone: string): AccountLifecycleDate {
  return { precision: "datetime", date, time, timezone };
}

function days(value: AccountLifecycleDate, now: Date): number | null {
  const count = lifecycleDayCount(value, now);
  return count.known ? count.days : null;
}

describe("civilDayNumber", () => {
  it("counts whole days from 1970-01-01 across leap and century rules", () => {
    expect(civilDayNumber({ year: 1970, month: 1, day: 1 })).toBe(0);
    expect(civilDayNumber({ year: 2000, month: 3, day: 1 })).toBe(11_017);
    expect(civilDayNumber({ year: 1, month: 1, day: 1 })).toBe(-719_162);
    expect(civilDayNumber({ year: 2028, month: 3, day: 1 }) - civilDayNumber({ year: 2028, month: 2, day: 28 })).toBe(2);
    expect(civilDayNumber({ year: 2100, month: 3, day: 1 }) - civilDayNumber({ year: 2100, month: 2, day: 28 })).toBe(1);
  });

  it("matches the UTC calendar for every day of a decade", () => {
    for (let time = Date.UTC(2024, 0, 1); time <= Date.UTC(2034, 11, 31); time += 86_400_000) {
      const day = new Date(time);
      expect(
        civilDayNumber({ year: day.getUTCFullYear(), month: day.getUTCMonth() + 1, day: day.getUTCDate() }),
      ).toBe(time / 86_400_000);
    }
  });
});

describe("lifecycleDayCount for a civil date", () => {
  it("is not shortened by a spring-forward daylight saving change in the viewer's zone", () => {
    vi.stubEnv("TZ", "America/New_York");

    expect(days(civil("2026-03-09"), new Date(2026, 2, 7, 23, 30))).toBe(2);
    expect(days(civil("2026-03-09"), new Date(2026, 2, 7, 0, 30))).toBe(2);
    expect(days(civil("2026-03-08"), new Date(2026, 2, 7, 23, 59))).toBe(1);
  });

  it("is not lengthened by a fall-back daylight saving change in the viewer's zone", () => {
    vi.stubEnv("TZ", "America/New_York");

    expect(days(civil("2026-11-02"), new Date(2026, 9, 31, 0, 15))).toBe(2);
    expect(days(civil("2026-11-02"), new Date(2026, 9, 31, 23, 45))).toBe(2);
  });

  it.each(["America/Los_Angeles", "Pacific/Kiritimati"])(
    "compares with the viewer's local date without shifting the saved date (TZ=%s)",
    (zone) => {
      vi.stubEnv("TZ", zone);
      const lateEvening = new Date(2026, 9, 10, 23, 59);
      const earlyMorning = new Date(2026, 9, 10, 0, 1);

      expect(days(civil("2026-10-10"), lateEvening)).toBe(0);
      expect(days(civil("2026-10-10"), earlyMorning)).toBe(0);
      expect(days(civil("2026-10-11"), lateEvening)).toBe(1);
      expect(days(civil("2026-10-09"), earlyMorning)).toBe(-1);
    },
  );

  it("counts across month ends, year ends and leap days", () => {
    vi.stubEnv("TZ", "UTC");

    expect(days(civil("2027-01-01"), new Date(2026, 11, 31, 12))).toBe(1);
    expect(days(civil("2026-02-01"), new Date(2026, 0, 31, 12))).toBe(1);
    expect(days(civil("2028-03-01"), new Date(2028, 1, 28, 12))).toBe(2);
    expect(days(civil("2028-02-29"), new Date(2028, 1, 28, 12))).toBe(1);
    expect(days(civil("2027-03-01"), new Date(2027, 1, 28, 12))).toBe(1);
    expect(days(civil("2026-10-31"), new Date(2026, 9, 10, 12))).toBe(21);
  });
});

describe("lifecycleDayCount for a local date and time", () => {
  it("counts in the recorded IANA zone, not the viewer's", () => {
    vi.stubEnv("TZ", "UTC");
    const now = new Date("2026-10-10T23:30:00Z");

    expect(days(timed("2026-10-11", "09:30", "Asia/Tokyo"), now)).toBe(0);
    expect(days(timed("2026-10-11", "09:30", "America/New_York"), now)).toBe(1);
    expect(days(timed("2026-10-11", "09:30", "UTC"), now)).toBe(1);
  });

  it("counts in a recorded fixed offset, including the extreme offsets", () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    const now = new Date("2026-10-10T23:30:00Z");

    expect(days(timed("2026-10-11", "09:30", "-05:00"), now)).toBe(1);
    expect(days(timed("2026-10-11", "09:30", "+05:45"), now)).toBe(0);
    expect(days(timed("2026-10-11", "00:00", "+14:00"), new Date("2026-10-10T10:00:00Z"))).toBe(0);
    expect(days(timed("2026-10-11", "00:00", "-12:00"), new Date("2026-10-10T10:00:00Z"))).toBe(2);
  });

  it("ignores the time of day", () => {
    vi.stubEnv("TZ", "UTC");
    const now = new Date("2026-10-10T12:00:00Z");

    expect(days(timed("2026-10-11", "00:00", "UTC"), now)).toBe(1);
    expect(days(timed("2026-10-11", "23:59:59", "UTC"), now)).toBe(1);
    expect(days(timed("2026-10-10", "00:00", "UTC"), now)).toBe(0);
  });

  it("is not changed by a daylight saving change in the recorded zone", () => {
    vi.stubEnv("TZ", "UTC");

    // 2026-03-08 01:30 EST, then 03:30 EDT after the clocks went forward.
    expect(days(timed("2026-03-09", "09:00", "America/New_York"), new Date("2026-03-08T06:30:00Z"))).toBe(1);
    expect(days(timed("2026-03-09", "09:00", "America/New_York"), new Date("2026-03-08T07:30:00Z"))).toBe(1);
    // 2026-03-07 23:30 EST is still the 7th in New York although it is the 8th in UTC.
    expect(days(timed("2026-03-09", "09:00", "America/New_York"), new Date("2026-03-08T04:30:00Z"))).toBe(2);
  });
});

describe("lifecycle attention", () => {
  it("marks one to seven days due soon, zero today and negative passed", () => {
    vi.stubEnv("TZ", "UTC");
    const now = new Date(2026, 9, 10, 12);
    const attention = (date: string) => {
      const count = lifecycleDayCount(civil(date), now);
      return count.known ? count.attention : "unknown";
    };

    expect(LIFECYCLE_DUE_SOON_DAYS).toBe(7);
    expect(attention("2026-10-09")).toBe("passed");
    expect(attention("2026-10-10")).toBe("today");
    expect(attention("2026-10-11")).toBe("dueSoon");
    expect(attention("2026-10-17")).toBe("dueSoon");
    expect(attention("2026-10-18")).toBeNull();
  });

  it("gives no count for a value the browser cannot evaluate", () => {
    const now = new Date("2026-10-10T12:00:00Z");

    expect(lifecycleDayCount(civil("2026-02-30"), now)).toEqual({ known: false });
    expect(lifecycleDayCount(civil("10/31/2026"), now)).toEqual({ known: false });
    expect(lifecycleDayCount(timed("2026-10-11", "09:30", "Mars/Olympus_Mons"), now)).toEqual({ known: false });
    expect(lifecycleDayCount(timed("2026-10-11", "09:30", "america/new_york"), now)).toEqual({ known: false });
    expect(lifecycleDayCount(timed("2026-10-11", "09:30", "+15:00"), now)).toEqual({ known: false });
    expect(lifecycleDayCount(timed("2026-10-11", "25:00", "UTC"), now)).toEqual({ known: false });
  });
});

describe("accountLifecycleEntry", () => {
  const now = new Date("2026-10-10T12:00:00Z");

  it("shows notes read for the same account", () => {
    const entry = accountLifecycleEntry(
      "acc_a",
      { data: createAccountLifecycle({ accountId: "acc_a", renewsOn: civil("2026-10-31") }), failed: false },
      now,
    );

    expect(entry).toMatchObject({ status: "ready", endsOn: null, renewsOn: { value: civil("2026-10-31") } });
  });

  it("never shows notes returned for another account", () => {
    const entry = accountLifecycleEntry(
      "acc_a",
      { data: createAccountLifecycle({ accountId: "acc_b", endsOn: civil("2026-10-12") }), failed: false },
      now,
    );

    expect(entry).toEqual({ status: "unavailable" });
  });

  it("keeps a failed read apart from loading and from unset dates", () => {
    expect(accountLifecycleEntry("acc_a", { data: undefined, failed: true }, now)).toEqual({ status: "unavailable" });
    expect(
      accountLifecycleEntry(
        "acc_a",
        { data: createAccountLifecycle({ accountId: "acc_a", endsOn: civil("2026-10-12") }), failed: true },
        now,
      ),
    ).toEqual({ status: "unavailable" });
    expect(accountLifecycleEntry("acc_a", { data: undefined, failed: false }, now)).toEqual({ status: "loading" });
    expect(
      accountLifecycleEntry("acc_a", { data: createAccountLifecycle({ accountId: "acc_a" }), failed: false }, now),
    ).toEqual({ status: "ready", endsOn: null, renewsOn: null });
  });
});

describe("summarizeLifecycleAttention", () => {
  const now = new Date("2026-10-10T12:00:00Z");
  const ready = (endsOn: AccountLifecycleDate | null, renewsOn: AccountLifecycleDate | null): AccountLifecycleEntry =>
    accountLifecycleEntry("acc", { data: createAccountLifecycle({ accountId: "acc", endsOn, renewsOn }), failed: false }, now);

  beforeEach(() => {
    vi.stubEnv("TZ", "UTC");
  });

  it("counts each account once by its most urgent date", () => {
    expect(
      summarizeLifecycleAttention([
        ready(civil("2026-10-01"), civil("2026-10-13")),
        ready(null, civil("2026-10-10")),
        ready(civil("2026-10-12"), civil("2026-10-15")),
        ready(null, civil("2026-11-30")),
        ready(null, null),
      ]),
    ).toEqual({ passed: 1, today: 1, dueSoon: 1, unknown: 0, pending: 0 });
  });

  it("counts unreadable notes and dates without a day count as unknown unless a date needs attention", () => {
    expect(
      summarizeLifecycleAttention([
        { status: "unavailable" },
        ready(timed("2026-10-12", "09:30", "Mars/Olympus_Mons"), civil("2026-11-30")),
        ready(timed("2026-10-12", "09:30", "Mars/Olympus_Mons"), civil("2026-10-09")),
        { status: "loading" },
      ]),
    ).toEqual({ passed: 1, today: 0, dueSoon: 0, unknown: 2, pending: 1 });
  });
});

describe("lifecycleDayKey", () => {
  it("changes exactly when a displayed day count changes", () => {
    vi.stubEnv("TZ", "UTC");
    const lifecycles = [
      createAccountLifecycle({ accountId: "acc_a", renewsOn: civil("2026-10-31") }),
      createAccountLifecycle({ accountId: "acc_b", endsOn: timed("2026-10-12", "09:30", "Asia/Tokyo") }),
    ];

    const evening = lifecycleDayKey(lifecycles, new Date("2026-10-10T12:00:00Z"));
    expect(lifecycleDayKey(lifecycles, new Date("2026-10-10T14:59:00Z"))).toBe(evening);
    // Midnight in Tokyo comes before midnight in the viewer's zone.
    expect(lifecycleDayKey(lifecycles, new Date("2026-10-10T15:00:00Z"))).not.toBe(evening);
    expect(lifecycleDayKey([createAccountLifecycle({ accountId: "acc_c" })], new Date())).toBe(",");
  });
});
