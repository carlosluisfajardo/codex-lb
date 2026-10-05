from __future__ import annotations

from app.db.models import AccountLifecyclePreference
from app.modules.account_lifecycle.repository import AccountLifecycleRepository, LifecycleColumns, LifecycleKey
from app.modules.account_lifecycle.schemas import (
    AccountLifecycleResponse,
    AccountLifecycleUpdateRequest,
    CancellationStatus,
    LifecycleDate,
)


class AccountLifecycleConflictError(Exception):
    """The write was based on a revision that is no longer the stored one; nothing changed."""


def lifecycle_columns(
    ends_on: LifecycleDate | None,
    renews_on: LifecycleDate | None,
    cancellation_status: CancellationStatus | None,
) -> LifecycleColumns:
    return LifecycleColumns(
        ends_on_date=ends_on.date if ends_on is not None else None,
        ends_on_time=ends_on.time if ends_on is not None else None,
        ends_on_timezone=ends_on.timezone if ends_on is not None else None,
        renews_on_date=renews_on.date if renews_on is not None else None,
        renews_on_time=renews_on.time if renews_on is not None else None,
        renews_on_timezone=renews_on.timezone if renews_on is not None else None,
        cancellation_status=cancellation_status,
    )


def stored_lifecycle_date(date: str | None, time: str | None, timezone: str | None) -> LifecycleDate | None:
    """Rebuild a stored value as declared. Values were validated on write and are returned verbatim."""

    if date is None:
        return None
    if time is None:
        return LifecycleDate.model_construct(precision="date", date=date, time=None, timezone=None)
    return LifecycleDate.model_construct(precision="datetime", date=date, time=time, timezone=timezone)


def stored_cancellation_status(value: str | None) -> CancellationStatus | None:
    if value is None:
        return None
    if value == "not_cancelled":
        return "not_cancelled"
    if value == "cancelled":
        return "cancelled"
    raise ValueError(f"unexpected stored cancellation status {value!r}")


def lifecycle_response(account_id: str, row: AccountLifecyclePreference | None) -> AccountLifecycleResponse:
    if row is None:
        return AccountLifecycleResponse(
            account_id=account_id,
            ends_on=None,
            renews_on=None,
            cancellation_status=None,
            revision=0,
            updated_at=None,
        )
    return AccountLifecycleResponse(
        account_id=row.account_id,
        ends_on=stored_lifecycle_date(row.ends_on_date, row.ends_on_time, row.ends_on_timezone),
        renews_on=stored_lifecycle_date(row.renews_on_date, row.renews_on_time, row.renews_on_timezone),
        cancellation_status=stored_cancellation_status(row.cancellation_status),
        revision=row.revision,
        updated_at=row.updated_at,
    )


class AccountLifecycleService:
    def __init__(self, repository: AccountLifecycleRepository) -> None:
        self._repo = repository

    async def get(self, account_id: str) -> AccountLifecycleResponse | None:
        """``None`` when the account is absent or pending deletion; retained records stay hidden then."""

        key = await self._current_key(account_id)
        if key is None:
            return None
        return lifecycle_response(account_id, await self._repo.get(key))

    async def update(self, account_id: str, request: AccountLifecycleUpdateRequest) -> AccountLifecycleResponse | None:
        key = await self._current_key(account_id)
        if key is None:
            return None
        saved = await self._repo.save(
            key,
            lifecycle_columns(request.ends_on, request.renews_on, request.cancellation_status),
            expected_revision=request.expected_revision,
        )
        if not saved:
            raise AccountLifecycleConflictError(
                "Lifecycle details were changed since they were loaded; reload and apply the edit again"
            )
        return lifecycle_response(account_id, await self._repo.get(key))

    async def _current_key(self, account_id: str) -> LifecycleKey | None:
        incarnation = (await self._repo.visible_account_incarnations({account_id})).get(account_id)
        return LifecycleKey(account_id, incarnation) if incarnation is not None else None
