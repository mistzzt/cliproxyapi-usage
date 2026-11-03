"""Browser tests for the usage page's data loading: one request per panel."""

from collections import Counter
from datetime import date, timedelta

from conftest import ApiCall, ApiLog, wait_until
from playwright.sync_api import Locator, Page, Route, expect

PANELS = Counter(
    [
        ("summary", None),
        ("breakdown", "person"),
        ("breakdown", "model"),
        ("matrix", None),
    ]
)
_TODAY = date.today()
CUSTOM = (
    f"range=custom&from={_TODAY - timedelta(days=20)}T00:00"
    f"&to={_TODAY - timedelta(days=5)}T00:00"
)


def requests(calls: list[ApiCall]) -> Counter[tuple[str, str | None]]:
    return Counter((c.endpoint, c.params.get("dim")) for c in calls)


def windows(calls: list[ApiCall]) -> set[tuple[str | None, str]]:
    return {c.window for c in calls if c.endpoint != "options"}


def panel(page: Page, heading: str) -> Locator:
    return page.locator("section").filter(has=page.get_by_role("heading", name=heading))


def people(page: Page) -> Locator:
    return panel(page, "People by spend")


def heatmap(page: Page) -> Locator:
    return panel(page, "Who spends what on which model")


def kpis(page: Page) -> Locator:
    return page.locator("div[aria-busy]").filter(has_text="Avg latency")


def tab_panel(page: Page) -> Locator:
    return page.locator("section").filter(has=page.get_by_role("tablist"))


def open_usage(page: Page, api: ApiLog, query: str = "range=30d") -> list[ApiCall]:
    page.goto(f"/usage?{query}")
    expect(people(page).locator("tbody tr").first).to_be_visible()
    return api.settle()


def pick_range(page: Page, current: str, target: str) -> None:
    page.get_by_role("button", name=f"{current} ▾").click()
    page.get_by_role("button", name=target, exact=True).click()


def test_range_change_fetches_each_panel_once(page: Page, api: ApiLog) -> None:
    first = open_usage(page, api)
    assert requests(first) == PANELS + Counter([("options", None)])

    pick_range(page, "Last 30 days", "Last 7 days")
    calls = api.settle()
    assert requests(calls) == PANELS
    assert len(windows(calls)) == 1
    assert windows(calls).isdisjoint(windows(first))


def test_refresh_refetches_shown_panels_and_options(page: Page, api: ApiLog) -> None:
    refresh = page.get_by_role("button", name="Refresh")
    [(_, before)] = windows(open_usage(page, api))
    refresh.click()
    calls = api.settle()
    assert requests(calls) == PANELS + Counter([("options", None)])
    [(_, after)] = windows(calls)
    assert after > before

    fixed = windows(open_usage(page, api, CUSTOM))
    refresh.click()
    calls = api.settle()
    assert requests(calls) == PANELS + Counter([("options", None)])
    assert windows(calls) == fixed


def test_revisited_range_gets_a_fresh_window(page: Page, api: ApiLog) -> None:
    [(_, first_end)] = windows(open_usage(page, api))
    pick_range(page, "Last 30 days", "Last 7 days")
    api.settle()
    pick_range(page, "Last 7 days", "Last 30 days")
    calls = api.settle()
    assert requests(calls) == PANELS
    [(_, second_end)] = windows(calls)
    assert second_end > first_end


def test_drawer_requests_share_the_page_window(page: Page, api: ApiLog) -> None:
    page_calls = open_usage(page, api)
    people(page).locator("tbody tr").first.click()
    expect(page.get_by_role("complementary", name="Person details")).to_be_visible()
    drawer_calls = api.settle()
    assert requests(drawer_calls) == Counter(
        [("summary", None), ("breakdown", "model"), ("breakdown", "credential")]
    )
    assert all("people" in c.params for c in drawer_calls)
    assert windows(drawer_calls) == windows(page_calls)

    # A fresh load with the drawer open resolves one window for everything.
    page.reload()
    expect(page.get_by_role("complementary", name="Person details")).to_be_visible()
    calls = api.settle()
    assert requests(calls) == PANELS + requests(drawer_calls) + Counter(
        [("options", None)]
    )
    assert len(windows(calls)) == 1


def test_tab_switch_fetches_only_unseen_tabs(page: Page, api: ApiLog) -> None:
    open_usage(page, api)
    page.get_by_role("tab", name="Credentials").click()
    expect(tab_panel(page).locator("tbody tr").first).to_be_visible()
    assert requests(api.settle()) == Counter([("breakdown", "credential")])
    page.get_by_role("tab", name="Models").click()
    expect(tab_panel(page).locator("tbody tr").first).to_be_visible()
    assert api.settle() == []


def test_failed_panel_shows_its_failure_only(page: Page, api: ApiLog) -> None:
    page.route(
        "**/api/usage/matrix?*",
        lambda route: route.fulfill(status=500, body='{"detail": "boom"}'),
    )
    page.goto("/usage?range=30d")
    expect(heatmap(page)).to_contain_text("Failed to load.")
    expect(page.get_by_text("Heatmap: boom")).to_be_visible()
    expect(people(page).locator("tbody tr").first).to_be_visible()
    expect(tab_panel(page).locator("tbody tr").first).to_be_visible()
    expect(kpis(page)).to_contain_text("Requests")


def test_slow_panels_keep_previous_rows_until_their_response(
    page: Page, api: ApiLog
) -> None:
    open_usage(page, api)
    kpi_text = kpis(page).text_content() or ""
    held: list[Route] = []
    page.route("**/api/usage/**", lambda route: held.append(route))
    pick_range(page, "Last 30 days", "Last 7 days")
    wait_until(page, lambda: len(held) == len(PANELS))

    tables = [people(page), heatmap(page), tab_panel(page)]
    shown = [*tables, kpis(page)]
    for p in shown:
        expect(p).to_have_attribute("aria-busy", "true")
    for p in tables:
        expect(p.locator("tbody tr").first).to_be_visible()
    expect(kpis(page)).to_have_text(kpi_text)

    [person] = [r for r in held if "dim=person" in r.request.url]
    person.continue_()
    expect(people(page)).to_have_attribute("aria-busy", "false")
    for p in shown[1:]:
        expect(p).to_have_attribute("aria-busy", "true")

    for r in held:
        if r is not person:
            r.continue_()
    for p in shown:
        expect(p).to_have_attribute("aria-busy", "false")
