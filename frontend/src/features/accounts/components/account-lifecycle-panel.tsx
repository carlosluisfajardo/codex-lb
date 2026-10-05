import { Pencil } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  browserTimeZone,
  formatLifecycleDate,
  isAccountLifecycleConflict,
  lifecycleDraftToPayload,
  lifecycleToDraft,
  validateLifecycleDraft,
  type LifecycleCancellationChoice,
  type LifecycleDateDraft,
  type LifecycleDateDraftError,
  type LifecycleDraft,
  type LifecyclePrecisionChoice,
} from "@/features/accounts/lifecycle";
import type {
  AccountCancellationStatus,
  AccountLifecycle,
  AccountLifecycleDate,
  AccountLifecycleUpdateRequest,
  AccountRoutingPolicy,
  AccountSummary,
} from "@/features/accounts/schemas";
import { useDateDisplayFormatStore } from "@/hooks/use-date-format";

export type AccountLifecyclePanelProps = {
  account: AccountSummary;
  lifecycle: AccountLifecycle | null | undefined;
  loading: boolean;
  error: string | null;
  busy: boolean;
  readOnly: boolean;
  onSave: (accountId: string, payload: AccountLifecycleUpdateRequest) => Promise<unknown>;
};

const ROUTING_POLICY_LABEL_KEYS: Record<AccountRoutingPolicy, string> = {
  normal: "common.routingPolicies.normal",
  burn_first: "common.routingPolicies.burnFirst",
  preserve: "common.routingPolicies.preserve",
};

const CANCELLATION_LABEL_KEYS: Record<AccountCancellationStatus, string> = {
  not_cancelled: "accounts.lifecycle.cancellationStatus.not_cancelled",
  cancelled: "accounts.lifecycle.cancellationStatus.cancelled",
};

const VALIDATION_MESSAGE_KEYS: Record<LifecycleDateDraftError, string> = {
  dateRequired: "accounts.lifecycle.validation.dateRequired",
  dateInvalid: "accounts.lifecycle.validation.dateInvalid",
  timeRequired: "accounts.lifecycle.validation.timeRequired",
  timeInvalid: "accounts.lifecycle.validation.timeInvalid",
  timezoneRequired: "accounts.lifecycle.validation.timezoneRequired",
  timezoneInvalid: "accounts.lifecycle.validation.timezoneInvalid",
};

type EditSession = {
  draft: LifecycleDraft;
  /** The values, revision and account row the draft was based on, captured when editing started. */
  initial: LifecycleDraft;
  expectedRevision: number;
  expectedConcurrencyToken: string;
};

