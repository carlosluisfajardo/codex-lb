import { isCivilDate, isLifecycleTimezone, isLocalTime } from "@/features/accounts/lifecycle";
import type { AccountLifecycle, AccountLifecycleDate } from "@/features/accounts/schemas";

// Saved lifecycle dates are operator notes. The dashboard only counts the calendar
// days left until each one: a civil date never becomes an instant, and a date that
// passed is never read as a provider expiry.

/** A set date this many calendar days away, or closer, is due soon. */
export const LIFECYCLE_DUE_SOON_DAYS = 7;

export type LifecycleAttention = "passed" | "today" | "dueSoon";

export type LifecycleDayCount =
  | { known: true; days: number; attention: LifecycleAttention | null }
  | { known: false };

export type LifecycleDateView = {
  value: AccountLifecycleDate;
  count: LifecycleDayCount;
};

/** What the dashboard can say about one account's saved dates. `null` dates are unset. */
export type AccountLifecycleEntry =
  | { status: "loading" }
  | { status: "unavailable" }
  | { status: "ready"; endsOn: LifecycleDateView | null; renewsOn: LifecycleDateView | null };

export type LifecycleAttentionSummary = {
  passed: number;
  today: number;
  dueSoon: number;
  unknown: number;
  pending: number;
};

type CivilDate = { year: number; month: number; day: number };

const UTC_OFFSET_PATTERN = /^([+-])(\d{2}):(\d{2})$/;

/** Days since 1970-01-01 in the proleptic Gregorian calendar, by integer arithmetic only. */
export function civilDayNumber({ year, month, day }: CivilDate): number {
  const marchYear = month <= 2 ? year - 1 : year;
  const era = Math.floor(marchYear / 400);
  const yearOfEra = marchYear - era * 400;
  const dayOfYear = Math.floor((153 * ((month + 9) % 12) + 2) / 5) + day - 1;
  const dayOfEra = yearOfEra * 365 + Math.floor(yearOfEra / 4) - Math.floor(yearOfEra / 100) + dayOfYear;
  return era * 146_097 + dayOfEra - 719_468;
}

function parseCivilDate(value: string): CivilDate | null {
  if (!isCivilDate(value)) {
    return null;
  }
  const [year, month, day] = value.split("-").map(Number);
  return { year, month, day };
}

/** The calendar date at `now` in a recorded zone: a fixed UTC offset or an IANA name the browser knows. */
function todayInTimezone(now: Date, timezone: string): CivilDate | null {
  if (!isLifecycleTimezone(timezone)) {
    return null;
  }
  const offset = UTC_OFFSET_PATTERN.exec(timezone);
  if (offset) {
    const minutes = (Number(offset[2]) * 60 + Number(offset[3])) * (offset[1] === "-" ? -1 : 1);
    const shifted = new Date(now.getTime() + minutes * 60_000);
    return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1, day: shifted.getUTCDate() };
  }
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    calendar: "gregory",
    numberingSystem: "latn",
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((entry) => entry.type === type)?.value);
  const today = { year: part("year"), month: part("month"), day: part("day") };
  return Number.isInteger(today.year) && Number.isInteger(today.month) && Number.isInteger(today.day) ? today : null;
}

/**
 * Calendar days from today until a saved date. A civil date is compared with the viewer's
 * local date; a local date and time with the date in its recorded zone. The time of day
 * never changes the count, and a value the browser cannot evaluate has no count.
 */
export function lifecycleDayCount(value: AccountLifecycleDate, now: Date): LifecycleDayCount {
  const target = parseCivilDate(value.date);
  const today =
    value.precision === "date"
      ? { year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate() }
      : isLocalTime(value.time)
        ? todayInTimezone(now, value.timezone)
        : null;
  if (!target || !today) {
    return { known: false };
  }
  const days = civilDayNumber(target) - civilDayNumber(today);
  const attention: LifecycleAttention | null =
    days < 0 ? "passed" : days === 0 ? "today" : days <= LIFECYCLE_DUE_SOON_DAYS ? "dueSoon" : null;
  return { known: true, days, attention };
}

function dateView(value: AccountLifecycleDate | null, now: Date): LifecycleDateView | null {
  return value ? { value, count: lifecycleDayCount(value, now) } : null;
}

/**
 * The dashboard view of one read. Notes returned for another id are never shown, and a read
 * whose latest attempt failed is unavailable even when older notes are still cached.
 */
export function accountLifecycleEntry(
  accountId: string,
  read: { data: AccountLifecycle | undefined; failed: boolean },
  now: Date,
): AccountLifecycleEntry {
  if (read.failed) {
    return { status: "unavailable" };
  }
  if (read.data) {
    return read.data.accountId === accountId
      ? { status: "ready", endsOn: dateView(read.data.endsOn, now), renewsOn: dateView(read.data.renewsOn, now) }
      : { status: "unavailable" };
  }
  return { status: "loading" };
}

/** Changes exactly when one of the displayed day counts changes. */
export function lifecycleDayKey(lifecycles: readonly AccountLifecycle[], now: Date): string {
  const countKey = (value: AccountLifecycleDate | null) => {
    if (!value) {
      return "";
    }
    const count = lifecycleDayCount(value, now);
    return count.known ? String(count.days) : "?";
  };
  return lifecycles.map((lifecycle) => `${countKey(lifecycle.endsOn)},${countKey(lifecycle.renewsOn)}`).join("|");
}

const ATTENTION_ORDER: readonly LifecycleAttention[] = ["passed", "today", "dueSoon"];

/** Counts each account once, by its most urgent date. */
export function summarizeLifecycleAttention(entries: Iterable<AccountLifecycleEntry>): LifecycleAttentionSummary {
  const summary: LifecycleAttentionSummary = { passed: 0, today: 0, dueSoon: 0, unknown: 0, pending: 0 };
  for (const entry of entries) {
    if (entry.status === "loading") {
      summary.pending += 1;
      continue;
    }
    if (entry.status === "unavailable") {
      summary.unknown += 1;
      continue;
    }
    const counts = [entry.endsOn, entry.renewsOn].flatMap((view) => (view ? [view.count] : []));
    const attention = ATTENTION_ORDER.find((level) =>
      counts.some((count) => count.known && count.attention === level),
    );
    if (attention) {
      summary[attention] += 1;
    } else if (counts.some((count) => !count.known)) {
      summary.unknown += 1;
    }
  }
  return summary;
}
