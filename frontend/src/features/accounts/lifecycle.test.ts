import { afterEach, describe, expect, it, vi } from "vitest";

import {
  browserTimeZone,
  formatLifecycleDate,
  isCivilDate,
  isLifecycleTimezone,
  isLocalTime,
  lifecycleDraftToPayload,
  lifecycleToDraft,
  validateLifecycleDateDraft,
  validateLifecycleDraft,
  type LifecycleDraft,
} from "@/features/accounts/lifecycle";
import type { AccountLifecycle } from "@/features/accounts/schemas";

afterEach(() => {
  vi.unstubAllEnvs();
});

// Both directions of a midnight shift: a UTC-midnight bug shows up west of
// UTC, a local-midnight bug shows up east of it.
const SHIFTING_ZONES = ["America/Los_Angeles", "Pacific/Kiritimati"] as const;

describe("formatLifecycleDate", () => {
  it.each(SHIFTING_ZONES)("renders a civil date verbatim in ISO 8601 without a day shift (TZ=%s)", (zone) => {
    vi.stubEnv("TZ", zone);

    expect(
      formatLifecycleDate({ precision: "date", date: "2026-10-12", time: null, timezone: null }, "iso8601", "en-US"),
    ).toBe("2026-10-12");
  });

  it.each(SHIFTING_ZONES)("renders a civil date in the locale without a day shift (TZ=%s)", (zone) => {
    vi.stubEnv("TZ", zone);

    expect(
      formatLifecycleDate({ precision: "date", date: "2026-10-12", time: null, timezone: null }, "default", "en-US"),
    ).toBe("Oct 12, 2026");
    expect(
      formatLifecycleDate({ precision: "date", date: "2026-01-01", time: null, timezone: null }, "default", "en-US"),
    ).toBe("Jan 1, 2026");
  });

  it("keeps a datetime's time and timezone exactly as entered", () => {
    vi.stubEnv("TZ", "Pacific/Kiritimati");

    expect(
      formatLifecycleDate(
        { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "America/New_York" },
        "iso8601",
        "en-US",
      ),
    ).toBe("2026-11-03 09:30 America/New_York");
    expect(
      formatLifecycleDate(
        { precision: "datetime", date: "2026-11-03", time: "09:30:15", timezone: "+02:00" },
        "iso8601",
        "en-US",
      ),
    ).toBe("2026-11-03 09:30:15 +02:00");
    expect(
      formatLifecycleDate(
        { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "America/New_York" },
        "default",
        "en-US",
      ),
    ).toBe("Nov 3, 2026 09:30 America/New_York");
  });
});

describe("lifecycle validators", () => {
  it("accepts only real calendar dates written YYYY-MM-DD", () => {
    expect(isCivilDate("2026-10-12")).toBe(true);
    expect(isCivilDate("2024-02-29")).toBe(true);
    expect(isCivilDate("2026-02-29")).toBe(false);
    expect(isCivilDate("2026-02-30")).toBe(false);
    expect(isCivilDate("2026-13-01")).toBe(false);
    expect(isCivilDate("2026-00-10")).toBe(false);
    expect(isCivilDate("2026-04-31")).toBe(false);
    expect(isCivilDate("2026-4-01")).toBe(false);
    expect(isCivilDate("0000-01-01")).toBe(false);
    expect(isCivilDate("")).toBe(false);
  });

  it("accepts only 24-hour HH:MM or HH:MM:SS times", () => {
    expect(isLocalTime("00:00")).toBe(true);
    expect(isLocalTime("09:30")).toBe(true);
    expect(isLocalTime("23:59:59")).toBe(true);
    expect(isLocalTime("24:00")).toBe(false);
    expect(isLocalTime("9:30")).toBe(false);
    expect(isLocalTime("09:60")).toBe(false);
    expect(isLocalTime("09:30:60")).toBe(false);
    expect(isLocalTime("09:30:15.5")).toBe(false);
    expect(isLocalTime("")).toBe(false);
  });

  it("accepts IANA names and offsets between -12:00 and +14:00", () => {
    expect(isLifecycleTimezone("America/New_York")).toBe(true);
    expect(isLifecycleTimezone("UTC")).toBe(true);
    expect(isLifecycleTimezone("+02:00")).toBe(true);
    expect(isLifecycleTimezone("-12:00")).toBe(true);
    expect(isLifecycleTimezone("+14:00")).toBe(true);
    expect(isLifecycleTimezone("+14:30")).toBe(false);
    expect(isLifecycleTimezone("-12:30")).toBe(false);
    expect(isLifecycleTimezone("+15:00")).toBe(false);
    expect(isLifecycleTimezone("02:00")).toBe(false);
    expect(isLifecycleTimezone("Not/AZone")).toBe(false);
    expect(isLifecycleTimezone("")).toBe(false);
  });

  it("rejects zone names that differ from the IANA spelling only by letter case", () => {
    expect(isLifecycleTimezone("america/new_york")).toBe(false);
    expect(isLifecycleTimezone("utc")).toBe(false);
    expect(isLifecycleTimezone("AMERICA/NEW_YORK")).toBe(false);
    // An alias the engine canonicalizes to another name is left for the server to judge.
    expect(isLifecycleTimezone("Asia/Kolkata")).toBe(true);
    expect(isLifecycleTimezone("US/Eastern")).toBe(true);
    expect(isLifecycleTimezone("Etc/GMT+5")).toBe(true);
  });

  it("rejects lower-case spellings of aliases that resolve to another zone", () => {
    expect(isLifecycleTimezone("us/eastern")).toBe(false);
    expect(isLifecycleTimezone("asia/kolkata")).toBe(false);
    expect(isLifecycleTimezone("europe/kyiv")).toBe(false);
  });

  it("reports why a date draft cannot be saved", () => {
    expect(validateLifecycleDateDraft({ precision: "unset", date: "", time: "", timezone: "" })).toBeNull();
    expect(validateLifecycleDateDraft({ precision: "date", date: "", time: "", timezone: "" })).toBe("dateRequired");
    expect(validateLifecycleDateDraft({ precision: "date", date: "2026-02-30", time: "", timezone: "" })).toBe(
      "dateInvalid",
    );
    expect(validateLifecycleDateDraft({ precision: "date", date: "2026-10-12", time: "", timezone: "" })).toBeNull();
    expect(
      validateLifecycleDateDraft({ precision: "datetime", date: "2026-11-03", time: "", timezone: "UTC" }),
    ).toBe("timeRequired");
    expect(
      validateLifecycleDateDraft({ precision: "datetime", date: "2026-11-03", time: "24:00", timezone: "UTC" }),
    ).toBe("timeInvalid");
    expect(
      validateLifecycleDateDraft({ precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "  " }),
    ).toBe("timezoneRequired");
    expect(
      validateLifecycleDateDraft({ precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "+15:00" }),
    ).toBe("timezoneInvalid");
    expect(
      validateLifecycleDateDraft({
        precision: "datetime",
        date: "2026-11-03",
        time: "09:30",
        timezone: "America/New_York",
      }),
    ).toBeNull();
  });

  it("validates both dates of a draft", () => {
    const draft: LifecycleDraft = {
      endsOn: { precision: "date", date: "2026-02-30", time: "", timezone: "" },
      renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "" },
      cancellationStatus: "unset",
    };

    expect(validateLifecycleDraft(draft)).toEqual({ endsOn: "dateInvalid", renewsOn: "timezoneRequired" });
  });
});

