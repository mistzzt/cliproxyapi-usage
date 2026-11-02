"""Browser test fixtures: a seeded usage server, a browser, and a request log."""

import os
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page, Request, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data = tmp_path_factory.mktemp("usage")
    subprocess.run(
        ["uv", "run", "python", "e2e/seed_db.py", str(data)], cwd=ROOT, check=True
    )
    port = _free_port()
    env = {
        **os.environ,
        "USAGE_DB_PATH": str(data / "usage.db"),
        "USAGE_SERVER_PORT": str(port),
        # Unreachable, so a stale pricing cache never reaches the network.
        "USAGE_PRICING_URL": "http://127.0.0.1:9/",
    }
    server = subprocess.Popen(["uv", "run", "cliproxy-usage-server"], cwd=ROOT, env=env)
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(f"{url}/api/usage/options", server)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


def _wait_until_up(url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"usage server exited with {server.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"usage server did not answer {url}")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@dataclass(frozen=True)
class ApiCall:
    """One usage API request: its endpoint (`summary`, ...) and query params."""

    endpoint: str
    params: dict[str, str]

    @property
    def window(self) -> tuple[str | None, str]:
        return self.params.get("start"), self.params["end"]


@dataclass
class ApiLog:
    """Usage API requests a page sent, and how many are still in flight."""

    page: Page
    calls: list[ApiCall] = field(default_factory=list)
    pending: set[Request] = field(default_factory=set)

    def record(self, request: Request) -> None:
        url = urlparse(request.url)
        if not url.path.startswith("/api/usage/"):
            return
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append(ApiCall(url.path.removeprefix("/api/usage/"), params))
        self.pending.add(request)

    def done(self, request: Request) -> None:
        self.pending.discard(request)

    def settle(self, quiet_ms: int = 500) -> list[ApiCall]:
        """Waits until nothing was in flight for `quiet_ms`; pops the logged calls."""
        quiet_since = time.monotonic()
        while (time.monotonic() - quiet_since) * 1000 < quiet_ms:
            self.page.wait_for_timeout(50)
            if self.pending:
                quiet_since = time.monotonic()
        calls, self.calls = self.calls, []
        return calls


@pytest.fixture
def page(browser: Browser, base_url: str) -> Iterator[Page]:
    context = browser.new_context(base_url=base_url, timezone_id="America/Chicago")
    yield context.new_page()
    context.close()


@pytest.fixture
def api(page: Page) -> ApiLog:
    log = ApiLog(page)
    page.on("request", log.record)
    page.on("requestfinished", log.done)
    page.on("requestfailed", log.done)
    return log
