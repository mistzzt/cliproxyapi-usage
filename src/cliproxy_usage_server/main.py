"""FastAPI app factory, lifespan, and uvicorn entry point."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from cliproxy_usage_server.config import ServerConfig, load_config
from cliproxy_usage_server.pricing import ModelPricing, fetch_pricing
from cliproxy_usage_server.quota.client import CliProxyClient
from cliproxy_usage_server.quota.providers import PROVIDERS
from cliproxy_usage_server.quota.service import QuotaService
from cliproxy_usage_server.routes.pricing import build_router as build_pricing_router
from cliproxy_usage_server.routes.quota import build_router as build_quota_router
from cliproxy_usage_server.routes.usage import build_router as build_usage_router

_log = logging.getLogger(__name__)

_SPA_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _resolve_cache_path(config: ServerConfig) -> Path:
    if config.pricing_cache is not None:
        return config.pricing_cache
    return config.db_path.parent / "pricing.json"


async def _refresh_loop(app: FastAPI, config: ServerConfig) -> None:
    """Background task: refresh pricing every pricing_ttl_seconds."""
    while True:
        try:
            await asyncio.sleep(config.pricing_ttl_seconds)
        except asyncio.CancelledError:
            return
        try:
            app.state.pricing = await asyncio.to_thread(
                fetch_pricing,
                url=config.pricing_url,
                cache_path=_resolve_cache_path(config),
                ttl_seconds=config.pricing_ttl_seconds,
            )
        except Exception as exc:
            _log.warning("Failed to refresh pricing: %s", exc)


def _default_pricing_provider() -> dict[str, ModelPricing]:
    """Placeholder; replaced by monkeypatch in tests that call run()."""
    return {}  # pragma: no cover


def _default_quota_service_factory(config: ServerConfig) -> QuotaService:
    """Production factory: builds a real CliProxyClient + QuotaService."""
    assert config.cliproxy_base_url is not None  # guarded by caller
    assert config.cliproxy_management_key is not None  # guarded by caller
    client = CliProxyClient(config.cliproxy_base_url, config.cliproxy_management_key)
    return QuotaService(client, PROVIDERS, success_ttl=config.quota_cache_ttl_seconds)


# ---------------------------------------------------------------------------
# Module-level shared routers
# ---------------------------------------------------------------------------

_api = APIRouter()


@_api.get("/health-check")
def _health_check() -> dict[str, bool]:
    return {"ok": True}


_QUOTA_DISABLED_DETAIL = (
    "quota disabled: CLIPROXY_BASE_URL and CLIPROXY_MANAGEMENT_KEY required"
)

_quota_disabled = APIRouter()


@_quota_disabled.get("/quota/{path:path}")
def _quota_fallback(path: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": _QUOTA_DISABLED_DETAIL},
    )


def _prefixed(base_path: str, path: str) -> str:
    if base_path == "/":
        return path
    return f"{base_path}{path}"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    config: ServerConfig,
    *,
    pricing_provider: Callable[[], Mapping[str, ModelPricing]] | None = None,
    quota_service_factory: Callable[[ServerConfig], QuotaService] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    config:
        Runtime configuration.
    pricing_provider:
        If supplied, the lifespan loads pricing from this callable and skips
        the background refresh loop.  Intended for tests.
    quota_service_factory:
        If supplied, this factory is called (instead of the production one) to
        create the QuotaService.  Intended for tests.  When ``None`` the
        production factory is used when both ``CLIPROXY_*`` env vars are set.
    """
    quota_configured = (
        config.cliproxy_base_url is not None
        and config.cliproxy_management_key is not None
    )

    # Build the quota service eagerly so we can include its router before the
    # app starts.  Lifespan only handles cleanup (aclose).
    service: QuotaService | None = None
    if quota_configured:
        factory = quota_service_factory or _default_quota_service_factory
        service = factory(config)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if pricing_provider is not None:
            app.state.pricing = dict(pricing_provider())
            try:
                yield
            finally:
                if service is not None:
                    await service.aclose()
            return

        # Production path — fetch on startup (blocking OK here), then refresh.
        app.state.pricing = fetch_pricing(
            url=config.pricing_url,
            cache_path=_resolve_cache_path(config),
            ttl_seconds=config.pricing_ttl_seconds,
        )
        refresh_task = asyncio.create_task(_refresh_loop(app, config))
        try:
            yield
        finally:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task
            if service is not None:
                await service.aclose()

    api_prefix = _prefixed(config.base_path, "/api")

    app = FastAPI(lifespan=lifespan)
    app.include_router(_api, prefix=api_prefix)
    app.include_router(build_usage_router(config.db_path), prefix=api_prefix)
    app.include_router(build_pricing_router(), prefix=api_prefix)

    @app.get(_prefixed(config.base_path, "/usage-config.js"), include_in_schema=False)
    async def _runtime_config() -> Response:
        payload = json.dumps(
            {
                "basePath": config.base_path,
                "apiBase": api_prefix,
            },
            separators=(",", ":"),
        )
        return Response(
            f"window.__CLIPROXY_USAGE_CONFIG__={payload};\n",
            media_type="application/javascript",
        )

    if service is not None:
        app.state.quota_service = service
        app.include_router(build_quota_router(service), prefix=api_prefix)
    else:
        app.include_router(_quota_disabled, prefix=api_prefix)

    if _SPA_DIR.is_dir():
        # Serve hashed assets directly, then fall back to index.html for any
        # non-API path so client-side routes (e.g. /quota) resolve.
        app.mount(
            _prefixed(config.base_path, "/assets"),
            StaticFiles(directory=_SPA_DIR / "assets"),
            name="spa-assets",
        )
        _index_html = _SPA_DIR / "index.html"

        if config.base_path == "/":

            @app.get("/{full_path:path}", include_in_schema=False)
            async def _spa_fallback(full_path: str) -> FileResponse:
                del full_path  # unused; any non-API GET renders the SPA shell
                return FileResponse(_index_html)

        else:

            @app.get(config.base_path, include_in_schema=False)
            async def _spa_redirect() -> RedirectResponse:
                return RedirectResponse(f"{config.base_path}/")

            @app.get(f"{config.base_path}/{{full_path:path}}", include_in_schema=False)
            async def _prefixed_spa_fallback(full_path: str) -> FileResponse:
                del full_path  # unused; any base-path GET renders the SPA shell
                return FileResponse(_index_html)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Load config and start uvicorn."""
    config = load_config()
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
