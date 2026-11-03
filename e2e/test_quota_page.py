"""Browser test for the Limits page: quota loading and manual reset, faked endpoints."""

from collections import Counter

from playwright.sync_api import Page, Route, expect

ACCOUNT = {"provider": "codex", "auth_name": "e2e.json", "display_name": "E2E account"}


class FakeQuota:
    """Serves the quota endpoints for one account whose manual resets count down."""

    def __init__(self, resets: int) -> None:
        self.resets = resets
        self.calls: Counter[str] = Counter()

    def accounts(self, route: Route) -> None:
        self.calls["accounts"] += 1
        route.fulfill(json={"accounts": [ACCOUNT]})

    def quota(self, route: Route) -> None:
        if route.request.method == "POST":
            self.calls["reset"] += 1
            self.resets -= 1
            route.fulfill(status=204)
            return
        self.calls["quota"] += 1
        route.fulfill(
            json={
                "quota": {
                    "provider": "codex",
                    "auth_name": "e2e.json",
                    "plan_type": "pro",
                    "windows": [],
                    "manual_resets": {
                        "available_count": self.resets,
                        "credits": [],
                        "credits_error": None,
                    },
                    "extra": {},
                },
                "error": None,
                "fetched_at": "2026-01-01T00:00:00Z",
                "stale_at": "2026-01-01T00:05:00Z",
            }
        )


def test_reset_refetches_quota_and_each_visit_reloads(page: Page) -> None:
    fake = FakeQuota(resets=2)
    page.route("**/api/quota/accounts", fake.accounts)
    page.route("**/api/quota/codex/e2e.json**", fake.quota)
    count = page.get_by_test_id("manual-reset-count")

    page.goto("/limits")
    expect(page.get_by_text("E2E account")).to_be_visible()
    expect(count).to_have_text("2")
    assert fake.calls == Counter(accounts=1, quota=1)

    page.get_by_role("button", name="Reset quota").click()
    page.get_by_role("button", name="Consume reset").click()
    expect(page.get_by_text("Manual reset consumed.")).to_be_visible()
    expect(count).to_have_text("1")
    assert fake.calls == Counter(accounts=1, quota=2, reset=1)

    # Leaving and coming back fetches the quotas again.
    page.get_by_role("link", name="Usage").click()
    page.get_by_role("link", name="Limits").click()
    page.wait_for_function("() => location.pathname.endsWith('/limits')")
    for _ in range(40):
        if fake.calls["quota"] == 3:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(300)
    assert fake.calls == Counter(accounts=2, quota=3, reset=1)
    expect(count).to_have_text("1")
