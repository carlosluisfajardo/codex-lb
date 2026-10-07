from __future__ import annotations

import time

import pytest

from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorParam,
    is_previous_response_not_found_error,
    is_previous_response_not_found_public_shape,
    previous_response_id_from_not_found_message,
    previous_response_stream_incomplete_error,
    response_failed_event,
    sanitize_public_upstream_error_message,
)


def test_response_failed_event_includes_incomplete_details():
    event = response_failed_event("stream_incomplete", "Upstream closed stream", response_id="resp_1")

    response = event["response"]
    assert "incomplete_details" in response
    assert response["incomplete_details"] is None


def test_response_failed_event_accepts_incomplete_details():
    event = response_failed_event(
        "stream_incomplete",
        "Upstream closed stream",
        response_id="resp_1",
        incomplete_details={"reason": "max_output_tokens"},
    )

    response = event["response"]
    assert response.get("incomplete_details") == {"reason": "max_output_tokens"}


def test_response_failed_event_preserves_reset_hint():
    event = response_failed_event(
        "usage_limit_reached",
        "Rate limit exceeded. Try again in 1h",
        error_type="usage_limit_reached",
        response_id="resp_1",
        resets_at=1_700_003_600,
    )

    assert event["response"]["error"].get("resets_at") == 1_700_003_600


def test_previous_response_not_found_classifier_covers_openai_shapes():
    assert is_previous_response_not_found_error(
        code="previous_response_not_found",
        param=None,
        message="Previous response with id 'resp_abc' not found.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message='Previous response with id "resp_abc" not found.',
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message="Invalid `previous_response_id`.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message='Previous response with id "resp_abc" not found.',
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message="Invalid `previous_response_id`.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid request payload.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`...",
    )
    assert not is_previous_response_not_found_error(
        code=None,
        param=None,
        message="Invalid `previous_response_id`.",
    )


def test_previous_response_not_found_classifier_covers_parameterless_invalid_anchor():
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message="Invalid previous_response_id.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message="Invalid previous_response_id.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid input.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="A required tool output from the previous response was not found.",
    )


def test_previous_response_not_found_classifier_rejects_non_string_param():
    for param in (0, False, {}, []):
        assert not is_previous_response_not_found_error(
            code="invalid_request_error",
            param=param,
            message="Invalid previous_response_id.",
        )


def test_error_param_retains_presence_and_normalizes_public_strings():
    absent = OpenAIErrorParam.absent()
    assert not absent.present
    assert absent.raw is None
    assert absent.normalized is None
    assert not absent.malformed

    valid = OpenAIErrorParam(True, " previous_response_id ")
    assert valid.present
    assert valid.normalized == "previous_response_id"
    assert not valid.malformed

    for raw in (None, 0, False, {}, [], "", "   "):
        malformed = OpenAIErrorParam(True, raw)
        assert malformed.present
        assert malformed.malformed


def test_previous_response_not_found_classifier_fails_closed_for_malformed_present_param():
    message = "Invalid `previous_response_id`."
    for param in (OpenAIErrorParam(True, None), OpenAIErrorParam(True, 0), OpenAIErrorParam(True, " ")):
        assert not is_previous_response_not_found_error(
            code="invalid_request_error",
            param=param,
            message=message,
        )


def test_previous_response_not_found_public_shape_masks_malformed_stale_id_message():
    assert is_previous_response_not_found_public_shape(
        code="invalid_request_error",
        param=OpenAIErrorParam(True, None),
        message="Previous response with id 'resp_abc' not found.",
    )
    assert not is_previous_response_not_found_public_shape(
        code="invalid_request_error",
        param=OpenAIErrorParam(True, "input"),
        message="Previous response with id 'resp_abc' not found.",
    )
    assert not is_previous_response_not_found_public_shape(
        code="invalid_request_error",
        param=OpenAIErrorParam.absent(),
        message="A required tool output from the previous response was not found.",
    )


def test_response_failed_event_omits_malformed_param_and_trims_valid_param():
    malformed = response_failed_event(
        "stream_incomplete",
        "closed",
        error_param=OpenAIErrorParam(True, None),
    )
    assert "param" not in malformed["response"]["error"]

    valid = response_failed_event(
        "stream_incomplete",
        "closed",
        error_param=OpenAIErrorParam(True, " model "),
    )
    assert valid["response"]["error"].get("param") == "model"


