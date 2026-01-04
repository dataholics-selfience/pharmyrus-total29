import asyncio
import logging
import re
from typing import List, Dict

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wipo_v2")

WIPO_SEARCH_URL = "https://patentscope.wipo.int/search/en/result.jsf"
WIPO_DETAIL_URL = "https://patentscope.wipo.int/search/en/detail.jsf?docId={wo}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
}

# =========================
# SEARCH (HTTPX)
# =========================
async def search_wipo(query: str, limit: int = 10) -> List[str]:
    params = {
        "query": f"FP:({query})"
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=30) as client:
        r = await client.get(WIPO_SEARCH_URL, params=params)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")

    wos = []
    for a in soup.select("a[href*='docId=WO']"):
        match = re.search(r"docId=(WO\d+)", a["href"])
        if match:
            wos.append(match.group(1))

    unique = list(dict.fromkeys(wos))
    return unique[:limit]

# =========================
# PLAYWRIGHT DETAIL
# =========================
async def fetch_detail_html(wo_number: str) -> str:
    url = WIPO_DETAIL_URL.format(wo=wo_number)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"]
        )
        page = await context.new_page()

        await page.goto(url, wait_until="networkidle", timeout=60000)

        # DOM FINAL – SEM ISSO, NÃO EXTRAI
        await page.wait_for_selector("div.ps-patent", timeout=60000)

        html = await page.content()
        await browser.close()
        return html

# =========================
# PARSER
# =========================
def parse_biblio(html: str) -> Dict:
    soup = BeautifulSoup(html, "lxml")

    container = soup.select_one("div.ps-patent")
    if not container:
        raise ValueError("Patent container not found")

    def extract(label: str):
        el = container.find("span", string=re.compile(label, re.I))
        if not el:
            return ""
        value = el.find_parent("tr")
        return value.get_text(" ", strip=True) if value else ""

    return {
        "title": extract("Title"),
        "applicants": extract("Applicants"),
        "inventors": extract("Inventors"),
        "ipc_codes": extract("IPC"),
        "publication_date": extract("Publication Date"),
        "filing_date": extract("Filing Date")
    }

# =========================
# PIPELINE
# =========================
async def search_wipo_patents(
    query: str,
    max_results: int = 5
) -> List[Dict]:

    logger.info(f"🔍 WIPO search: {query}")
    wos = await search_wipo(query, max_results)

    results = []
    success = 0
    failures = 0

    for idx, wo in enumerate(wos, 1):
        logger.info(f"   Processing {wo} ({idx}/{len(wos)})")

        try:
            html = await fetch_detail_html(wo)
            biblio = parse_biblio(html)

            results.append({
                "wo_number": wo,
                "source": "WIPO",
                "biblio_data": biblio
            })

            success += 1

        except Exception as e:
            logger.error(f"❌ {wo}: {e}")
            failures += 1

    logger.info(f"✅ WIPO done | Success: {success} | Failures: {failures}")
    return results

# =========================
# TESTE LOCAL
# =========================
if __name__ == "__main__":
    print("\n🧪 Testing WIPO Crawler V2 (REFATORADO)\n")

    query = "darolutamide OR ODM-201 OR BAY-1841788"

    patents = asyncio.run(
        search_wipo_patents(query, max_results=2)
    )

    print(f"\nRetrieved: {len(patents)}")
    if patents:
        import json
        print(json.dumps(patents[0], indent=2, ensure_ascii=False))
