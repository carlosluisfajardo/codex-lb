from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.account_lifecycle.schemas import AccountLifecycleUpdateRequest, LifecycleDate

pytestmark = pytest.mark.unit


def _request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "endsOn": None,
        "renewsOn": None,
        "cancellationStatus": None,
        "expectedRevision": 0,
        "expectedConcurrencyToken": "0" * 64,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("value", ["2026-10-12", "2028-02-29", "0001-01-01", "9999-12-31"])
def test_civil_date_is_kept_verbatim(value: str) -> None:
    parsed = LifecycleDate.model_validate({"precision": "date", "date": value})

    assert parsed.model_dump(by_alias=True) == {"precision": "date", "date": value, "time": None, "timezone": None}


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-29",
        "2026-02-30",
        "2026-13-01",
        "2026-00-10",
        "2026-10-32",
        "2026-1-5",
        "20261012",
        "2026-W41-1",
        "2026-10-12T00:00",
        " 2026-10-12",
        "",
        "2026-10-12Z",
    ],
)
def test_impossible_or_non_canonical_dates_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        LifecycleDate.model_validate({"precision": "date", "date": value})


@pytest.mark.parametrize(
    ("time", "timezone"),
    [
        ("09:30", "America/New_York"),
        ("09:30:15", "America/New_York"),
        ("23:59:59", "UTC"),
        ("00:00", "+05:30"),
        ("12:00", "-12:00"),
        ("12:00", "+14:00"),
        ("12:00", "-00:00"),
    ],
)
def test_local_datetime_keeps_declared_time_and_timezone(time: str, timezone: str) -> None:
    parsed = LifecycleDate.model_validate(
        {"precision": "datetime", "date": "2026-11-03", "time": time, "timezone": timezone}
    )

    assert parsed.model_dump(by_alias=True) == {
        "precision": "datetime",
        "date": "2026-11-03",
        "time": time,
        "timezone": timezone,
    }


@pytest.mark.parametrize(
    "time",
    ["24:00", "9:30", "09:60", "09:30:60", "09:30:15.5", "0930", "09:30Z", "", "1\u0669:5\u0669", "\uff10\uff19:30"],
)
def test_invalid_times_are_rejected(time: str) -> None:
    with pytest.raises(ValidationError):
        LifecycleDate.model_validate({"precision": "datetime", "date": "2026-11-03", "time": time, "timezone": "UTC"})


@pytest.mark.parametrize(
    "timezone",
    [
        "Z",
        "+14:30",
        "-12:30",
        "+5:30",
        "05:30",
        "+05:60",
        "America/Not_A_Zone",
        "../etc/passwd",
        "",
        "EST5EDT/x",
        "+\u0660\u0665:\u0663\u0660",
        "+\uff11\uff14:\uff10\uff10",
        "america/new_york",
    ],
)
def test_unknown_or_out_of_range_timezones_are_rejected(timezone: str) -> None:
    with pytest.raises(ValidationError):
        LifecycleDate.model_validate(
            {"precision": "datetime", "date": "2026-11-03", "time": "09:30", "timezone": timezone}
        )


@pytest.mark.parametrize(
    "value",
    [
        {"precision": "datetime", "date": "2026-11-03", "time": "09:30"},
        {"precision": "datetime", "date": "2026-11-03", "timezone": "UTC"},
        {"precision": "datetime", "date": "2026-11-03", "time": "09:30", "timezone": None},
        {"precision": "date", "date": "2026-11-03", "time": "09:30"},
        {"precision": "date", "date": "2026-11-03", "timezone": "UTC"},
        {"precision": "instant", "date": "2026-11-03"},
        {"precision": "date"},
        {"date": "2026-11-03"},
        {"precision": "date", "date": "2026-11-03", "note": "extra"},
    ],
)
def test_precision_shape_is_enforced(value: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LifecycleDate.model_validate(value)


def test_update_request_accepts_explicit_nulls_and_both_cancellation_values() -> None:
    for status in (None, "not_cancelled", "cancelled"):
        request = AccountLifecycleUpdateRequest.model_validate(_request(cancellationStatus=status))
        assert request.cancellation_status == status
        assert request.ends_on is None and request.renews_on is None


@pytest.mark.parametrize(
    "missing", ["endsOn", "renewsOn", "cancellationStatus", "expectedRevision", "expectedConcurrencyToken"]
)
def test_update_request_requires_every_field(missing: str) -> None:
    payload = _request()
    payload.pop(missing)

    with pytest.raises(ValidationError):
        AccountLifecycleUpdateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"expectedRevision": -1},
        {"expectedRevision": 2**31 - 1},
        {"expectedRevision": 2**63},
        {"expectedRevision": "1"},
        {"cancellationStatus": "paused"},
        {"cancellationStatus": True},
        {"endsOn": "2026-10-12"},
        {"unexpected": 1},
    ],
)
def test_update_request_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AccountLifecycleUpdateRequest.model_validate(_request(**overrides))


def test_expected_revision_accepts_the_largest_storable_base_revision() -> None:
    request = AccountLifecycleUpdateRequest.model_validate(_request(expectedRevision=2**31 - 2))

    assert request.expected_revision == 2**31 - 2