export function AccountLifecyclePanel({
  account,
  lifecycle,
  loading,
  error,
  busy,
  readOnly,
  onSave,
}: AccountLifecyclePanelProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const [session, setSession] = useState<EditSession | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflictNotice, setConflictNotice] = useState(false);
  const [sessionReadOnly, setSessionReadOnly] = useState(readOnly);

  // Losing write access discards an open draft at once, during render, so no save control or
  // submission survives the change, and regaining access does not bring the old draft back.
  if (readOnly !== sessionReadOnly) {
    setSessionReadOnly(readOnly);
    if (readOnly) {
      setSession(null);
      setSaveError(null);
    }
  }

  // Values read for another id are never shown or edited here.
  const loaded = lifecycle && lifecycle.accountId === account.accountId ? lifecycle : null;
  const routingPolicy = account.routingPolicy ?? "normal";
  const errors = session ? validateLifecycleDraft(session.draft) : null;
  const invalid = errors !== null && (errors.endsOn !== null || errors.renewsOn !== null);

  const startEditing = () => {
    if (!loaded) {
      return;
    }
    const draft = lifecycleToDraft(loaded);
    setSession({
      draft,
      initial: draft,
      expectedRevision: loaded.revision,
      expectedConcurrencyToken: loaded.concurrencyToken,
    });
    setSaveError(null);
    setConflictNotice(false);
  };

  const cancelEditing = () => {
    setSession(null);
    setSaveError(null);
  };

  const updateDraft = (update: (draft: LifecycleDraft) => LifecycleDraft) => {
    setSession((current) => (current ? { ...current, draft: update(current.draft) } : current));
  };

  const save = async () => {
    if (!session || readOnly || invalid || saving || busy) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onSave(
        account.accountId,
        lifecycleDraftToPayload(session.draft, session.expectedRevision, session.expectedConcurrencyToken),
      );
      setSession(null);
    } catch (caught) {
      if (isAccountLifecycleConflict(caught)) {
        // Nothing was written; the stored values are being reloaded, so the draft is stale.
        setSession(null);
        setConflictNotice(true);
      } else {
        setSaveError(
          (caught instanceof Error && caught.message) || t("accounts.lifecycle.toasts.saveFailed"),
        );
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby={titleId} className="min-w-0 space-y-3 rounded-lg border bg-muted/30 p-4">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <h3 id={titleId} className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("accounts.lifecycle.title")}
        </h3>
        {!readOnly && !session ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={t("accounts.lifecycle.edit")}
            title={t("accounts.lifecycle.edit")}
            disabled={busy || !loaded}
            onClick={startEditing}
          >
            <Pencil className="size-3.5" aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      {conflictNotice ? (
        <p role="status" className="text-xs text-muted-foreground">
          {t("accounts.lifecycle.conflictNotice")}
        </p>
      ) : null}
      {!loaded && !error && loading ? (
        <p className="text-xs text-muted-foreground">{t("accounts.lifecycle.loading")}</p>
      ) : null}
      {error ? (
        <p className="text-xs text-muted-foreground">{t("accounts.lifecycle.loadFailed", { message: error })}</p>
      ) : null}

      {session && errors && !readOnly ? (
        <form
          className="space-y-3"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <LifecycleDateFields
            label={t("accounts.lifecycle.endsOn")}
            draft={session.draft.endsOn}
            withSeconds={session.initial.endsOn.time.length > 5}
            error={errors.endsOn}
            disabled={saving}
            onChange={(endsOn) => updateDraft((draft) => ({ ...draft, endsOn }))}
          />
          <LifecycleDateFields
            label={t("accounts.lifecycle.renewsOn")}
            draft={session.draft.renewsOn}
            withSeconds={session.initial.renewsOn.time.length > 5}
            error={errors.renewsOn}
            disabled={saving}
            onChange={(renewsOn) => updateDraft((draft) => ({ ...draft, renewsOn }))}
          />
          <div className="min-w-0 space-y-1.5">
            <p className="text-xs text-muted-foreground">{t("accounts.lifecycle.cancellation")}</p>
            <Select
              value={session.draft.cancellationStatus}
              onValueChange={(value) =>
                updateDraft((draft) => ({ ...draft, cancellationStatus: value as LifecycleCancellationChoice }))
              }
              disabled={saving}
            >
              <SelectTrigger
                size="sm"
                className="h-8 w-full min-w-0 text-xs sm:w-44"
                aria-label={t("accounts.lifecycle.cancellationAria")}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unset">{t("accounts.lifecycle.notSet")}</SelectItem>
                <SelectItem value="not_cancelled">{t(CANCELLATION_LABEL_KEYS.not_cancelled)}</SelectItem>
                <SelectItem value="cancelled">{t(CANCELLATION_LABEL_KEYS.cancelled)}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {saveError ? (
            <p role="alert" className="break-words text-xs text-destructive">
              {saveError}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="outline" className="h-8 text-xs" disabled={saving} onClick={cancelEditing}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" size="sm" className="h-8 text-xs" disabled={saving || busy || invalid}>
              {saving ? t("accounts.lifecycle.saving") : t("common.actions.save")}
            </Button>
          </div>
        </form>
      ) : loaded ? (
        <dl className="space-y-2 text-xs">
          <LifecycleRow label={t("accounts.lifecycle.endsOn")}>
            <LifecycleDateValue value={loaded.endsOn} />
          </LifecycleRow>
          <LifecycleRow label={t("accounts.lifecycle.renewsOn")}>
            <LifecycleDateValue value={loaded.renewsOn} />
          </LifecycleRow>
          <LifecycleRow label={t("accounts.lifecycle.cancellation")}>
            {loaded.cancellationStatus ? (
              t(CANCELLATION_LABEL_KEYS[loaded.cancellationStatus])
            ) : (
              <NotSet />
            )}
          </LifecycleRow>
        </dl>
      ) : null}

      <dl className="text-xs">
        <LifecycleRow label={t("accounts.actions.routingPolicy")}>{t(ROUTING_POLICY_LABEL_KEYS[routingPolicy])}</LifecycleRow>
      </dl>
      <p className="text-xs text-muted-foreground">{t("accounts.lifecycle.routingNote")}</p>
    </section>
  );
}

function LifecycleRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-2">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-right font-medium">{children}</dd>
    </div>
  );
}

