from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[2] / "app"

# Lifecycle notes are informational: only their own module, the schema and the
# composition roots (DI, router registration, CLI) may refer to them. Routing,
# selection, sticky ownership and the proxy must never read them.
_ALLOWED = {
    "cli.py",
    "dependencies.py",
    "main.py",
    "db/models.py",
    "db/alembic/versions/20261005_000000_add_account_lifecycle_preferences.py",
}


def test_only_the_lifecycle_module_and_composition_roots_refer_to_lifecycle_notes() -> None:
    referencing = {
        path.relative_to(_APP).as_posix()
        for path in _APP.rglob("*.py")
        if "modules/account_lifecycle/" not in path.as_posix()
        and ("account_lifecycle" in (text := path.read_text(encoding="utf-8")) or "AccountLifecycle" in text)
    }

    assert referencing == _ALLOWED
