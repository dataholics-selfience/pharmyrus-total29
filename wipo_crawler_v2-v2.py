"""
WIPO PatentScope Crawler V2 - Production Grade
===============================================

FIXED: Robust HTML parsing with Groq fallback for validation
"""

import asyncio
import httpx
import re
import logging
import random
from typing import List, Dict, Optional, Any
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from dataclasses import dataclass
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wipo_v2")


class ExtractionMethod(Enum):
    """Track which extraction method succeeded"""
    STATIC_HTTPX = "static_httpx"
    DIRECT_PLAYWRIGHT = "direct_playwright"
    INTERACTIVE_PLAYWRIGHT = "interactive_playwright"
    FAILED = "failed"


class WIPOExtractionError(Exception):
    """Custom exception for WIPO extraction failures"""
    pass


@dataclass
class WIPOStats:
    """Track extraction statistics"""
    static_success: int = 0
    direct_success: int = 0
    interactive_success: int = 0
    failures: int = 0
    
    def success_rate(self) -> float:
        total = self.static_success + self.direct_success + self.interactive_success + self.failures
        if total == 0:
            return 0.0
        return (total - self.failures) / total * 100


def looks_wrong(value: str, field_type: str) -> bool:
    """
    Validate if extracted value looks correct
    
    Args:
        value: Extracted value
        field_type: Type of field (title, ipc, applicants, etc)
        
    Returns:
        True if value looks wrong/invalid
    """
    if not value or not value.strip():
        return True
    
    value = value.strip()
    
    # Generic invalid values
    invalid_patterns = [
        'close', 'reset', 'click', 'button', 'submit',
        'search', 'menu', 'nav', 'header', 'footer',
        'green inventory', 'loading', 'please wait'
    ]
    
    if value.lower() in invalid_patterns:
        return True
    
    # Field-specific validation
    if field_type == 'title':
        # Title should be substantial (>10 chars) and not UI text
        if len(value) < 10:
            return True
        if value.lower().startswith(('click', 'close', 'open', 'select')):
            return True
            
    elif field_type == 'ipc':
        # IPC should match pattern: A61K 9/14 2006.1
        if not re.search(r'[A-H]\d{2}[A-Z]', value):
            return True
            
    elif field_type == 'publication_number':
        # Should start with WO
        if not value.startswith('WO'):
            return True
            
    elif field_type == 'applicants':
        # Should be company/person name (>3 chars)
        if len(value) < 3:
            return True
            
    return False


async def groq_extract_field(html: str, field_name: str, groq_api_key: str = None) -> Optional[str]:
    """
    Use Groq to extract field from HTML when DOM parsing fails
    
    Args:
        html: Raw HTML content
        field_name: Field to extract (e.g., "title", "applicants")
        groq_api_key: Groq API key
        
    Returns:
        Extracted value or None
    """
    if not groq_api_key:
        return None
    
    try:
        from groq import AsyncGroq
        
        client = AsyncGroq(api_key=groq_api_key)
        
        # Truncate HTML to first 4000 chars (token limit)
        html_sample = html[:4000]
        
        prompt = f"""Extract the {field_name} from this WIPO patent HTML.
Return ONLY the value, nothing else.

HTML:
{html_sample}

{field_name}:"""
        
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.debug(f"Groq extraction failed: {e}")
        return None


