"""Exercise test bootstrap in fresh processes with inert store-access sentinels."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = r"""
import json
import os
from pathlib import Path
import runpy
import sys

repo, forbidden, scenario = sys.argv[1:]
sys.path.insert(0, repo)
forbidden = Path(forbidden)
accesses = []
watching = True

# These paths are dummy fixtures. Stop BEFORE any key/store I/O even when the
# bootstrap or its guard is deliberately broken for the negative controls.
def sentinel(event, args):
    if not watching:
        return
    if event == "socket.connect":
        raise AssertionError("unexpected network access in isolation probe")
    if event not in {"open", "os.mkdir", "os.remove", "os.rmdir", "sqlite3.connect"}:
        return
    target = args[0]
    if isinstance(target, (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(target)).absolute()
        linked_probe = scenario in {"symlink", "symlink_parent"} and "linked" in target.parts
        if target.is_relative_to(forbidden) or linked_probe:
            accesses.append(event)
            raise AssertionError("dummy store sentinel reached before isolation guard")

sys.addaudithook(sentinel)
result = {"accesses": accesses}
try:
    if scenario == "bootstrap_defaults":
        for name in ("CODEX_LB_DATA_DIR", "CODEX_LB_ENCRYPTION_KEY_FILE", "CODEX_LB_CONVERSATION_ARCHIVE_DIR"):
            del os.environ[name]
    elif scenario == "sqlite_override":
        os.environ["CODEX_LB_TEST_DATABASE_URL"] = f"sqlite+aiosqlite:///{forbidden / 'store.db'}"
    elif scenario == "postgres_override":
        os.environ["CODEX_LB_TEST_DATABASE_URL"] = "postgresql+asyncpg://fixture:fixture@example.invalid/fixture"
    elif scenario == "inherited_tmpdir":
        os.environ["TMPDIR"] = str(forbidden / "tmp")
    elif scenario == "cached_tempdir":
        import tempfile
        tempfile.tempdir = str(forbidden / "tmp")
    if scenario == "preloaded":
        from app.core.config.settings import get_settings
        get_settings()
    namespace = runpy.run_path(str(Path(repo) / "tests" / "conftest.py"))
    from app.core.config.settings import get_settings
    from app.core.crypto import TokenEncryptor, get_or_create_key
    settings = get_settings()
    if scenario in {"bootstrap", "bootstrap_defaults"}:
        encryptor = TokenEncryptor()
        assert encryptor.decrypt(encryptor.encrypt("fixture")) == "fixture"
        result.update(home=os.environ["HOME"], data=str(settings.data_dir),
                      key=str(settings.encryption_key_file),
                      source=sys.modules["app.core.config.settings"].__file__)
        result.update(home_mode=Path(os.environ["HOME"]).stat().st_mode & 0o777,
                      data_mode=settings.data_dir.stat().st_mode & 0o777)
    elif scenario == "explicit":
        get_or_create_key(forbidden / "explicit.key")
    elif scenario == "cached":
        settings.encryption_key_file = forbidden / "cached.key"
        TokenEncryptor()
    elif scenario == "environment":
        os.environ["CODEX_LB_ENCRYPTION_KEY_FILE"] = str(forbidden / "environment.key")
        get_settings.cache_clear()
        TokenEncryptor()
    elif scenario == "symlink":
        link = settings.data_dir / "linked"
        link.symlink_to(forbidden, target_is_directory=True)
        get_or_create_key(link / "linked.key")
    elif scenario == "symlink_parent":
        link = settings.data_dir / "linked"
        link.symlink_to(forbidden / "child", target_is_directory=True)
        get_or_create_key(link / ".." / "parent.key")
    elif scenario == "postgres_override":
        from app.db.session import engine
        assert str(engine.url) == "postgresql+asyncpg://fixture:***@example.invalid/fixture"
    elif scenario == "sqlite_override":
        from app.db.session import engine
        # A connection would attempt store access; the audit sentinel keeps
        # this inert when the bootstrap's SQLite guard is mutation-disabled.
        import asyncio
        async def connect():
            async with engine.connect():
                pass
        asyncio.run(connect())
    elif scenario == "private_override":
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.key"
            first = get_or_create_key(path)
            assert get_or_create_key(path) == first
            encryptor = TokenEncryptor(key_file=path)
            assert encryptor.decrypt(encryptor.encrypt("fixture")) == "fixture"
    result["outcome"] = "ok"
except Exception as exc:
    result.update(outcome=type(exc).__name__, message=str(exc))
result["database_imported"] = "app.db.session" in sys.modules
print("ISOLATION_PROBE=" + json.dumps(result))
# The result is final. Let the bootstrap's exit cleanup unlink, never follow,
# the dummy links so no session is left in the OS temporary root.
watching = False
"""


def _probe(tmp_path: Path, scenario: str) -> dict[str, object]:
    forbidden = tmp_path / "dummy-store"
    environ = {name: value for name, value in os.environ.items() if not name.upper().startswith("CODEX_LB_")}
    environ.update(
        HOME=str(forbidden / "home"),
        CODEX_LB_DATA_DIR=str(forbidden / "data"),
        CODEX_LB_ENCRYPTION_KEY_FILE=str(forbidden / "data" / "encryption.key"),
        CODEX_LB_CONVERSATION_ARCHIVE_DIR=str(forbidden / "archive"),
        CODEX_LB_ENV_FILE=str(tmp_path / "absent.env"),
        codex_lb_database_url=f"sqlite+aiosqlite:///{forbidden / 'lowercase.db'}",
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PROBE, str(_REPO_ROOT), str(forbidden), scenario],
        cwd=tmp_path,
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    line = next(line for line in completed.stdout.splitlines() if line.startswith("ISOLATION_PROBE="))
    return json.loads(line.removeprefix("ISOLATION_PROBE="))


def test_bootstrap_is_private_before_application_imports(tmp_path: Path) -> None:
    first = _probe(tmp_path, "bootstrap")
    second = _probe(tmp_path, "bootstrap")
    for result in (first, second):
        assert result["accesses"] == [], result
        assert not Path(str(result.get("home", tmp_path / "dummy-store"))).is_relative_to(tmp_path / "dummy-store"), (
            result
        )
        assert result["outcome"] == "ok", result
        assert result["source"] == str(_REPO_ROOT / "app" / "core" / "config" / "settings.py")
        assert result["home_mode"] == 0o700
        assert result["data_mode"] == 0o700
        assert Path(str(result["key"])).is_relative_to(Path(str(result["data"])))
    assert first["home"] != second["home"]
    assert first["data"] != second["data"]


def test_bootstrap_refuses_preloaded_application_settings(tmp_path: Path) -> None:
    result = _probe(tmp_path, "preloaded")
    assert result["accesses"] == [], result
    assert result["database_imported"] is False, result
    assert result["outcome"] == "RuntimeError", result
    assert "before application imports" in str(result["message"])


@pytest.mark.parametrize("scenario", ["inherited_tmpdir", "cached_tempdir"])
def test_session_root_ignores_inherited_temporary_directory(tmp_path: Path, scenario: str) -> None:
    result = _probe(tmp_path, scenario)
    assert result["accesses"] == [], result
    assert result["outcome"] == "ok", result


@pytest.mark.parametrize(
    "scenario", ["explicit", "cached", "environment", "symlink", "symlink_parent", "sqlite_override"]
)
def test_key_access_refuses_paths_outside_private_session(tmp_path: Path, scenario: str) -> None:
    result = _probe(tmp_path, scenario)
    assert result["accesses"] == [], result
    assert result["outcome"] == "RuntimeError", result
    assert "outside the private test session" in str(result["message"])


def test_key_access_allows_private_temporary_override(tmp_path: Path) -> None:
    result = _probe(tmp_path, "private_override")
    assert result["accesses"] == [], result
    assert result["outcome"] == "ok", result


@pytest.mark.parametrize("scenario", ["bootstrap_defaults", "postgres_override"])
def test_bootstrap_keeps_safe_defaults_and_explicit_postgres_route(tmp_path: Path, scenario: str) -> None:
    result = _probe(tmp_path, scenario)
    assert result["accesses"] == [], result
    assert result["outcome"] == "ok", result
