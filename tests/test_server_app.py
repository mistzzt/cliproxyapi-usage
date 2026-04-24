"""Tests for cliproxy_usage_server.main — app factory and lifespan."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.schemas import QuotaAccount


def _make_config() -> ServerConfig:
    return ServerConfig(  # pyright: ignore[reportCallIssue]
        db_path=Path("/tmp/test-server-app.db"),
        host="127.0.0.1",
        port=8318,
    )


def _make_config_with_cliproxy() -> ServerConfig:
    return ServerConfig(  # pyright: ignore[reportCallIssue]
        db_path=Path("/tmp/test-server-app.db"),
        host="127.0.0.1",
        port=8318,
        cliproxy_base_url="https://example.com",
        cliproxy_management_key="test-key",
    )


def _make_prefixed_config() -> ServerConfig:
    return ServerConfig(  # pyright: ignore[reportCallIssue]
        db_path=Path("/tmp/test-server-app.db"),
        host="127.0.0.1",
        port=8318,
        base_path="/api-usage",
    )


# ---------------------------------------------------------------------------
# Stub QuotaService for injection tests
# ---------------------------------------------------------------------------


class _StubQuotaService:
    """Minimal stub for quota wiring tests."""

    def __init__(self) -> None:
        self.closed = False
        self.list_accounts_calls = 0

    async def list_accounts(self) -> list[QuotaAccount]:
        self.list_accounts_calls += 1
        return []

    async def aclose(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------


def test_create_app_health_check() -> None:
    cfg = _make_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        resp = client.get("/api/health-check")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_create_app_health_check_uses_configured_base_path() -> None:
    cfg = _make_prefixed_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        resp = client.get("/api-usage/api/health-check")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_create_app_prefixed_mode_does_not_claim_root_api() -> None:
    cfg = _make_prefixed_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        resp = client.get("/api/health-check")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SPA not mounted when _spa dir is missing
# ---------------------------------------------------------------------------


def test_create_app_spa_not_mounted_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nonexistent = tmp_path / "_spa_does_not_exist"
    import cliproxy_usage_server.main as main_mod

    monkeypatch.setattr(main_mod, "_SPA_DIR", nonexistent)

    cfg = _make_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/")
    assert resp.status_code == 404


def test_runtime_config_served_in_root_mode() -> None:
    cfg = _make_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        resp = client.get("/usage-config.js")
    assert resp.status_code == 200
    assert "application/javascript" in resp.headers["content-type"]
    assert 'basePath":"/"' in resp.text
    assert 'apiBase":"/api"' in resp.text


def test_runtime_config_served_under_configured_base_path() -> None:
    cfg = _make_prefixed_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        resp = client.get("/api-usage/usage-config.js")
    assert resp.status_code == 200
    assert 'basePath":"/api-usage"' in resp.text
    assert 'apiBase":"/api-usage/api"' in resp.text


def test_prefixed_spa_and_assets_use_configured_base_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spa_dir = tmp_path / "dist"
    assets_dir = spa_dir / "assets"
    assets_dir.mkdir(parents=True)
    (spa_dir / "index.html").write_text("<html>usage app</html>", encoding="utf-8")
    (assets_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")

    import cliproxy_usage_server.main as main_mod

    monkeypatch.setattr(main_mod, "_SPA_DIR", spa_dir)

    cfg = _make_prefixed_config()
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app, follow_redirects=False) as client:
        redirect = client.get("/api-usage")
        shell = client.get("/api-usage/quota")
        asset = client.get("/api-usage/assets/app.js")
        unprefixed_shell = client.get("/quota")
        unprefixed_asset = client.get("/assets/app.js")

    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/api-usage/"
    assert shell.status_code == 200
    assert shell.text == "<html>usage app</html>"
    assert asset.status_code == 200
    assert asset.text == "console.log('ok')"
    assert unprefixed_shell.status_code == 404
    assert unprefixed_asset.status_code == 404


# ---------------------------------------------------------------------------
# run() passes host/port from config to uvicorn
# ---------------------------------------------------------------------------


def test_run_invokes_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "USAGE_DB_PATH",
        "USAGE_SERVER_HOST",
        "USAGE_SERVER_PORT",
        "USAGE_PRICING_CACHE",
        "USAGE_PRICING_TTL_SECONDS",
        "USAGE_PRICING_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("USAGE_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("USAGE_SERVER_PORT", "9999")

    mock_uvicorn_run = MagicMock()
    import cliproxy_usage_server.main as main_mod

    monkeypatch.setattr(main_mod.uvicorn, "run", mock_uvicorn_run)

    monkeypatch.setattr(main_mod, "_default_pricing_provider", lambda: {})

    from cliproxy_usage_server.main import run

    run()

    mock_uvicorn_run.assert_called_once()
    _, kwargs = mock_uvicorn_run.call_args
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9999


# ---------------------------------------------------------------------------
# Quota wiring tests
# ---------------------------------------------------------------------------


def test_quota_routes_mounted_when_configured() -> None:
    """With CLIPROXY_* env vars set and a stub factory, /api/quota/accounts → 200."""
    stub = _StubQuotaService()
    cfg = _make_config_with_cliproxy()
    app = create_app(
        cfg,
        pricing_provider=lambda: {},
        quota_service_factory=lambda _config: stub,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        resp = client.get("/api/quota/accounts")
    assert resp.status_code == 200
    assert "accounts" in resp.json()
    assert stub.list_accounts_calls == 1


def test_quota_routes_return_503_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without CLIPROXY_* env vars, /api/quota/* returns 503 with descriptive detail."""
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)

    cfg = ServerConfig(  # pyright: ignore[reportCallIssue]
        db_path=Path("/tmp/test-server-app.db"),
        host="127.0.0.1",
        port=8318,
        # cliproxy_base_url and cliproxy_management_key left as None (defaults)
    )
    app = create_app(cfg, pricing_provider=lambda: {})

    _EXPECTED_DETAIL = (
        "quota disabled: CLIPROXY_BASE_URL and CLIPROXY_MANAGEMENT_KEY required"
    )

    with TestClient(app) as client:
        resp1 = client.get("/api/quota/accounts")
        assert resp1.status_code == 503
        assert resp1.json() == {"detail": _EXPECTED_DETAIL}

        resp2 = client.get("/api/quota/claude/anything")
        assert resp2.status_code == 503
        assert resp2.json() == {"detail": _EXPECTED_DETAIL}


def test_quota_disabled_route_uses_configured_base_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)

    cfg = _make_prefixed_config()
    app = create_app(cfg, pricing_provider=lambda: {})

    _EXPECTED_DETAIL = (
        "quota disabled: CLIPROXY_BASE_URL and CLIPROXY_MANAGEMENT_KEY required"
    )

    with TestClient(app) as client:
        resp = client.get("/api-usage/api/quota/accounts")
    assert resp.status_code == 503
    assert resp.json() == {"detail": _EXPECTED_DETAIL}


def test_quota_service_is_closed_on_shutdown() -> None:
    """After TestClient exits, stub service.aclose() must have been called."""
    stub = _StubQuotaService()
    cfg = _make_config_with_cliproxy()
    app = create_app(
        cfg,
        pricing_provider=lambda: {},
        quota_service_factory=lambda _config: stub,  # type: ignore[arg-type]
    )
    with TestClient(app):
        assert not stub.closed  # still running
    assert stub.closed  # shutdown triggered aclose()