class WIPOCrawlerV2:
    """
    Production-ready WIPO PatentScope crawler
    
    Key improvements over V1:
    - NO click-based navigation
    - Direct URL construction for all tabs
    - Static HTML parsing where possible
    - Playwright only when necessary
    - Proper wait strategies
    - Anti-bot detection
    - Robust DOM parsing with Groq fallback
    """
    
    BASE_URL = "https://patentscope.wipo.int"
    SEARCH_URL = f"{BASE_URL}/search/en/result.jsf"
    DETAIL_URL = f"{BASE_URL}/search/en/detail.jsf"
    
    # Tab URL parameters
    TAB_PARAMS = {
        'biblio': '',
        'description': '&tab=PCTDESCRIPTION',
        'claims': '&tab=PCTCLAIMS',
        'isr': '&tab=SEARCHREPORT',
        'wosa': '&tab=WOSA'
    }
    
    def __init__(self, use_playwright: bool = True, timeout: int = 30, groq_api_key: str = None):
        """
        Initialize crawler
        
        Args:
            use_playwright: Whether to use Playwright for fallback
            timeout: Request timeout in seconds
            groq_api_key: Groq API key for fallback extraction
        """
        self.use_playwright = use_playwright
        self.timeout = timeout
        self.groq_api_key = groq_api_key
        self.stats = WIPOStats()
        
        self.httpx_client: Optional[httpx.AsyncClient] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
    async def __aenter__(self):
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        
    async def start(self):
        """Initialize HTTP client and optionally Playwright"""
        self.httpx_client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
        )
        
        if self.use_playwright:
            await self._init_playwright()
            
        logger.info("✅ WIPO Crawler V2 initialized")
        
    async def _init_playwright(self):
        """Initialize Playwright with stealth configuration"""
        playwright = await async_playwright().start()
        
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process'
            ]
        )
        
        self.context = await self.browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York'
        )
        
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)
        
        self.page = await self.context.new_page()
        
    async def close(self):
        """Cleanup resources"""
        if self.httpx_client:
            await self.httpx_client.aclose()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
            
    async def search_wipo(
        self,
        query: str,
        max_results: int = 50,
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """Main search entry point"""
        logger.info(f"🔍 WIPO V2 search: {query}")
        
        if progress_callback:
            progress_callback(5, "Getting WO list...")
        
        wo_list = await self._get_wo_list_httpx(query)
        
        if not wo_list:
            logger.warning("   ⚠️  No WO patents found")
            return []
        
        logger.info(f"   Found {len(wo_list)} WO patents")
        
        patents_data = []
        total = min(len(wo_list), max_results)
        
        for idx, wo in enumerate(wo_list[:max_results], 1):
            try:
                if progress_callback and idx % 5 == 0:
                    progress = 5 + int((idx / total) * 90)
                    progress_callback(progress, f"Processing {idx}/{total}")
                
                logger.info(f"   Processing {wo} ({idx}/{total})")
                
                patent_data = await self._extract_patent_tiered(wo)
                
                if patent_data:
                    patents_data.append(patent_data)
                
                await asyncio.sleep(random.uniform(1.5, 3.0))
                
            except Exception as e:
                logger.error(f"   ❌ Failed {wo}: {e}")
                self.stats.failures += 1
                continue
        
        logger.info(f"✅ WIPO V2 complete: {len(patents_data)} patents")
        logger.info(f"📊 Stats: {self.stats.__dict__}")
        logger.info(f"📈 Success rate: {self.stats.success_rate():.1f}%")
        
        return patents_data
    
    async def _get_wo_list_httpx(self, query: str) -> List[str]:
        """Get WO list using httpx"""
        try:
            search_url = f"{self.SEARCH_URL}?query=FP:({query})"
            
            response = await self.httpx_client.get(search_url)
            
            if response.status_code != 200:
                logger.error(f"   Search failed: HTTP {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Multiple selector strategies
            wo_elements = (
                soup.find_all('span', class_='ps-patent-result--title--patent-number') or
                soup.find_all('span', class_=re.compile('patent.*number')) or
                soup.find_all('a', href=re.compile('docId=WO'))
            )
            
            wo_numbers = []
            for elem in wo_elements:
                wo_text = elem.get_text().strip()
                # Also check href for WO number
                if not wo_text and elem.get('href'):
                    match = re.search(r'docId=(WO\d+)', elem.get('href'))
                    if match:
                        wo_text = match.group(1)
                
                # Normalize
                wo_clean = re.sub(r'[/\s-]', '', wo_text)
                if wo_clean and wo_clean.startswith('WO'):
                    wo_numbers.append(wo_clean)
            
            return list(dict.fromkeys(wo_numbers))
            
        except Exception as e:
            logger.error(f"   ❌ Error getting WO list: {e}")
            return []
    
    async def _extract_patent_tiered(self, wo_number: str) -> Optional[Dict[str, Any]]:
        """Tiered extraction strategy"""
        # Tier 1: Static extraction with httpx
        try:
            data = await self._extract_static_httpx(wo_number)
            self.stats.static_success += 1
            return data
        except WIPOExtractionError as e:
            logger.debug(f"   Static extraction failed for {wo_number}: {e}")
        
        # Tier 2: Direct URL with Playwright
        if self.use_playwright:
            try:
                data = await self._extract_direct_playwright(wo_number)
                self.stats.direct_success += 1
                return data
            except WIPOExtractionError as e:
                logger.debug(f"   Direct Playwright failed for {wo_number}: {e}")
        
        raise WIPOExtractionError(f"All extraction tiers failed for {wo_number}")
    
    async def _extract_static_httpx(self, wo_number: str) -> Dict[str, Any]:
        """Tier 1: Static extraction using httpx + BeautifulSoup"""
        patent_data = {
            'wo_number': wo_number,
            'source': 'WIPO',
            'extraction_method': ExtractionMethod.STATIC_HTTPX.value
        }
        
        biblio_url = f"{self.DETAIL_URL}?docId={wo_number}"
        response = await self.httpx_client.get(biblio_url)
        
        if response.status_code != 200:
            raise WIPOExtractionError(f"HTTP {response.status_code}")
        
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract with robust parsing
        patent_data['biblio_data'] = await self._parse_biblio_robust(soup, html)
        
        # Validate extraction
        if not patent_data['biblio_data'].get('title') or looks_wrong(patent_data['biblio_data'].get('title', ''), 'title'):
            raise WIPOExtractionError("Title extraction failed validation")
        
        return patent_data
    
    async def _extract_direct_playwright(self, wo_number: str) -> Dict[str, Any]:
        """Tier 2: Direct URL navigation with Playwright"""
        patent_data = {
            'wo_number': wo_number,
            'source': 'WIPO',
            'extraction_method': ExtractionMethod.DIRECT_PLAYWRIGHT.value
        }
        
        biblio_url = f"{self.DETAIL_URL}?docId={wo_number}"
        
        await self.page.goto(biblio_url, wait_until='domcontentloaded', timeout=15000)
        
        try:
            await self.page.wait_for_selector(
                'div.ps-patent-detail',
                state='attached',
                timeout=10000
            )
        except:
            raise WIPOExtractionError("Patent detail container not found")
        
        html = await self.page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        patent_data['biblio_data'] = await self._parse_biblio_robust(soup, html)
        
        if not patent_data['biblio_data'].get('title') or looks_wrong(patent_data['biblio_data'].get('title', ''), 'title'):
            raise WIPOExtractionError("Extraction validation failed")
        
        return patent_data
    
    async def _parse_biblio_robust(self, soup: BeautifulSoup, html: str) -> Dict[str, Any]:
        """
        Robust bibliographic data parsing with Groq fallback
        
        Strategy:
        1. Find label element
        2. Navigate to container (tr/div)
        3. Extract value from sibling
        4. Validate with looks_wrong()
        5. Fallback to Groq if invalid
        """
        biblio = {}
        
        # ===== TITLE =====
        title = self._extract_field_with_label(soup, ['Title', 'TITLE'], ['strong', 'label', 'dt'])
        
        if looks_wrong(title, 'title'):
            logger.debug(f"   Title looks wrong: '{title}', trying Groq...")
            groq_title = await groq_extract_field(html, 'patent title', self.groq_api_key)
            title = groq_title if groq_title and not looks_wrong(groq_title, 'title') else title
        
        biblio['title'] = title
        
        # ===== PUBLICATION NUMBER =====
        pub_num = self._extract_field_with_label(soup, ['Publication Number', 'Publication No'], ['strong', 'label', 'dt'])
        
        if looks_wrong(pub_num, 'publication_number'):
            logger.debug(f"   Pub number looks wrong: '{pub_num}'")
            # Try finding WO number in any visible text
            wo_match = re.search(r'WO[/\s]?\d{4}[/\s]?\d{6}', html)
            if wo_match:
                pub_num = wo_match.group(0).replace('/', '').replace(' ', '')
        
        biblio['publication_number'] = pub_num
        
        # ===== APPLICANTS =====
        applicants = self._extract_list_field(soup, ['Applicants', 'APPLICANTS', 'Applicant'])
        
        if not applicants or (len(applicants) == 1 and looks_wrong(applicants[0], 'applicants')):
            logger.debug(f"   Applicants look wrong: {applicants}, trying Groq...")
            groq_apps = await groq_extract_field(html, 'applicants (comma separated)', self.groq_api_key)
            if groq_apps:
                applicants = [a.strip() for a in groq_apps.split(',')]
        
        biblio['applicants'] = applicants[:10]
        
        # ===== INVENTORS =====
        inventors = self._extract_list_field(soup, ['Inventors', 'INVENTORS', 'Inventor'])
        biblio['inventors'] = inventors[:10]
        
        # ===== IPC CODES =====
        ipc = self._extract_field_with_label(soup, ['IPC', 'Int. Cl.', 'International Classification'], ['strong', 'label', 'dt'])
        
        if looks_wrong(ipc, 'ipc'):
            logger.debug(f"   IPC looks wrong: '{ipc}', trying Groq...")
            groq_ipc = await groq_extract_field(html, 'IPC classification codes', self.groq_api_key)
            ipc = groq_ipc if groq_ipc and not looks_wrong(groq_ipc, 'ipc') else ipc
        
        biblio['ipc_codes'] = ipc
        
        # ===== CPC CODES =====
        cpc = self._extract_field_with_label(soup, ['CPC'], ['strong', 'label', 'dt'])
        biblio['cpc_codes'] = cpc
        
        # ===== FILING DATE =====
        filing_date = self._extract_field_with_label(soup, ['International Filing Date', 'Filing Date'], ['strong', 'label', 'dt'])
        biblio['filing_date'] = filing_date
        
        # ===== PUBLICATION DATE =====
        pub_date = self._extract_field_with_label(soup, ['Publication Date'], ['strong', 'label', 'dt'])
        biblio['publication_date'] = pub_date
        
        # ===== ABSTRACT =====
        abstract = self._extract_abstract(soup)
        biblio['abstract'] = abstract[:1000] if abstract else ""
        
        return biblio
    
    def _extract_field_with_label(self, soup: BeautifulSoup, labels: List[str], label_tags: List[str]) -> str:
        """
        Extract field by finding label and navigating to value
        
        Args:
            soup: BeautifulSoup object
            labels: Possible label texts
            label_tags: HTML tags to search for label
            
        Returns:
            Extracted value or empty string
        """
        for label_text in labels:
            for tag in label_tags:
                # Find label element
                label_elem = soup.find(tag, string=re.compile(label_text, re.I))
                
                if label_elem:
                    # Strategy 1: Next sibling
                    value_elem = label_elem.find_next_sibling()
                    if value_elem:
                        value = value_elem.get_text(strip=True)
                        if value and value.lower() != label_text.lower():
                            return value
                    
                    # Strategy 2: Parent container, then find value
                    parent = label_elem.find_parent(['tr', 'div', 'dl', 'li'])
                    if parent:
                        # Remove label text from parent
                        parent_copy = BeautifulSoup(str(parent), 'html.parser')
                        for lbl in parent_copy.find_all(tag, string=re.compile(label_text, re.I)):
                            lbl.decompose()
                        
                        value = parent_copy.get_text(strip=True)
                        if value:
                            return value
                    
                    # Strategy 3: Following text node
                    for sibling in label_elem.next_siblings:
                        if sibling.string and sibling.string.strip():
                            return sibling.string.strip()
        
        return ""
    
    def _extract_list_field(self, soup: BeautifulSoup, labels: List[str]) -> List[str]:
        """
        Extract list field (applicants, inventors)
        
        Returns:
            List of values
        """
        for label_text in labels:
            # Find section with label
            section = soup.find(string=re.compile(label_text, re.I))
            
            if section:
                # Get parent container
                parent = section.find_parent(['div', 'section', 'dl'])
                
                if parent:
                    # Find all text nodes, excluding label
                    items = []
                    for elem in parent.descendants:
                        if isinstance(elem, str):
                            text = elem.strip()
                            if text and text.lower() != label_text.lower() and len(text) > 2:
                                # Filter out UI elements
                                if not any(ui in text.lower() for ui in ['close', 'click', 'reset', 'button']):
                                    items.append(text)
                    
                    # Deduplicate and limit
                    seen = set()
                    unique_items = []
                    for item in items:
                        if item not in seen:
                            seen.add(item)
                            unique_items.append(item)
                    
                    if unique_items:
                        return unique_items
        
        return []
    
    def _extract_abstract(self, soup: BeautifulSoup) -> str:
        """Extract abstract"""
        # Multiple strategies
        abstract_elem = (
            soup.find('div', class_=re.compile('abstract', re.I)) or
            soup.find('section', class_=re.compile('abstract', re.I)) or
            soup.find('p', class_=re.compile('abstract', re.I))
        )
        
        if abstract_elem:
            # Find actual content, skip labels
            text = abstract_elem.get_text(strip=True)
            # Remove "Abstract" label if present
            text = re.sub(r'^abstract:?\s*', '', text, flags=re.I)
            return text
        
        return ""


# ============================================================================
# Integration function
# ============================================================================

async def search_wipo_patents(
    molecule: str,
    dev_codes: List[str] = None,
    cas: str = None,
    max_results: int = 50,
    groq_api_key: str = None,
    progress_callback: callable = None
) -> List[Dict[str, Any]]:
    """Main integration function - compatible with existing pipeline"""
    query_parts = [molecule]
    if dev_codes:
        query_parts.extend(dev_codes[:5])
    if cas:
        query_parts.append(cas)
    
    query = ' OR '.join(query_parts)
    
    logger.info(f"🌐 WIPO V2 search initiated: {query}")
    
    async with WIPOCrawlerV2(use_playwright=True, groq_api_key=groq_api_key) as crawler:
        results = await crawler.search_wipo(
            query=query,
            max_results=max_results,
            progress_callback=progress_callback
        )
    
    logger.info(f"✅ WIPO V2 complete: {len(results)} patents")
    
    return results


# ============================================================================
# Standalone test
# ============================================================================

async def test_wipo_v2():
    """Test V2 crawler"""
    print("🧪 Testing WIPO Crawler V2...")
    print("=" * 60)
    
    results = await search_wipo_patents(
        molecule="darolutamide",
        dev_codes=["ODM-201", "BAY-1841788"],
        max_results=5
    )
    
    print(f"\n✅ Retrieved {len(results)} patents")
    
    if results:
        print("\n📄 Sample patent:")
        import json
        print(json.dumps(results[0], indent=2))
    
    return results


if __name__ == "__main__":
    asyncio.run(test_wipo_v2())