from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, NamedTuple, NotRequired, TypedDict

from app.core.types import JsonValue


@dataclass(frozen=True, slots=True)
class OpenAIErrorParam:
    """Preserve whether an upstream error supplied a ``param`` field."""

    present: bool
    raw: JsonValue | None = None

    @classmethod
    def absent(cls) -> "OpenAIErrorParam":
        return cls(False, None)

    @classmethod
    def from_mapping(cls, error: Mapping[str, JsonValue]) -> "OpenAIErrorParam":
        if "param" not in error:
            return cls.absent()
        return cls(True, error["param"])

    @property
    def normalized(self) -> str | None:
        return self.raw.strip() if isinstance(self.raw, str) else None

    @property
    def malformed(self) -> bool:
        return self.present and (self.normalized is None or not self.normalized)


def coerce_error_param(param: OpenAIErrorParam | JsonValue) -> OpenAIErrorParam:
    """Convert legacy raw values to presence-aware error parameter state."""

    if isinstance(param, OpenAIErrorParam):
        return param
    if param is None:
        return OpenAIErrorParam.absent()
    return OpenAIErrorParam(True, param)


def normalize_public_error_param(param: OpenAIErrorParam | JsonValue) -> str | None:
    """Return only a trimmed, non-empty string safe for a public response."""

    normalized = coerce_error_param(param).normalized
    return normalized if normalized else None


class OpenAIErrorDetail(TypedDict, total=False):
    message: str
    type: str
    code: str
    param: JsonValue
    plan_type: str
    resets_at: int | float
    resets_in_seconds: int | float


class OpenAIErrorEnvelope(TypedDict):
    error: OpenAIErrorDetail


class DashboardErrorDetail(TypedDict):
    code: str
    message: str
    param: NotRequired[str]
    details: NotRequired[dict[str, JsonValue]]


class DashboardErrorEnvelope(TypedDict):
    error: DashboardErrorDetail


class ResponseFailedResponse(TypedDict):
    object: str
    status: str
    error: OpenAIErrorDetail
    id: NotRequired[str]
    created_at: NotRequired[int]
    incomplete_details: NotRequired[dict[str, str] | None]


class ResponseFailedEvent(TypedDict):
    type: Literal["response.failed"]
    response: ResponseFailedResponse
    _codex_lb_synthetic_transport_failure: NotRequired[bool]


PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE = "Upstream websocket closed before response.completed"
# Local bridge recovery (fresh replay, context-overflow rollover, previous
# response rebind) tears down our own upstream session. It is not an upstream
# close and must not be reported as one.
HTTP_BRIDGE_LOCAL_RESET_MESSAGE = "HTTP responses session bridge reset this session locally before response.completed"
# ``stream_incomplete`` raised against a continuation anchor is ambiguous: the
# turn may already exist upstream. Neither of these messages proves the account
# misbehaved, so neither may drive an account-health penalty.
STREAM_INCOMPLETE_ANCHOR_NEUTRAL_MESSAGES = frozenset(
    {
        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
        HTTP_BRIDGE_LOCAL_RESET_MESSAGE,
    }
)
# Pre-response-start bridge silence. Distinct from ``stream_idle_timeout``,
# whose budget (``stream_idle_timeout_seconds``) only governs gaps *after*
# ``response.created``. When the bridge saw no unmatched upstream liveness,
# nothing was created upstream and a retry forks no context. If liveness was
# observed but not matched to a response, callers must treat retry as
# at-least-once.
HTTP_BRIDGE_EVENTLESS_TIMEOUT_CODE = "bridge_eventless_timeout"
PREVIOUS_RESPONSE_NOT_FOUND_CODE = "previous_response_not_found"
PREVIOUS_RESPONSE_NOT_FOUND_MESSAGE = "Previous response was not found; retry without previous_response_id."
PREVIOUS_RESPONSE_MALFORMED_PARAM_REASON = "previous_response_not_found_malformed_param"
SYNTHETIC_TRANSPORT_FAILURE_MARKER = "_codex_lb_synthetic_transport_failure"
SYNTHETIC_TRANSPORT_FAILURE_CODES = frozenset(
    {"stream_incomplete", "stream_idle_timeout", "upstream_request_timeout", "upstream_unavailable"}
)
# Every sentence upstream uses to say the account's subscription window is
# spent, written in the form ``is_upstream_usage_limit_message`` normalizes to:
# lowercase alphanumeric words joined by single spaces. The passive
# "The usage limit has been reached" is what the HTTP bridge, the Codex
# WebSocket and the SDK error body actually carry; the second-person forms come
# from the native client rendering and from the promo text appended to a
# decline. A table built from one voice is a table that never fires.
_USAGE_LIMIT_MESSAGE_MARKERS = (
    "usage limit has been reached",
    "usage limit reached",
    "hit your usage limit",
    "reached your usage limit",
    "exceeded your usage limit",
)
_MESSAGE_WORD_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def openai_error(
    code: str,
    message: str,
    error_type: str = "server_error",
    *,
    resets_at: int | float | None = None,
) -> OpenAIErrorEnvelope:
    detail: OpenAIErrorDetail = {"message": message, "type": error_type, "code": code}
    if resets_at is not None:
        detail["resets_at"] = int(resets_at)
    return {"error": detail}


