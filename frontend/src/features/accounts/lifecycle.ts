import type {
  AccountCancellationStatus,
  AccountLifecycle,
  AccountLifecycleDate,
  AccountLifecycleDateInput,
  AccountLifecycleUpdateRequest,
} from "@/features/accounts/schemas";
import type { DateDisplayFormat } from "@/hooks/use-date-format";
import { ApiError } from "@/lib/api-client";

// Lifecycle dates are civil values typed in by an operator. Nothing here turns
// them into an instant: a civil date has no time of day and a local time keeps
// the timezone it was declared in.

const CIVIL_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const LOCAL_TIME_PATTERN = /^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/;
const UTC_OFFSET_PATTERN = /^([+-])(\d{2}):(\d{2})$/;
// Every IANA zone name component starts with an upper-case letter (America/New_York, Etc/GMT+5).
const TIMEZONE_NAME_PATTERN = /^[A-Z][A-Za-z0-9_+-]*(?:\/[A-Z][A-Za-z0-9_+-]*)*$/;
const MAX_EAST_OFFSET_MINUTES = 14 * 60;
const MAX_WEST_OFFSET_MINUTES = 12 * 60;

function parseCivilDate(value: string): { year: number; month: number; day: number } | null {
  const match = CIVIL_DATE_PATTERN.exec(value);
  if (!match) {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1) {
    return null;
  }
  const leapYear = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return day <= daysInMonth ? { year, month, day } : null;
}

export function isCivilDate(value: string): boolean {
  return parseCivilDate(value) !== null;
}

export function isLocalTime(value: string): boolean {
  return LOCAL_TIME_PATTERN.test(value);
}

function isUtcOffset(value: string): boolean {
  const match = UTC_OFFSET_PATTERN.exec(value);
  if (!match) {
    return false;
  }
  const hours = Number(match[2]);
  const minutes = Number(match[3]);
  if (minutes > 59) {
    return false;
  }
  const total = hours * 60 + minutes;
  return match[1] === "+" ? total <= MAX_EAST_OFFSET_MINUTES : total <= MAX_WEST_OFFSET_MINUTES;
}

function isKnownTimeZoneName(value: string): boolean {
  if (!TIMEZONE_NAME_PATTERN.test(value)) {
    return false;
  }
  let resolved: string;
  try {
    resolved = new Intl.DateTimeFormat("en-US", { timeZone: value }).resolvedOptions().timeZone;
  } catch {
    return false;
  }
  // Intl matches zone names case-insensitively, the server does not: a name that
  // differs from the canonical one only by letter case would be rejected there.
  return resolved === value || resolved.toLowerCase() !== value.toLowerCase();
}

/** An IANA zone name the browser knows, or a fixed offset from -12:00 to +14:00. The server decides finally. */
export function isLifecycleTimezone(value: string): boolean {
  return isUtcOffset(value) || isKnownTimeZoneName(value);
}

/** The save was based on a stale revision and nothing was written. */
export function isAccountLifecycleConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === "account_lifecycle_conflict";
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function formatCivilDate(value: string, displayFormat: DateDisplayFormat, locale: string): string {
  const parsed = parseCivilDate(value);
  if (displayFormat === "iso8601" || !parsed) {
    return value;
  }
  // Format the calendar day itself: pin both the construction and the
  // rendering to UTC so no browser offset can move it to a neighbouring day.
  const day = new Date(0);
  day.setUTCFullYear(parsed.year, parsed.month - 1, parsed.day);
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" }).format(day);
}

/** Read-only text for a lifecycle date; the time and timezone are always shown as entered. */
export function formatLifecycleDate(
  value: AccountLifecycleDate,
  displayFormat: DateDisplayFormat,
  locale: string,
): string {
  const date = formatCivilDate(value.date, displayFormat, locale);
  if (value.precision === "date") {
    return date;
  }
  return `${date} ${value.time} ${value.timezone}`;
}

export type LifecyclePrecisionChoice = "unset" | "date" | "datetime";

export type LifecycleDateDraft = {
  precision: LifecyclePrecisionChoice;
  date: string;
  time: string;
  timezone: string;
};

export type LifecycleCancellationChoice = "unset" | AccountCancellationStatus;

export type LifecycleDraft = {
  endsOn: LifecycleDateDraft;
  renewsOn: LifecycleDateDraft;
  cancellationStatus: LifecycleCancellationChoice;
};

export type LifecycleDateDraftError =
  | "dateRequired"
  | "dateInvalid"
  | "timeRequired"
  | "timeInvalid"
  | "timezoneRequired"
  | "timezoneInvalid";

export function lifecycleDateToDraft(value: AccountLifecycleDate | null | undefined): LifecycleDateDraft {
  if (!value) {
    return { precision: "unset", date: "", time: "", timezone: "" };
  }
  return {
    precision: value.precision,
    date: value.date,
    time: value.time ?? "",
    timezone: value.timezone ?? "",
  };
}

export function lifecycleToDraft(lifecycle: AccountLifecycle | null | undefined): LifecycleDraft {
  return {
    endsOn: lifecycleDateToDraft(lifecycle?.endsOn),
    renewsOn: lifecycleDateToDraft(lifecycle?.renewsOn),
    cancellationStatus: lifecycle?.cancellationStatus ?? "unset",
  };
}

export function validateLifecycleDateDraft(draft: LifecycleDateDraft): LifecycleDateDraftError | null {
  if (draft.precision === "unset") {
    return null;
  }
  if (!draft.date) {
    return "dateRequired";
  }
  if (!isCivilDate(draft.date)) {
    return "dateInvalid";
  }
  if (draft.precision === "date") {
    return null;
  }
  if (!draft.time) {
    return "timeRequired";
  }
  if (!isLocalTime(draft.time)) {
    return "timeInvalid";
  }
  const timezone = draft.timezone.trim();
  if (!timezone) {
    return "timezoneRequired";
  }
  return isLifecycleTimezone(timezone) ? null : "timezoneInvalid";
}

export function validateLifecycleDraft(draft: LifecycleDraft): {
  endsOn: LifecycleDateDraftError | null;
  renewsOn: LifecycleDateDraftError | null;
} {
  return {
    endsOn: validateLifecycleDateDraft(draft.endsOn),
    renewsOn: validateLifecycleDateDraft(draft.renewsOn),
  };
}

/** The request value for one date: only the keys its precision allows. */
export function lifecycleDateDraftToInput(draft: LifecycleDateDraft): AccountLifecycleDateInput | null {
  if (draft.precision === "unset") {
    return null;
  }
  if (draft.precision === "date") {
    return { precision: "date", date: draft.date };
  }
  return { precision: "datetime", date: draft.date, time: draft.time, timezone: draft.timezone.trim() };
}

/** A full replacement: every field is sent, unset ones as explicit nulls. */
export function lifecycleDraftToPayload(
  draft: LifecycleDraft,
  expectedRevision: number,
): AccountLifecycleUpdateRequest {
  return {
    endsOn: lifecycleDateDraftToInput(draft.endsOn),
    renewsOn: lifecycleDateDraftToInput(draft.renewsOn),
    cancellationStatus: draft.cancellationStatus === "unset" ? null : draft.cancellationStatus,
    expectedRevision,
  };
}
