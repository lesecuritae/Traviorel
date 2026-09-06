"""Live browser regression; run against the started Compose app with Playwright installed."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = os.getenv("TRAVIOREL_TEST_URL", "http://127.0.0.1:8791")
ARTIFACTS = Path(os.getenv("TRAVIOREL_BROWSER_ARTIFACTS", ".browser-artifacts"))


async def wait_selected(page, *fields: str, timeout_ms: int = 30_000) -> None:
    for _ in range(timeout_ms // 100):
        if all([await page.locator(f"#{field}").get_attribute("data-selected") == "true" for field in fields]):
            return
        await page.wait_for_timeout(100)
    raise AssertionError(f"Stationsauswahl nicht bestätigt: {', '.join(fields)}")


async def check_viewport(browser, width: int, height: int) -> None:
    context = await browser.new_context(viewport={"width": width, "height": height}, is_mobile=width <= 620, has_touch=width <= 620)
    page = await context.new_page()
    errors = []
    page.on("console", lambda message: errors.append(f"console:{message.type}:{message.text}") if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"page:{error}"))
    await page.goto(BASE_URL, wait_until="networkidle")
    segment_labels = await page.evaluate("""() => ({
      flix: readableLegTitle({provider:'flixbus', segment_provider:'FlixTrain', type:'train'}),
      regional: readableLegTitle({line:'RE13', operator:'DB Regio AG Südost', mode:'train'}),
      bus: readableLegTitle({type:'bus'}),
      station: readablePlace({station:'Leipzig Messe'}),
      technical: readablePlace({station:'68c5db74-0034-41b2-b3ae-6947c837f77b'})
    })""")
    assert segment_labels == {
        "flix":"FlixTrain", "regional":"RE13 · DB Regio AG Südost",
        "bus":"Bus", "station":"Leipzig Messe", "technical":"",
    }
    assert "[object Object]" not in str(segment_labels)
    assert await page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth"), f"horizontale Überbreite bei {width}x{height}"
    if width <= 620:
        assert await page.locator(".topbar").evaluate("el => getComputedStyle(el).position") == "static"
    await page.locator("#departureCalendarToggle").click()
    assert await page.locator("#calendarPanel").is_visible()
    assert await page.locator("#calendarDays button:not([disabled])").count() >= 20
    month = await page.locator("#calendarMonth").text_content()
    await page.locator("#calendarNext").click()
    assert await page.locator("#calendarMonth").text_content() != month
    selectable = page.locator("#calendarDays button:not([disabled])").first
    chosen = await selectable.get_attribute("data-calendar-date")
    await selectable.click()
    assert await page.locator("#departureDate").input_value() == chosen
    assert not await page.locator("#calendarPanel").is_visible()
    await page.locator("#returnCalendarToggle").click()
    assert await page.locator("#calendarPanel").is_visible()
    return_choice = page.locator("#calendarDays button:not([disabled])").last
    chosen_return = await return_choice.get_attribute("data-calendar-date")
    await return_choice.click()
    assert await page.locator("#returnDate").input_value() == chosen_return
    await page.locator("#oneWayButton").click()
    assert await page.locator("#returnDate").is_disabled()
    assert await page.locator("#returnCalendarToggle").is_disabled()
    assert await page.locator("#returnDisabledHint").is_visible()
    await page.locator("#oneWayButton").click()
    assert await page.locator("#returnDate").is_enabled()
    assert await page.locator("#returnCalendarToggle").is_enabled()
    assert await page.locator("#returnDate").input_value()
    await page.locator("#priceCalendarButton").scroll_into_view_if_needed()
    assert await page.locator("#priceCalendarButton").is_visible()
    assert await page.locator("#splitTicket").is_visible()
    assert await page.locator("#splitTicket").is_checked()
    await page.locator("#splitTicket").uncheck()
    assert not await page.locator("#splitTicket").is_checked()
    await page.locator("#splitTicket").check()
    assert await page.locator("#includeTrain").is_checked()
    assert await page.locator("#includeBus").is_checked()
    await page.locator("#includeTrain").uncheck()
    assert await page.locator("#splitTicket").is_disabled()
    await page.locator("#includeTrain").check()
    assert await page.locator("#splitTicket").is_enabled()
    await page.locator("#origin").fill("München")
    await page.locator("#originSuggestions").wait_for(state="visible", timeout=30_000)
    assert await page.locator("#origin").get_attribute("data-selected") is None
    assert await page.locator("#originSuggestions button").count() > 1
    assert "München Hbf" in await page.locator("#originSuggestions button").first.inner_text()
    assert "Flughafen" not in " ".join(await page.locator("#originSuggestions button").all_inner_texts())
    await page.screenshot(path=ARTIFACTS / f"traviorel-location-choice-{width}x{height}.png", full_page=True)
    await page.locator("#origin").fill("")
    if width <= 620:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        assert await page.evaluate("window.scrollY > 0")
        assert await page.locator(".topbar").evaluate("el => getComputedStyle(el).position") == "static"
    await page.screenshot(path=ARTIFACTS / f"traviorel-{width}x{height}.png", full_page=True)
    assert not errors, errors
    await context.close()


async def check_flexible_calendar(browser, width: int, height: int) -> None:
    context = await browser.new_context(viewport={"width": width, "height": height}, is_mobile=width <= 620, has_touch=width <= 620)
    page = await context.new_page()

    async def station_route(route):
        query = route.request.url.casefold()
        station = ({"name":"Leipzig Hbf", "provider_id":"8010205"} if "leipzig" in query
                   else {"name":"Frankfurt(Main) Hbf", "provider_id":"8000105"})
        candidate = {**station, "provider":"db", "provider_ids":{"db":station["provider_id"]}, "label":station["name"]}
        await route.fulfill(json={"stations":[candidate], "requires_selection":False, "auto_selection":candidate})

    await page.route("**/api/stations*", station_route)
    await page.route("**/api/flix-stops*", lambda route: route.fulfill(json={"origin_stops":[], "destination_stops":[]}))
    await page.route("**/api/price-calendar", lambda route: route.fulfill(json={
        "status":"ok", "origin":"Leipzig Hbf", "destination":"Frankfurt(Main) Hbf",
        "calendar_days":3, "cheapest_date":"2026-10-28", "days":[
            {"date":"2026-10-27", "status":"available", "connection_count":4, "price":39.9, "currency":"EUR", "price_available":True, "cheapest":False},
            {"date":"2026-10-28", "status":"available", "connection_count":5, "price":29.9, "currency":"EUR", "price_available":True, "cheapest":True},
            {"date":"2026-10-29", "status":"available", "connection_count":3, "price":None, "price_available":False, "cheapest":False},
        ],
    }))
    await page.goto(BASE_URL)
    await page.locator("#origin").fill("Leipzig Hbf")
    await page.locator("#origin").press("Tab")
    await page.locator("#destination").fill("Frankfurt(Main) Hbf")
    await page.locator("#destination").press("Tab")
    await wait_selected(page, "origin", "destination")
    await page.locator("#priceCalendarPreset").select_option("3")
    await page.locator("#priceCalendarButton").click()
    await page.locator(".price-calendar-results").wait_for(state="visible")
    assert await page.locator(".price-calendar-day").count() == 3
    assert await page.locator(".price-calendar-day.cheapest").count() == 1
    assert "29,90" in await page.locator(".price-calendar-day.cheapest").inner_text()
    assert "Preis offen" in await page.locator(".price-calendar-day").nth(2).inner_text()
    assert await page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
    await page.screenshot(path=ARTIFACTS / f"traviorel-flexible-calendar-{width}x{height}.png", full_page=True)
    await context.close()


async def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        for viewport in ((390, 844), (412, 915), (1440, 1000), (1920, 1080)):
            await check_viewport(browser, *viewport)
        await check_flexible_calendar(browser, 1440, 1000)
        await check_flexible_calendar(browser, 390, 844)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.goto(BASE_URL)
        await page.locator("#origin").fill("Leipzig Hbf")
        await page.locator("#origin").wait_for()
        await wait_selected(page, "origin")
        await page.locator("#destination").fill("Frankfurt(Main) Hbf")
        await wait_selected(page, "destination")
        await page.locator("#departureDate").evaluate("(el) => el.value = '2026-10-27'")
        await page.locator("#departureAfter").fill("03:00")
        await page.locator("#oneWayButton").click()
        await page.locator("#searchButton").click()
        await page.locator("#searchProgress").wait_for(state="visible")
        assert await page.locator(".progress-step").count() == 6
        await page.locator("#results").wait_for(state="visible", timeout=150_000)
        await page.locator("#searchProgress").wait_for(state="hidden", timeout=5_000)
        assert await page.locator(".connection-card").count() > 0
        assert await page.locator("body").evaluate("el => el.scrollWidth <= el.clientWidth")
        await page.screenshot(path=ARTIFACTS / "traviorel-results-desktop.png", full_page=True)
        await browser.close()
    print("Browser UI, flexible Preise, Kalender, Loader und Desktop-/Mobil-Viewports: OK")


if __name__ == "__main__":
    asyncio.run(main())
