from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal, Permission
from app.core.auth.dependencies import (
    require_dashboard_permission,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardConflictError, DashboardNotFoundError
from app.dependencies import AccountLifecycleContext, get_account_lifecycle_context
from app.modules.account_lifecycle.schemas import AccountLifecycleResponse, AccountLifecycleUpdateRequest
from app.modules.account_lifecycle.service import AccountLifecycleConflictError

router = APIRouter(
    prefix="/api/accounts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/{account_id}/lifecycle", response_model=AccountLifecycleResponse)
async def get_account_lifecycle(
    account_id: str,
    _read_access=Depends(require_dashboard_permission(Permission.ACCOUNTS_READ)),
    context: AccountLifecycleContext = Depends(get_account_lifecycle_context),
) -> AccountLifecycleResponse:
    result = await context.service.get(account_id)
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.put("/{account_id}/lifecycle", response_model=AccountLifecycleResponse)
async def update_account_lifecycle(
    request: Request,
    account_id: str,
    payload: AccountLifecycleUpdateRequest,
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.ACCOUNTS_WRITE)),
    context: AccountLifecycleContext = Depends(get_account_lifecycle_context),
) -> AccountLifecycleResponse:
    try:
        result = await context.service.update(account_id, payload)
    except AccountLifecycleConflictError as exc:
        raise DashboardConflictError(str(exc), code="account_lifecycle_conflict") from exc
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    AuditService.log_async(
        "account_lifecycle_updated",
        actor_ip=request.client.host if request.client else None,
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("account", account_id),
        details={"account_id": account_id, "revision": result.revision},
    )
    return result
