"""Own the one pre-dispatch required-owner selection window and its leases."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from app.core.balancer import OwnerFailureReason
from app.core.clients.proxy import ProxyResponseError
from app.core.clock import clock_for, scheduler_for
from app.core.errors import openai_error
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_previous_response_owner_unavailable_error,
    _HTTPBridgeRequiredOwnerReplayForbiddenError,
    _HTTPBridgeRequiredOwnerSelectionError,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.service_stubs import _record_same_account_takeover
from app.modules.proxy._service.http_bridge.session_registry import _HTTPBridgeCreationFuture
from app.modules.proxy.load_balancer import CONTINUITY_OWNER_UNAVAILABLE, AccountSelection
from app.modules.proxy.selection_errors import selection_failure_response


@dataclass
class _HTTPBridgeOwnerSelection:
    request_deadline: float
    preferred_account_id: str | None
    require_preferred_account: bool
    continuity_owner: bool
    allow_recovery: bool
    excluded_account_ids: Collection[str]
    response_create_attempt_count: int = 0
    creation: _HTTPBridgeCreationFuture | None = None
    recovery_deadline: float | None = field(default=None, init=False)
    refusal: ProxyResponseError | None = field(default=None, init=False)
    failed_selections: int = field(default=0, init=False)

    async def select(
        self,
        service: _HTTPBridgeServiceProtocol,
        *,
        last_error: ProxyResponseError | None,
        **selection_kwargs: Any,
    ) -> AccountSelection:
        while True:
            self._raise_if_expired(service)
            deadline = self.request_deadline if self.recovery_deadline is None else self.recovery_deadline
            try:
                selection = await service._select_account_with_budget_for_stream(deadline, **selection_kwargs)
            except ProxyResponseError:
                self._raise_if_expired(service)
                raise
            accepted = False
            try:
                self._raise_if_expired(service)
                if selection.account is not None:
                    if (
                        self.require_preferred_account
                        and self.preferred_account_id is not None
                        and selection.account.id != self.preferred_account_id
                    ):
                        _record_same_account_takeover(
                            preferred_account_id=self.preferred_account_id,
                            selected_account_id=selection.account.id,
                        )
                        if self.continuity_owner:
                            refusal = _http_bridge_previous_response_owner_unavailable_error()
                            raise _HTTPBridgeRequiredOwnerReplayForbiddenError(refusal.status_code, refusal.payload)
                        raise ProxyResponseError(
                            503,
                            openai_error(
                                "preferred_account_unavailable",
                                "Preferred account is unavailable; retry later.",
                                error_type="server_error",
                            ),
                        )
                    accepted = True
                    return selection
            finally:
                if not accepted:
                    # No session owns a rejected selection. Finish its lease
                    # release before propagating even repeated cancellation.
                    cancellation = await _await_cleanup_deferring_cancellation(
                        service._load_balancer.release_account_lease(selection.lease), scheduler=scheduler_for(service)
                    )
                    if cancellation is not None:
                        raise cancellation
            _record_same_account_takeover(
                preferred_account_id=self.preferred_account_id,
                selected_account_id=None,
            )
            if last_error is not None:
                raise last_error
            if (
                selection.owner_failure_reason is OwnerFailureReason.LOCAL_CONTENTION
                and self.response_create_attempt_count == 0
            ):
                # Local admission keeps its established outer request-deadline
                # wait, including follow-up creation; it is not provider recovery.
                status_code, error_payload = selection_failure_response(selection)
                raise ProxyResponseError(status_code, error_payload)
            if (
                self.require_preferred_account
                and self.preferred_account_id is not None
                and self.continuity_owner
                and selection.error_code in (CONTINUITY_OWNER_UNAVAILABLE, "hard_affinity_saturated")
            ):
                refusal = _http_bridge_previous_response_owner_unavailable_error()
            else:
                status_code, error_payload = selection_failure_response(selection)
                refusal = ProxyResponseError(status_code, error_payload)
            if not self.require_preferred_account or self.preferred_account_id is None:
                raise refusal
            can_wait = (
                self.allow_recovery
                and self.response_create_attempt_count == 0
                and selection.owner_failure_reason.temporary
                and self.preferred_account_id not in self.excluded_account_ids
            )
            now = clock_for(service).monotonic()
            if can_wait and self.recovery_deadline is None:
                self.recovery_deadline = min(self.request_deadline, now + 30.0)
            # The outer proof still decides replay on an unattempted refusal,
            # including expiry. The typed refusal cannot start another window.
            refusal_type = (
                _HTTPBridgeRequiredOwnerReplayForbiddenError
                if self.response_create_attempt_count > 0
                else _HTTPBridgeRequiredOwnerSelectionError
            )
            self.refusal = refusal_type(refusal.status_code, refusal.payload)
            if can_wait:
                assert self.recovery_deadline is not None
                remaining = self.recovery_deadline - now
                if remaining > 0:
                    if self.creation is not None:
                        self.creation.mark_recovering()
                    self.failed_selections += 1
                    delay = 2.0 if self.failed_selections < 3 else 8.0
                    await scheduler_for(service).sleep(min(delay, remaining))
                    continue
            raise self.refusal

    def _raise_if_expired(self, service: _HTTPBridgeServiceProtocol) -> None:
        if self.recovery_deadline is not None and clock_for(service).monotonic() >= self.recovery_deadline:
            assert self.refusal is not None
            raise self.refusal
