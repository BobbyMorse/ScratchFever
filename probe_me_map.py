"""Probe: navigate to ME hunt on prod, click Map, capture state and any JS errors."""
from playwright.sync_api import sync_playwright
import time

URL = "https://scratchfever.app/"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()

    js_errors = []
    console_msgs = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))
    page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))

    print("Loading prod ...")
    page.goto(URL, wait_until="networkidle", timeout=30_000)
    time.sleep(2)

    # Open state dropdown and click ME
    print("Selecting ME ...")
    page.evaluate("selectHuntState('ME')")
    time.sleep(3)

    # Check state
    state = page.evaluate("""() => ({
        huntConsoleGen_display: document.getElementById('huntConsoleGen')?.style.display,
        genMapSection_display: document.getElementById('genMapSection')?.style.display,
        genViewMapBtn_display: document.getElementById('genViewMapBtn')?.style.display,
        currentHuntState: typeof currentHuntState !== 'undefined' ? currentHuntState : null,
        currentGenState: typeof currentGenState !== 'undefined' ? currentGenState : null,
        genMap_exists: !!window.genMap,
        retailerCount: typeof allGenRetailers !== 'undefined' && allGenRetailers.ME ? allGenRetailers.ME.length : 0,
        firstRetailerGeo: typeof allGenRetailers !== 'undefined' && allGenRetailers.ME && allGenRetailers.ME[0] ? {lat: allGenRetailers.ME[0].latitude, lng: allGenRetailers.ME[0].longitude} : null,
    })""")
    print("After selecting ME:")
    for k, v in state.items():
        print(f"  {k}: {v}")

    # Click Map button
    print("\nClicking Map button ...")
    page.evaluate("toggleGenMap()")
    time.sleep(2)

    # Re-check state
    state2 = page.evaluate("""() => ({
        genMapSection_display: document.getElementById('genMapSection')?.style.display,
        genMapSection_offsetHeight: document.getElementById('genMapSection')?.offsetHeight,
        genMap_div_offsetHeight: document.getElementById('genMap')?.offsetHeight,
        genMap_inner_children: document.getElementById('genMap')?.children.length,
        genMapVisible: typeof genMapVisible !== 'undefined' ? genMapVisible : null,
        genMap_exists: !!window.genMap,
        markerCount: window._genInventoryLayer ? window._genInventoryLayer.getLayers().length : 0,
    })""")
    print("After clicking Map:")
    for k, v in state2.items():
        print(f"  {k}: {v}")

    page.screenshot(path="C:/Users/rober/AppData/Local/Temp/me_map_after.png", full_page=True)
    print(f"\nScreenshot: C:/Users/rober/AppData/Local/Temp/me_map_after.png")

    print(f"\nJS errors ({len(js_errors)}):")
    for e in js_errors: print(f"  {e}")
    print(f"\nConsole msgs ({len(console_msgs)}):")
    for m in console_msgs[-15:]: print(f"  {m}")

    browser.close()