def test_previous_response_id_from_not_found_message_extracts_anchor():
    assert (
        previous_response_id_from_not_found_message(
            'Previous response with id "resp_0ba42212936dca97016a0d52aec2588191bc2499d3088e4e3e" not found.'
        )
        == "resp_0ba42212936dca97016a0d52aec2588191bc2499d3088e4e3e"
    )


def test_previous_response_stream_incomplete_error_is_public_safe():
    payload = previous_response_stream_incomplete_error()

    assert payload["error"].get("code") == "stream_incomplete"
    assert payload["error"].get("type") == "server_error"
    assert payload["error"].get("message") == PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE


def test_sanitize_public_upstream_error_message_keeps_ordinary_text_unchanged():
    message = (
        "Invalid response.create payload: Invalid 'input[66].arguments': string too long. "
        "Expected a string with maximum length 1048576, but got a string with length 5098360 instead."
    )

    assert sanitize_public_upstream_error_message(message) == message


def test_sanitize_public_upstream_error_message_scrubs_untrusted_text():
    sanitized = sanitize_public_upstream_error_message(
        "bad\x00value\x1b[31m Authorization: Bearer abc.def-ghi for owner@example.com "
        "key sk-proj-abcdefghijklmnop0123 jwt eyJhbGciOiJI.eyJzdWIiOiIx.c2ln"
    )

    assert sanitized == "bad value [31m Authorization: [REDACTED] for [REDACTED] key [REDACTED] jwt [REDACTED]"


def test_sanitize_public_upstream_error_message_bounds_length_and_defaults_empty_text():
    bounded = sanitize_public_upstream_error_message("z " * 5_000)

    assert len(bounded) <= 1_000
    assert bounded.endswith(" [truncated]")
    assert sanitize_public_upstream_error_message(None) == "Upstream rejected the request"
    assert sanitize_public_upstream_error_message(" \x00 ") == "Upstream rejected the request"


def test_sanitize_public_upstream_error_message_cost_is_bounded_for_huge_untrusted_text():
    # Unbounded patterns made these inputs quadratic (tens of seconds per message).
    for hostile in ("eyJ-" * 1_250_000, "a" * 5_000_000, "x@" * 2_500_000):
        started = time.monotonic()
        sanitized = sanitize_public_upstream_error_message(hostile)
        assert time.monotonic() - started < 1.0
        assert len(sanitized) <= 1_000


def test_sanitize_public_upstream_error_message_does_not_leave_a_cut_token_unredacted():
    sanitized = sanitize_public_upstream_error_message("x " * 1_995 + "sk-proj-abcdefghijklmnop0123 tail")

    assert "sk-proj" not in sanitized


@pytest.mark.parametrize(
    "message",
    [
        " " * 4_001,
        "\n" * 4_001,
        "\xa0" * 4_001,
        " " * 4_000 + "x",
        "sk-" + "a" * 3_985 + ",victim@exam" + "ple.com,tail",
    ],
)
def test_sanitize_public_upstream_error_message_never_keeps_a_token_the_scan_window_cut(message: str):
    assert sanitize_public_upstream_error_message(message) == "Upstream rejected the request"


def test_sanitize_public_upstream_error_message_redacts_glued_encoded_and_keyed_secrets():
    sanitized = sanitize_public_upstream_error_message(
        "Authorization:Bearer%20eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig_x "
        "x_sk-proj-abcdefghijklmnop0123 access_token%3DeyJhbGciOiJI.eyJzdWIiOiIx.c2ln "
        "refresh_token%3Dopaque123 api_key=abcdef0123456789secret Basic dXNlcjpwYXNzd29yZA== "
        "http://user:pw@localhost:8080/ ok"
    )

    assert sanitized == (
        "Authorization:[REDACTED] x_[REDACTED] access_[REDACTED] refresh_[REDACTED] "
        "api_key=[REDACTED] Basic [REDACTED] http://[REDACTED]@localhost:8080/ ok"
    )


def test_sanitize_public_upstream_error_message_keeps_words_that_resemble_token_prefixes():
    message = "the risk-assessment task-scheduler for disk-imaging keyJudge says hey"

    assert sanitize_public_upstream_error_message(message) == message