const TOKEN = "c".repeat(64);

describe("lifecycle drafts", () => {
  const saved: AccountLifecycle = {
    accountId: "acc_primary",
    endsOn: { precision: "date", date: "2026-10-12", time: null, timezone: null },
    renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30:15", timezone: "America/New_York" },
    cancellationStatus: "cancelled",
    revision: 2,
    concurrencyToken: TOKEN,
    updatedAt: "2026-10-05T10:00:00Z",
  };

  it("starts an empty draft when nothing was saved", () => {
    expect(lifecycleToDraft(null)).toEqual({
      endsOn: { precision: "unset", date: "", time: "", timezone: "" },
      renewsOn: { precision: "unset", date: "", time: "", timezone: "" },
      cancellationStatus: "unset",
    });
  });

  it("round-trips saved values into the exact replacement payload", () => {
    const payload = lifecycleDraftToPayload(lifecycleToDraft(saved), 2, TOKEN);

    expect(payload).toStrictEqual({
      endsOn: { precision: "date", date: "2026-10-12" },
      renewsOn: { precision: "datetime", date: "2026-11-03", time: "09:30:15", timezone: "America/New_York" },
      cancellationStatus: "cancelled",
      expectedRevision: 2,
      expectedConcurrencyToken: TOKEN,
    });
  });

  it("sends explicit nulls and drops a hidden time when the precision is narrowed", () => {
    const draft = lifecycleToDraft(saved);
    const payload = lifecycleDraftToPayload(
      {
        endsOn: { ...draft.endsOn, precision: "unset" },
        renewsOn: { ...draft.renewsOn, precision: "date", timezone: " America/New_York " },
        cancellationStatus: "unset",
      },
      7,
      TOKEN,
    );

    expect(payload).toStrictEqual({
      endsOn: null,
      renewsOn: { precision: "date", date: "2026-11-03" },
      cancellationStatus: null,
      expectedRevision: 7,
      expectedConcurrencyToken: TOKEN,
    });
    expect(Object.keys(payload).sort()).toEqual([
      "cancellationStatus",
      "endsOn",
      "expectedConcurrencyToken",
      "expectedRevision",
      "renewsOn",
    ]);
  });

  it("trims the timezone of a datetime value", () => {
    const payload = lifecycleDraftToPayload(
      {
        endsOn: { precision: "datetime", date: "2026-11-03", time: "09:30", timezone: " +02:00 " },
        renewsOn: { precision: "unset", date: "", time: "", timezone: "" },
        cancellationStatus: "not_cancelled",
      },
      0,
      TOKEN,
    );

    expect(payload.endsOn).toStrictEqual({ precision: "datetime", date: "2026-11-03", time: "09:30", timezone: "+02:00" });
    expect(payload.cancellationStatus).toBe("not_cancelled");
  });
});

describe("browserTimeZone", () => {
  it("reports the browser's resolved IANA timezone", () => {
    expect(browserTimeZone()).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});
