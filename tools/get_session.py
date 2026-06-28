"""Capture a TradeMe session by logging in as a human.

Run this on a machine with a normal display and your usual internet connection
(e.g. your laptop) — NOT on the headless VPS, because TradeMe shows a
bot-challenge (CAPTCHA) to automated/headless browsers.

    pip install playwright
    python -m playwright install chromium
    python -m tools.get_session

A real browser window opens. Log in to TradeMe by hand (including 2FA and any
"are you human" challenge). When the dashboard has loaded and you're logged in,
return to this terminal and press Enter. It writes ``trademe_session.json``.

Then either:
  * upload that file via the dashboard's  Account → Import session, or
  * copy it to the server as  data/storage_state.json
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

OUTPUT = "trademe_session.json"
LOGIN_URL = "https://www.trademe.co.nz/a/login"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(locale="en-NZ",
                                             timezone_id="Pacific/Auckland")
        page = await context.new_page()
        await page.goto(LOGIN_URL)

        print("\nA browser window has opened.")
        print("Log in to TradeMe there (email, password, 2FA, any challenge).")
        try:
            input("When you're fully logged in, press Enter here to save… ")
        except (EOFError, KeyboardInterrupt):
            pass

        await context.storage_state(path=OUTPUT)
        await browser.close()
        print(f"\nSaved {OUTPUT}")
        print("Import it via the dashboard (Account → Import session), or copy it "
              "to the server as data/storage_state.json")


if __name__ == "__main__":
    asyncio.run(main())
