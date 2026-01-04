import asyncio
import time
from playwright.async_api import async_playwright

WIPO_URL = "https://patentscope.wipo.int/search/en/detail.jsf?docId=WO2019028689"

async def run_diagnostic():
    print("🧪 WIPO PLAYWRIGHT DIAGNOSTIC STARTED")
    print("=" * 60)
    print(f"URL: {WIPO_URL}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )

        page = await context.new_page()

        # =========================
        # TIMING TEST (AÇÃO 3)
        # =========================
        start = time.time()
        await page.goto(WIPO_URL)
        after_goto = time.time()

        await page.wait_for_load_state("networkidle")
        after_network = time.time()

        print(f"⏱ goto(): {after_goto - start:.2f}s")
        print(f"⏱ networkidle(): {after_network - after_goto:.2f}s")

        # =========================
        # SCREENSHOT (AÇÃO 2)
        # =========================
        await page.screenshot(
            path="wo_screenshot.png",
            full_page=True
        )
        print("📸 Screenshot saved: wo_screenshot.png")

        # =========================
        # HTML EXTRACTION (AÇÃO 2 + 4)
        # =========================
        html = await page.content()
        html_length = len(html)
        print(f"📄 HTML length: {html_length}")

        with open("wo_playwright.html", "w", encoding="utf-8") as f:
            f.write(html)

        print("💾 HTML saved: wo_playwright.html")

        await browser.close()

        print("=" * 60)
        print("✅ DIAGNOSTIC COMPLETE")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
