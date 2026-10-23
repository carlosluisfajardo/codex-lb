"""Names and egress isolation for credentials owned by the dashboard."""

from typing import Final

DASHBOARD_SESSION_COOKIE: Final = "codex_lb_dashboard_session"
STEP_UP_COOKIE: Final = "codex_lb_step_up"
_LOCAL_AUTH_COOKIES = frozenset({DASHBOARD_SESSION_COOKIE, STEP_UP_COOKIE})


def without_dashboard_auth_cookies(value: str) -> str:
    return "; ".join(
        part.strip()
        for part in value.split(";")
        if part.strip() and part.partition("=")[0].strip() not in _LOCAL_AUTH_COOKIES
    )