def dashboard_error(
    code: str,
    message: str,
    *,
    param: str | None = None,
    details: Mapping[str, JsonValue] | None = None,
) -> DashboardErrorEnvelope:
    detail: DashboardErrorDetail = {"code": code, "message": message}
    if param is not None:
        detail["param"] = param
    if details:
        detail["details"] = dict(details)
    return {"error": detail}


#: RFC 7644 §3.12. Every body from ``/scim/v2`` carries this media type, and
#: every refusal carries the error schema below; an identity provider reads
#: both and neither the dashboard nor the OpenAI envelope means anything to it.
SCIM_CONTENT_TYPE = "application/scim+json"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class ScimErrorEnvelope(TypedDict):
    schemas: list[str]
    status: str
    detail: str
    scimType: NotRequired[str]


def scim_error(status_code: int, detail: str, *, scim_type: str | None = None) -> ScimErrorEnvelope:
    """RFC 7644's error body. ``status`` is a string there, not a number."""

    envelope: ScimErrorEnvelope = {
        "schemas": [SCIM_ERROR_SCHEMA],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type is not None:
        envelope["scimType"] = scim_type
    return envelope


def previous_response_stream_incomplete_error() -> OpenAIErrorEnvelope:
    return openai_error(
        "stream_incomplete",
        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
        error_type="server_error",
    )


def is_upstream_usage_limit_message(message: str | None) -> bool:
    """True when the message asserts the account's usage limit is spent.

    Upstream delivers this rejection as an HTTP body and as a serialized
    ``response.failed`` frame that carries no status and often no error code, so
    neither the status nor the code table can be the gate and every path that
    needs the answer has to read it from the same place. The words are matched
    after folding each run of non-alphanumeric characters to a single space,
    because the same sentence arrives with a straight apostrophe, a curly one,
    a hyphen joining "usage" and "limit", or wrapped across a line break.
    """
    if message is None:
        return False
    normalized = _MESSAGE_WORD_SEPARATOR_RE.sub(" ", message.lower()).strip()
    return any(marker in normalized for marker in _USAGE_LIMIT_MESSAGE_MARKERS)


def is_previous_response_not_found_message(message: str | None) -> bool:
    if message is None:
        return False
    normalized = " ".join(message.lower().split())
    return "previous response" in normalized and "not found" in normalized


def _is_invalid_previous_response_id_message(message: str | None) -> bool:
    if message is None:
        return False
    normalized = " ".join(message.casefold().replace("`", "").split()).removesuffix(".").rstrip()
    return normalized == "invalid previous_response_id"


def previous_response_id_from_not_found_message(message: str | None) -> str | None:
    if message is None:
        return None
    normalized = " ".join(message.split())
    match = re.search(
        r"""previous\s+response\s+with\s+id\s+['"](?P<response_id>[^'"]+)['"]\s+not\s+found""",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    response_id = match.group("response_id").strip()
    return response_id or None


def sanitize_public_error_detail(error: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Copy an error detail while omitting malformed ``param`` values."""

    normalized = dict(error)
    if "param" not in normalized:
        return normalized
    public_param = normalize_public_error_param(OpenAIErrorParam.from_mapping(error))
    if public_param is None:
        normalized.pop("param", None)
    else:
        normalized["param"] = public_param
    return normalized


# An upstream request rejection reaches the client rebuilt from validated parts:
# upstream text is never copied, because it can echo request bodies, secrets
# or account data. A well-formed identifier is not proof a value is public, so
# delivered text is drawn only from the fixed vocabularies below and from
# integers re-rendered from a recognized diagnostic. Recognition reads at most
# this many leading characters.
PUBLIC_REQUEST_REJECTION_SCAN_MAX_CHARS: Final[int] = 512
_PUBLIC_ERROR_PARAM_MAX_CHARS: Final[int] = 128
_PUBLIC_ERROR_PARAM_MAX_SEGMENTS: Final[int] = 8
# Public request rejection codes of the Responses API.
_PUBLIC_REQUEST_ERROR_CODES: Final = frozenset(
    {
        "array_above_max_length",
        "array_below_min_length",
        "context_length_exceeded",
        "decimal_above_max_value",
        "decimal_below_min_value",
        "empty_array",
        "integer_above_max_value",
        "integer_below_min_value",
        "invalid_encrypted_content",
        "invalid_prompt",
        "invalid_request_error",
        "invalid_type",
        "invalid_value",
        "misalignment_policy_violation",
        "missing_required_parameter",
        "model_not_found",
        "string_above_max_length",
        "string_below_min_length",
        "unknown_parameter",
        "unsupported_parameter",
        "unsupported_value",
    }
)
# Field names of the Responses request schema; a ``param`` path is delivered
# only when every segment names one of them.
_PUBLIC_REQUEST_FIELDS: Final = frozenset(
    {
        "action",
        "annotations",
        "arguments",
        "background",
        "call_id",
        "content",
        "conversation",
        "description",
        "detail",
        "effort",
        "encrypted_content",
        "file_data",
        "file_id",
        "file_url",
        "filename",
        "format",
        "function",
        "id",
        "image_url",
        "include",
        "input",
        "instructions",
        "max_output_tokens",
        "max_tool_calls",
        "metadata",
        "model",
        "name",
        "output",
        "parallel_tool_calls",
        "parameters",
        "previous_response_id",
        "prompt",
        "prompt_cache_key",
        "reasoning",
        "role",
        "safety_identifier",
        "schema",
        "service_tier",
        "status",
        "store",
        "stream",
        "strict",
        "summary",
        "temperature",
        "text",
        "tool_choice",
        "tools",
        "top_logprobs",
        "top_p",
        "truncation",
        "type",
        "user",
        "verbosity",
    }
)
_PUBLIC_ERROR_PARAM_SEGMENT: Final = re.compile(r"(?P<field>[a-z_]{1,32})(?:\[[0-9]{1,7}\])?")
_ARGUMENTS_TOO_LONG_DIAGNOSTIC: Final = re.compile(
    r"Invalid 'input\[(?P<index>[0-9]{1,7})\]\.arguments': string too long\. "
    r"Expected a string with maximum length (?P<maximum>[0-9]{1,10}), "
    r"but got a string with length (?P<actual>[0-9]{1,13}) instead\."
)
_MODEL_UNSUPPORTED_DIAGNOSTIC: Final = re.compile(
    r"The '[A-Za-z0-9][A-Za-z0-9._:-]{0,63}' model is not supported when using Codex with a ChatGPT account\."
)
_PUBLIC_UNSUPPORTED_MODEL_MESSAGE: Final[str] = (
    "The requested model is not supported when using Codex with a ChatGPT account. "
    "Retrying the same request fails the same way; choose a different model."
)


class PublicRequestRejection(NamedTuple):
    code: str
    message: str
    param: str | None


def _public_request_param(param: JsonValue) -> str | None:
    if not isinstance(param, str) or len(param) > _PUBLIC_ERROR_PARAM_MAX_CHARS:
        return None
    segments = param.split(".", _PUBLIC_ERROR_PARAM_MAX_SEGMENTS)
    if len(segments) > _PUBLIC_ERROR_PARAM_MAX_SEGMENTS:
        return None
    for segment in segments:
        match = _PUBLIC_ERROR_PARAM_SEGMENT.fullmatch(segment)
        if match is None or match["field"] not in _PUBLIC_REQUEST_FIELDS:
            return None
    return param


def public_request_rejection(*, code: JsonValue, message: JsonValue, param: JsonValue) -> PublicRequestRejection:
    """Rebuild an upstream request rejection from validated parts only.

    The arguments-length diagnostic is restated from its re-rendered numbers and
    the unsupported-model diagnostic as fixed text; any other message becomes a
    fixed instruction. ``code`` is kept only when it is a listed public request
    rejection code, and ``param`` only when it is a bounded path of listed
    Responses request field names and indices; otherwise they are replaced or
    dropped.
    """
    public_param = _public_request_param(param)
    text = message[:PUBLIC_REQUEST_REJECTION_SCAN_MAX_CHARS] if isinstance(message, str) else ""
    arguments = _ARGUMENTS_TOO_LONG_DIAGNOSTIC.search(text)
    if arguments is not None and 0 < int(arguments["maximum"]) < int(arguments["actual"]):
        index = int(arguments["index"])
        return PublicRequestRejection(
            "string_above_max_length",
            f"Invalid 'input[{index}].arguments': string too long. Expected a string with maximum length "
            f"{int(arguments['maximum'])}, but got a string with length {int(arguments['actual'])} instead. "
            "Upstream rejects this history item on every request; continue in a new conversation without it.",
            f"input[{index}].arguments",
        )
    public_code = code if isinstance(code, str) and code in _PUBLIC_REQUEST_ERROR_CODES else "invalid_request_error"
    if _MODEL_UNSUPPORTED_DIAGNOSTIC.search(text) is not None:
        return PublicRequestRejection(public_code, _PUBLIC_UNSUPPORTED_MODEL_MESSAGE, public_param)
    location = f" at '{public_param}'" if public_param is not None else ""
    return PublicRequestRejection(
        public_code,
        f"Upstream rejected the request as invalid{location}. Retrying the same request fails the same way; "
        "change the request or continue in a new conversation.",
        public_param,
    )


def is_previous_response_not_found_error(
    *,
    code: str | None,
    param: OpenAIErrorParam | JsonValue,
    message: str | None,
) -> bool:
    param_state = coerce_error_param(param)
    if param_state.malformed:
        return False
    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    if code != "invalid_request_error":
        return False
    if not param_state.present:
        return _is_invalid_previous_response_id_message(message)
    if param_state.normalized != "previous_response_id":
        return False
    return is_previous_response_not_found_message(message) or _is_invalid_previous_response_id_message(message)


def is_previous_response_not_found_public_shape(
    *,
    code: str | None,
    param: OpenAIErrorParam | JsonValue,
    message: str | None,
) -> bool:
    """Match stale-anchor errors for masking without authorizing replay."""

    if code == PREVIOUS_RESPONSE_NOT_FOUND_CODE:
        return True
    param_state = coerce_error_param(param)
    if (
        code == "invalid_request_error"
        and param_state.present
        and not param_state.malformed
        and param_state.normalized != "previous_response_id"
    ):
        return False
    if code == "invalid_request_error" and (
        _is_invalid_previous_response_id_message(message)
        or previous_response_id_from_not_found_message(message) is not None
    ):
        return True
    return is_previous_response_not_found_error(code=code, param=param, message=message)


def response_failed_event(
    code: str,
    message: str,
    error_type: str = "server_error",
    response_id: str | None = None,
    created_at: int | None = None,
    error_param: OpenAIErrorParam | JsonValue = None,
    resets_at: int | float | None = None,
    incomplete_details: dict[str, str] | None = None,
) -> ResponseFailedEvent:
    error = openai_error(code, message, error_type, resets_at=resets_at)["error"]
    public_param = normalize_public_error_param(error_param)
    if public_param is not None:
        error["param"] = public_param
    if created_at is None:
        created_at = int(time.time())
    response: ResponseFailedResponse = {
        "object": "response",
        "status": "failed",
        "error": error,
    }
    response["incomplete_details"] = incomplete_details
    if response_id:
        response["id"] = response_id
    if created_at is not None:
        response["created_at"] = created_at
    return {"type": "response.failed", "response": response}


def synthetic_transport_failure_event(event: ResponseFailedEvent) -> ResponseFailedEvent:
    """Mark an LB-generated transport terminal for boundary-only handling."""
    event[SYNTHETIC_TRANSPORT_FAILURE_MARKER] = True
    return event


def synthetic_stream_failure_event(
    code: str,
    message: str,
    error_type: str = "server_error",
    response_id: str | None = None,
    created_at: int | None = None,
    error_param: OpenAIErrorParam | JsonValue = None,
    resets_at: int | float | None = None,
    incomplete_details: dict[str, str] | None = None,
) -> ResponseFailedEvent:
    """Build a failed event and mark transport codes manufactured by codex-lb."""
    event = response_failed_event(
        code,
        message,
        error_type,
        response_id,
        created_at,
        error_param,
        resets_at,
        incomplete_details,
    )
    return synthetic_transport_failure_event(event) if code in SYNTHETIC_TRANSPORT_FAILURE_CODES else event
