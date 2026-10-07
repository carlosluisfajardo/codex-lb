from __future__ import annotations

import time

import pytest

from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    PUBLIC_REQUEST_REJECTION_SCAN_MAX_CHARS,
    OpenAIErrorParam,
    is_previous_response_not_found_error,
    is_previous_response_not_found_public_shape,
    previous_response_id_from_not_found_message,
    previous_response_stream_incomplete_error,
    public_request_rejection,
    response_failed_event,
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


_ARGUMENTS_DIAGNOSTIC = (
    "Invalid 'input[66].arguments': string too long. Expected a string with maximum length 1048576, "
    "but got a string with length 5098360 instead."
)
_RESTATED_ARGUMENTS_DIAGNOSTIC = (
    _ARGUMENTS_DIAGNOSTIC + " Upstream rejects this history item on every request; "
    "continue in a new conversation without it."
)
_FALLBACK = (
    "Upstream rejected the request as invalid{location}. Retrying the same request fails the same way; "
    "change the request or continue in a new conversation."
)
_MARKER = "SYNTHETIC_PRIVATE_REQUEST_BODY"
_UNSUPPORTED_MODEL = (
    "The requested model is not supported when using Codex with a ChatGPT account. "
    "Retrying the same request fails the same way; choose a different model."
)
# A dotted base64url credential shape that is also a well-formed field path.
_SYNTHETIC_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJTWU5USEVUSUNfUFJJVkFURSJ9.SYNTHETIC_SIGNATURE"


@pytest.mark.parametrize(
    "message",
    [
        _ARGUMENTS_DIAGNOSTIC,
        "Invalid response.create payload: " + _ARGUMENTS_DIAGNOSTIC,
        f'{_MARKER} {_ARGUMENTS_DIAGNOSTIC} {_MARKER} {{"password": "{_MARKER}"}}',
    ],
)
def test_public_request_rejection_restates_the_arguments_diagnostic_from_validated_numbers(message: str):
    rejection = public_request_rejection(code=None, message=message, param=_MARKER)

    assert rejection == ("string_above_max_length", _RESTATED_ARGUMENTS_DIAGNOSTIC, "input[66].arguments")


@pytest.mark.parametrize(
    "message",
    [
        "Invalid 'input[-1].arguments': string too long. Expected a string with maximum length 1048576, "
        "but got a string with length 5098360 instead.",
        "Invalid 'input[66].arguments': string too long. Expected a string with maximum length -7, "
        "but got a string with length -8 instead.",
        "Invalid 'input[66].arguments': string too long. Expected a string with maximum length 9, "
        "but got a string with length 9 instead.",
        "Invalid 'input[66].arguments': string too long. Expected a string with maximum length 0, "
        "but got a string with length 9 instead.",
        "Invalid 'input[\u0666].arguments': string too long. Expected a string with maximum length 1048576, "
        "but got a string with length 5098360 instead.",
        "Invalid 'input[66].arguments': string too long. Expected a string with maximum length 1048576, "
        "but got a string with length 50983",
        _MARKER,
        " " * 4_001,
        "eyJ" + "a" * 5_000 + ".bbbbbbbb.cccccccc",
        "z" * 4_001,
        None,
        {"message": _MARKER},
    ],
)
def test_public_request_rejection_uses_the_fixed_instruction_for_anything_else(message):
    rejection = public_request_rejection(code="invalid_request_error", message=message, param="input[66].arguments")

    assert rejection == (
        "invalid_request_error",
        _FALLBACK.format(location=" at 'input[66].arguments'"),
        "input[66].arguments",
    )


@pytest.mark.parametrize(
    ("code", "param", "expected_code", "expected_param"),
    [
        ("unsupported_value", "parallel_tool_calls", "unsupported_value", "parallel_tool_calls"),
        ("context_length_exceeded", "input[1].content[0].text", "context_length_exceeded", "input[1].content[0].text"),
        (_MARKER, _MARKER + " x", "invalid_request_error", None),
        ("a" * 65, "p" * 129, "invalid_request_error", None),
        ("invalid_value", "tools[0].function.parameters", "invalid_value", "tools[0].function.parameters"),
        ("ok_code", "input[1].arguments'; DROP", "invalid_request_error", None),
        ("Bearer abc", "input[12345678].arguments", "invalid_request_error", None),
        ("deadbeef" * 5, _SYNTHETIC_JWT, "invalid_request_error", None),
        ("deadbeef" * 4, "deadbeef" * 4, "invalid_request_error", None),
        ("invalid_value", "input[1].secret_field", "invalid_value", None),
        ("invalid_value", ".".join(["input"] * 9), "invalid_value", None),
        (None, None, "invalid_request_error", None),
        (7, ["input"], "invalid_request_error", None),
    ],
)
def test_public_request_rejection_keeps_only_validated_bounded_metadata(code, param, expected_code, expected_param):
    rejection = public_request_rejection(code=code, message=_MARKER, param=param)

    location = f" at '{expected_param}'" if expected_param is not None else ""
    assert rejection == (expected_code, _FALLBACK.format(location=location), expected_param)


@pytest.mark.parametrize("model", ["gpt-5.3-codex-spark", "sk-SYNTHETICPRIVATE" + "A" * 20])
def test_public_request_rejection_states_a_recognized_unsupported_model_as_fixed_text(model: str):
    message = f"The '{model}' model is not supported when using Codex with a ChatGPT account. {_MARKER}"

    assert public_request_rejection(code="invalid_request_error", message=message, param="model") == (
        "invalid_request_error",
        _UNSUPPORTED_MODEL,
        "model",
    )
    assert public_request_rejection(
        code="invalid_request_error",
        message="The 'gpt 5 with spaces' model is not supported when using Codex with a ChatGPT account.",
        param=None,
    ) == ("invalid_request_error", _FALLBACK.format(location=""), None)


def test_public_request_rejection_reads_only_a_bounded_prefix(monkeypatch):
    import app.core.errors as errors_module

    scanned: list[int] = []

    class _ScanSpy:
        def __init__(self, pattern):
            self._pattern = pattern

        def search(self, text):
            scanned.append(len(text))
            return self._pattern.search(text)

    for name in ("_ARGUMENTS_TOO_LONG_DIAGNOSTIC", "_MODEL_UNSUPPORTED_DIAGNOSTIC"):
        monkeypatch.setattr(errors_module, name, _ScanSpy(getattr(errors_module, name)))

    started = time.monotonic()
    late = public_request_rejection(code=None, message="x" * (1 << 20) + _ARGUMENTS_DIAGNOSTIC, param=None)

    assert time.monotonic() - started < 1.0
    assert late.code == "invalid_request_error"
    assert scanned and max(scanned) == PUBLIC_REQUEST_REJECTION_SCAN_MAX_CHARS