function NotSet() {
  const { t } = useTranslation();
  return <span className="font-normal text-muted-foreground">{t("accounts.lifecycle.notSet")}</span>;
}

function LifecycleDateValue({ value }: { value: AccountLifecycleDate | null }) {
  const { t, i18n } = useTranslation();
  const dateDisplayFormat = useDateDisplayFormatStore((state) => state.dateDisplayFormat);
  if (!value) {
    return <NotSet />;
  }
  const locale = i18n.resolvedLanguage ?? i18n.language ?? "en";
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
      <span className="font-mono tabular-nums">{formatLifecycleDate(value, dateDisplayFormat, locale)}</span>
      {value.precision === "date" ? (
        <span className="rounded border px-1 text-[10px] font-normal text-muted-foreground">
          {t("accounts.lifecycle.dateOnly")}
        </span>
      ) : null}
    </span>
  );
}

type LifecycleDateFieldsProps = {
  label: string;
  draft: LifecycleDateDraft;
  withSeconds: boolean;
  error: LifecycleDateDraftError | null;
  disabled: boolean;
  onChange: (draft: LifecycleDateDraft) => void;
};

function LifecycleDateFields({ label, draft, withSeconds, error, disabled, onChange }: LifecycleDateFieldsProps) {
  const { t } = useTranslation();
  const errorId = useId();
  const describedBy = error ? errorId : undefined;

  const changePrecision = (precision: LifecyclePrecisionChoice) => {
    // A local time needs a declared zone; offer the browser's as a starting point only.
    const timezone = precision === "datetime" && !draft.timezone.trim() ? browserTimeZone() : draft.timezone;
    onChange({ ...draft, precision, timezone });
  };

  return (
    <fieldset className="min-w-0 space-y-1.5">
      <legend className="text-xs text-muted-foreground">{label}</legend>
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap">
        <Select
          value={draft.precision}
          onValueChange={(value) => changePrecision(value as LifecyclePrecisionChoice)}
          disabled={disabled}
        >
          <SelectTrigger
            size="sm"
            className="h-8 w-full min-w-0 text-xs sm:w-36"
            aria-label={t("accounts.lifecycle.precisionAria", { field: label })}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="unset">{t("accounts.lifecycle.notSet")}</SelectItem>
            <SelectItem value="date">{t("accounts.lifecycle.dateOnly")}</SelectItem>
            <SelectItem value="datetime">{t("accounts.lifecycle.dateTime")}</SelectItem>
          </SelectContent>
        </Select>
        {draft.precision !== "unset" ? (
          <Input
            type="date"
            className="h-8 text-xs sm:w-40"
            aria-label={t("accounts.lifecycle.dateAria", { field: label })}
            aria-invalid={error === "dateRequired" || error === "dateInvalid"}
            aria-describedby={describedBy}
            value={draft.date}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, date: event.target.value })}
          />
        ) : null}
        {draft.precision === "datetime" ? (
          <>
            <Input
              type="time"
              step={withSeconds ? 1 : 60}
              className="h-8 text-xs sm:w-32"
              aria-label={t("accounts.lifecycle.timeAria", { field: label })}
              aria-invalid={error === "timeRequired" || error === "timeInvalid"}
              aria-describedby={describedBy}
              value={draft.time}
              disabled={disabled}
              onChange={(event) => onChange({ ...draft, time: event.target.value })}
            />
            <Input
              type="text"
              className="h-8 text-xs sm:w-48"
              aria-label={t("accounts.lifecycle.timezoneAria", { field: label })}
              aria-invalid={error === "timezoneRequired" || error === "timezoneInvalid"}
              aria-describedby={describedBy}
              placeholder={t("accounts.lifecycle.timezonePlaceholder")}
              autoComplete="off"
              spellCheck={false}
              value={draft.timezone}
              disabled={disabled}
              onChange={(event) => onChange({ ...draft, timezone: event.target.value })}
            />
          </>
        ) : null}
      </div>
      {error ? (
        <p id={errorId} className="text-xs text-destructive">
          {t(VALIDATION_MESSAGE_KEYS[error])}
        </p>
      ) : null}
    </fieldset>
  );
}
