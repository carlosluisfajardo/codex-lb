from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.openai.model_registry import ModelRegistry
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.request_policy import apply_api_key_enforcement


def registry(*, ultra: bool = True, snapshot: bool = True) -> ModelRegistry:
    efforts = ["low", "medium", "high", "xhigh", "max"] + (["ultra"] if ultra else [])
    model = SimpleNamespace(supported_reasoning_levels=tuple(SimpleNamespace(effort=e) for e in efforts))
    return cast(
        ModelRegistry,
        SimpleNamespace(get_snapshot=lambda: SimpleNamespace(models={"test-sol": model}) if snapshot else None),
    )


def key() -> ApiKeyData:
    return cast(
        ApiKeyData,
        SimpleNamespace(
            id="floor-key",
            enforced_model=None,
            enforced_reasoning_effort="low",
            enforced_service_tier=None,
            allowed_models=None,
            allowed_reasoning_efforts=None,
        ),
    )


@pytest.mark.parametrize("request_type", [ResponsesRequest, ResponsesCompactRequest])
@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "xhigh", "minimal", "ultra", "max", " MAX "])
@pytest.mark.parametrize("keyed", [False, True])
def test_ultra_catalog_floor_reaches_upstream_payload(request_type, effort, keyed):
    body: dict[str, JsonValue] = dict(model="test-sol", instructions="", input=[], service_tier="priority")
    if effort is not None:
        body["reasoning"] = {"effort": effort, "summary": "auto"}
    payload = request_type.model_validate(body)
    result = apply_api_key_enforcement(payload, key() if keyed else None, registry=registry())
    wire_reasoning = payload.to_payload()["reasoning"]
    assert isinstance(wire_reasoning, dict)
    assert wire_reasoning["effort"] == "max"
    assert payload.model == "test-sol"
    assert payload.service_tier == "priority"
    assert result.pre_normalization_reasoning_effort is None
    if effort is not None:
        assert payload.reasoning.summary == "auto"


@pytest.mark.parametrize(
    ("model", "catalog"),
    [("test-sol", registry(ultra=False)), ("unknown", registry()), ("test-sol", registry(snapshot=False))],
)
def test_without_catalog_ultra_existing_effort_and_tier_are_preserved(model, catalog):
    payload = ResponsesRequest.model_validate(
        dict(model=model, instructions="", input=[], reasoning={"effort": "medium"}, service_tier="flex")
    )
    apply_api_key_enforcement(payload, None, registry=catalog)
    wire_reasoning = payload.to_payload()["reasoning"]
    assert isinstance(wire_reasoning, dict)
    assert wire_reasoning["effort"] == "medium"
    assert payload.model == model
    assert payload.service_tier == "flex"
