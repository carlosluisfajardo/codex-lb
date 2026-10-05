from __future__ import annotations

import re
from datetime import date, datetime
from functools import cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import ConfigDict, Field, model_validator

from app.modules.shared.schemas import DashboardModel

CancellationStatus = Literal["not_cancelled", "cancelled"]
LifecyclePrecision = Literal["date", "datetime"]

_CIVIL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_LOCAL_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", re.ASCII)
_UTC_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})", re.ASCII)
_MIN_OFFSET_MINUTES = -12 * 60
_MAX_OFFSET_MINUTES = 14 * 60
# ``revision`` is an int4 column on PostgreSQL; a based-on revision must leave room for the increment.
MAX_STORED_REVISION = 2**31 - 1
MAX_BASE_REVISION = MAX_STORED_REVISION - 1


@cache
def _iana_zone_names() -> frozenset[str]:
    return frozenset(available_timezones())


def _validate_civil_date(value: str) -> None:
    if _CIVIL_DATE.fullmatch(value) is None:
        raise ValueError("date must be written YYYY-MM-DD")
    date.fromisoformat(value)


def _validate_local_time(value: str) -> None:
    if _LOCAL_TIME.fullmatch(value) is None:
        raise ValueError("time must be written HH:MM or HH:MM:SS on a 24-hour clock")


def _validate_timezone(value: str) -> None:
    offset = _UTC_OFFSET.fullmatch(value)
    if offset is not None:
        sign, hours, minutes = offset.groups()
        total = (int(hours) * 60 + int(minutes)) * (-1 if sign == "-" else 1)
        if int(minutes) < 60 and _MIN_OFFSET_MINUTES <= total <= _MAX_OFFSET_MINUTES:
            return
        raise ValueError("timezone offset must be between -12:00 and +14:00")
    if value not in _iana_zone_names():
        raise ValueError("timezone must be an IANA time zone name or a +HH:MM/-HH:MM offset")


class LifecycleDate(DashboardModel):
    """A lifecycle date exactly as the operator declared it; never an instant."""

    model_config = ConfigDict(extra="forbid")

    precision: LifecyclePrecision
    date: str
    time: str | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_declared_shape(self) -> LifecycleDate:
        _validate_civil_date(self.date)
        if self.precision == "date":
            if self.time is not None or self.timezone is not None:
                raise ValueError("a date-only value must not carry a time or timezone")
            return self
        if self.time is None or self.timezone is None:
            raise ValueError("a datetime value needs both a time and a timezone")
        _validate_local_time(self.time)
        _validate_timezone(self.timezone)
        return self


class AccountLifecycleResponse(DashboardModel):
    account_id: str
    ends_on: LifecycleDate | None
    renews_on: LifecycleDate | None
    cancellation_status: CancellationStatus | None
    revision: int = Field(ge=0)
    # Opaque and non-secret; it changes when the id names a different account row, so a save must echo it.
    concurrency_token: str
    updated_at: datetime | None


class AccountLifecycleUpdateRequest(DashboardModel):
    """Full replacement of the three fields, based on the revision and account row the draft was read from.

    ``expected_revision`` is 0 when nothing was saved; ``expected_concurrency_token`` is the
    ``concurrency_token`` of that same read.
    """

    model_config = ConfigDict(extra="forbid")

    ends_on: LifecycleDate | None
    renews_on: LifecycleDate | None
    cancellation_status: CancellationStatus | None
    expected_revision: int = Field(ge=0, le=MAX_BASE_REVISION, strict=True)
    expected_concurrency_token: str = Field(pattern=r"^[0-9a-f]{64}$")
