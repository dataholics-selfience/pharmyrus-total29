"""
WIPO PatentScope Crawler - Isolated Layer
Resilient crawler for WIPO patent data extraction

CRITICAL: This is an ISOLATED layer - does NOT modify existing functionality
Ready for integration into async pipeline
"""

import asyncio
import json
import logging
import re
from typing import List, Dict, Optional, Any
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
import random

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wipo_crawler")


class WIPOCrawler:
    """
    WIPO PatentScope crawler - isolated, resilient implementation
    Based on HTML samples and navigation guide
    """
    
    BASE_URL = "https://patentscope.wipo.int"
    
    # Proxies pool (from existing system)
    PROXIES = [
        # Add your proxy list here
        # Format: {"server": "http://proxy:port", "username": "user", "password": "pass"}
    ]
    
    def __init__(self, groq_api_key: Optional[str] = None, use_proxies: bool = True):
        """
        Initialize WIPO crawler
        
        Args:
            groq_api_key: Groq API key for dynamic element identification
            use_proxies: Whether to use proxy rotation
        """
        self.groq_api_key = groq_api_key
        self.use_proxies = use_proxies
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        
    async def __aenter__(self):
        """Async context manager entry"""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
        
    async def start(self):
        """Start browser with stealth configuration"""
        playwright = await async_playwright().start()
        
        # Stealth configuration (same as existing Google crawler)
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process'
        ]
        
        self.browser = await playwright.chromium.launch(
            headless=True,
            args=launch_args
        )
        
        # Create context with stealth
        context_options = {
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'viewport': {'width': 1920, 'height': 1080},
            'locale': 'en-US',
            'timezone_id': 'America/New_York'
        }
        
        # Add proxy if enabled
        if self.use_proxies and self.PROXIES:
            proxy = random.choice(self.PROXIES)
            context_options['proxy'] = proxy
            
        self.context = await self.browser.new_context(**context_options)
        
        # Inject stealth scripts
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)
        
        logger.info("✅ WIPO Crawler initialized with stealth mode")
        
    async def close(self):
        """Close browser"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
            
    async def search_wipo(
        self,
        query: str,
        max_results: int = 200,
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Search WIPO PatentScope and extract WO patents
        
        Args:
            query: Search query (molecule name, dev codes, etc)
            max_results: Maximum results to retrieve (max 200 per page)
            progress_callback: Optional callback for progress updates
            
        Returns:
            List of WO patent data dictionaries
        """
        if not self.context:
            await self.start()
            
        logger.info(f"🔍 Searching WIPO for: {query}")
        
        # Step 1: Perform search
        search_url = f"{self.BASE_URL}/search/en/result.jsf?query=FP:({query})"
        
        page = await self.context.new_page()
        
        try:
            # Navigate to search results
            await page.goto(search_url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(random.uniform(2, 4))
            
            # Step 2: Set 200 results per page
            await self._set_results_per_page(page, max_results)
            
            # Step 3: Extract WO numbers from results
            wo_numbers = await self._extract_wo_numbers(page)
            
            logger.info(f"   Found {len(wo_numbers)} WO patents")
            
            if progress_callback:
                progress_callback(5, f"Found {len(wo_numbers)} WO patents")
            
            # Step 4: Process each WO patent
            patents_data = []
            total = len(wo_numbers)
            
            for idx, wo_number in enumerate(wo_numbers[:max_results], 1):
                try:
                    if progress_callback and idx % 10 == 0:
                        progress = 5 + int((idx / total) * 15)  # 5-20% range
                        progress_callback(progress, f"Processing WO {idx}/{total}")
                    
                    logger.info(f"   Processing {wo_number} ({idx}/{total})")
                    
                    patent_data = await self._extract_patent_data(page, wo_number)
                    if patent_data:
                        patents_data.append(patent_data)
                    
                    # Rate limiting
                    await asyncio.sleep(random.uniform(1, 2))
                    
                except Exception as e:
                    logger.error(f"   ❌ Error processing {wo_number}: {e}")
                    continue
                    
            await page.close()
            
            logger.info(f"✅ WIPO search complete: {len(patents_data)} patents extracted")
            return patents_data
            
        except Exception as e:
            logger.error(f"❌ WIPO search failed: {e}")
            await page.close()
            return []
            
    async def _set_results_per_page(self, page: Page, count: int = 200):
        """
        Set number of results per page
        Based on: 2 - 200 resultados.html
        """
        try:
            # Wait for per-page selector
            await page.wait_for_selector('label.ps-plain-select--label:has-text("Per page")', timeout=10000)
            
            # Click on dropdown
            await page.click('label.ps-plain-select--label:has-text("Per page")')
            await asyncio.sleep(1)
            
            # Select 200 results option
            await page.click(f'text="{count}"')
            await asyncio.sleep(2)
            
            logger.info(f"   ✅ Set {count} results per page")
            
        except Exception as e:
            logger.warning(f"   ⚠️  Could not set results per page: {e}")
            
    async def _extract_wo_numbers(self, page: Page) -> List[str]:
        """
        Extract WO numbers from search results
        Based on: 2 - 200 resultados.html
        Pattern: <span class="notranslate ps-patent-result--title--patent-number">WO/2022/049075</span>
        """
        try:
            # Wait for results
            await page.wait_for_selector('.ps-patent-result--title--patent-number', timeout=10000)
            
            # Extract all WO numbers
            wo_elements = await page.query_selector_all('.ps-patent-result--title--patent-number')
            
            wo_numbers = []
            for element in wo_elements:
                text = await element.text_content()
                if text and 'WO' in text:
                    # Normalize: WO/2022/049075 -> WO2022049075
                    wo_clean = text.replace('/', '').strip()
                    wo_numbers.append(wo_clean)
                    
            return wo_numbers
            
        except Exception as e:
            logger.error(f"   ❌ Failed to extract WO numbers: {e}")
            return []
            
    async def _extract_patent_data(self, page: Page, wo_number: str) -> Optional[Dict[str, Any]]:
        """
        Extract complete patent data for a WO number
        Navigates through all tabs: Biblio, Description, Claims, ISR, WOSA
        """
        try:
            # Click on WO number in results
            await page.click(f'text="{wo_number}"')
            await page.wait_for_load_state('networkidle', timeout=30000)
            await asyncio.sleep(2)
            
            patent_data = {
                'wo_number': wo_number,
                'source': 'WIPO',
                'biblio_data': {},
                'description': None,
                'claims': [],
                'isr_data': {},
                'wosa_data': {}
            }
            
            # TAB 1: PCT Biblio Data (default tab)
            patent_data['biblio_data'] = await self._extract_biblio_data(page)
            
            # TAB 2: Description
            try:
                await page.click('a:has-text("Description")')
                await asyncio.sleep(2)
                patent_data['description'] = await self._extract_description(page)
            except:
                logger.warning(f"   ⚠️  No Description tab for {wo_number}")
                
            # TAB 3: Claims
            try:
                await page.click('a:has-text("Claims")')
                await asyncio.sleep(2)
                patent_data['claims'] = await self._extract_claims(page)
            except:
                logger.warning(f"   ⚠️  No Claims tab for {wo_number}")
                
            # TAB 4: ISR/WOSA
            try:
                await page.click('a:has-text("ISR/WOSA")')
                await asyncio.sleep(2)
                
                # Extract ISR
                patent_data['isr_data'] = await self._extract_isr(page)
                
                # Click on WOSA sub-tab
                try:
                    await page.click('a:has-text("Written Opinion")')
                    await asyncio.sleep(2)
                    patent_data['wosa_data'] = await self._extract_wosa(page)
                except:
                    logger.warning(f"   ⚠️  No WOSA data for {wo_number}")
                    
            except:
                logger.warning(f"   ⚠️  No ISR/WOSA tab for {wo_number}")
                
            # Navigate back to results
            await page.go_back()
            await asyncio.sleep(1)
            
            return patent_data
            
        except Exception as e:
            logger.error(f"   ❌ Failed to extract data for {wo_number}: {e}")
            return None
            
    async def _extract_biblio_data(self, page: Page) -> Dict[str, Any]:
        """
        Extract bibliographic data from PCT Biblio tab
        Based on: 4.1 - PCT Biblio. Data.html
        """
        biblio = {}
        
        try:
            # Extract structured fields
            fields_map = {
                'Publication Number': 'publication_number',
                'Publication Date': 'publication_date',
                'International Application No.': 'application_number',
                'International Filing Date': 'filing_date',
                'IPC': 'ipc_codes',
                'CPC': 'cpc_codes',
                'Publication Language': 'publication_language',
                'Filing Language': 'filing_language'
            }
            
            for label, key in fields_map.items():
                try:
                    value = await page.locator(f'text="{label}"').locator('..').inner_text()
                    biblio[key] = value.replace(label, '').strip()
                except:
                    pass
                    
            # Applicants
            try:
                applicants_text = await page.locator('text="Applicants"').locator('..').inner_text()
                biblio['applicants'] = [a.strip() for a in applicants_text.split('\n') if a.strip() and a.strip() != 'Applicants']
            except:
                biblio['applicants'] = []
                
            # Inventors
            try:
                inventors_text = await page.locator('text="Inventors"').locator('..').inner_text()
                biblio['inventors'] = [i.strip() for i in inventors_text.split('\n') if i.strip() and i.strip() != 'Inventors']
            except:
                biblio['inventors'] = []
                
            # Title
            try:
                title_elem = await page.query_selector('text="Title"')
                if title_elem:
                    parent = await title_elem.evaluate_handle('el => el.parentElement')
                    title_text = await parent.inner_text()
                    # Extract EN title
                    match = re.search(r'\(EN\)\s*(.+?)(?:\(FR\)|$)', title_text, re.DOTALL)
                    if match:
                        biblio['title'] = match.group(1).strip()
            except:
                pass
                
            # Abstract
            try:
                abstract_elem = await page.query_selector('text="Abstract"')
                if abstract_elem:
                    parent = await abstract_elem.evaluate_handle('el => el.parentElement')
                    abstract_text = await parent.inner_text()
                    # Extract EN abstract
                    match = re.search(r'\(EN\)\s*(.+?)(?:\(FR\)|$)', abstract_text, re.DOTALL)
                    if match:
                        biblio['abstract'] = match.group(1).strip()
            except:
                pass
                
            # Priority Data
            try:
                priority_section = await page.locator('text="Priority Data"').locator('..').inner_text()
                biblio['priority_data'] = priority_section.replace('Priority Data', '').strip()
            except:
                pass
                
        except Exception as e:
            logger.warning(f"   ⚠️  Error extracting biblio data: {e}")
            
        return biblio
        
    async def _extract_description(self, page: Page) -> Optional[str]:
        """
        Extract description (FAST mode - summary only)
        Based on: 4.2 - Description.html
        
        Only extract:
        - Technical field
        - Summary
        - Key examples
        """
        try:
            # Get first 5000 chars as summary
            desc_container = await page.query_selector('.ps-patent-detail-content')
            if desc_container:
                full_text = await desc_container.inner_text()
                # Take first 5000 chars
                return full_text[:5000] if full_text else None
            return None
        except:
            return None
            
    async def _extract_claims(self, page: Page) -> List[Dict[str, Any]]:
        """
        Extract claims with independent/dependent classification
        Based on: 5 - Claims.html
        """
        claims = []
        
        try:
            # Claims are typically numbered
            claim_elements = await page.query_selector_all('.claim')
            
            for elem in claim_elements:
                try:
                    text = await elem.inner_text()
                    
                    # Detect claim number
                    match = re.match(r'^(\d+)\.\s*', text)
                    if match:
                        claim_num = int(match.group(1))
                        claim_text = text[match.end():].strip()
                        
                        # Classify as independent or dependent
                        is_dependent = bool(re.search(r'claim\s+\d+', claim_text, re.IGNORECASE))
                        
                        claims.append({
                            'claim_number': claim_num,
                            'claim_type': 'dependent' if is_dependent else 'independent',
                            'claim_text': claim_text
                        })
                except:
                    continue
                    
        except Exception as e:
            logger.warning(f"   ⚠️  Error extracting claims: {e}")
            
        return claims
        
    async def _extract_isr(self, page: Page) -> Dict[str, Any]:
        """
        Extract International Search Report data
        Based on: 4.3.1 - ISR.html
        
        Focus on:
        - Citation documents (D1, D2, etc)
        - Categories (X, Y, etc)
        """
        isr_data = {
            'citations': [],
            'search_fields': None
        }
        
        try:
            # Extract citation table
            rows = await page.query_selector_all('table tr')
            
            for row in rows:
                try:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 2:
                        category = await cells[0].inner_text()
                        citation = await cells[1].inner_text()
                        
                        isr_data['citations'].append({
                            'category': category.strip(),
                            'document': citation.strip()
                        })
                except:
                    continue
                    
        except Exception as e:
            logger.warning(f"   ⚠️  Error extracting ISR: {e}")
            
        return isr_data
        
    async def _extract_wosa(self, page: Page) -> Dict[str, Any]:
        """
        Extract Written Opinion of ISA
        Based on: 4.3.3 - WOSA.html
        
        Focus on:
        - Novelty, Inventive Step, Industrial Applicability per claim
        - Examiner conclusions
        """
        wosa_data = {
            'novelty': {},
            'inventive_step': {},
            'industrial_applicability': {},
            'conclusions': []
        }
        
        try:
            # Look for Box No. V table
            content = await page.inner_text('body')
            
            # Extract novelty assessment
            novelty_match = re.search(r'Novelty \(N\)\s+Claims\s+([\d\-,\s]+)\s+YES', content)
            if novelty_match:
                wosa_data['novelty']['yes_claims'] = novelty_match.group(1).strip()
                
            novelty_no_match = re.search(r'Novelty \(N\)\s+Claims\s+([\d\-,\s]+)\s+NO', content)
            if novelty_no_match:
                wosa_data['novelty']['no_claims'] = novelty_no_match.group(1).strip()
                
            # Extract inventive step
            is_match = re.search(r'Inventive step \(IS\)\s+Claims\s+([\d\-,\s]+)\s+NO', content)
            if is_match:
                wosa_data['inventive_step']['no_claims'] = is_match.group(1).strip()
                
            # Extract main conclusions (first 1000 chars of reasoned statement)
            conclusion_match = re.search(r'Reasoned statement(.{0,1000})', content, re.DOTALL)
            if conclusion_match:
                wosa_data['conclusions'] = conclusion_match.group(1).strip()
                
        except Exception as e:
            logger.warning(f"   ⚠️  Error extracting WOSA: {e}")
            
        return wosa_data


# =========================
# INTEGRATION FUNCTION
# =========================

async def search_wipo_patents(
    molecule: str,
    dev_codes: List[str] = None,
    cas: str = None,
    max_results: int = 100,
    groq_api_key: str = None,
    progress_callback: callable = None
) -> List[Dict[str, Any]]:
    """
    Main integration function for WIPO search
    ISOLATED - ready to plug into existing pipeline
    
    Args:
        molecule: Molecule name
        dev_codes: Development codes
        cas: CAS number
        max_results: Max results to retrieve
        groq_api_key: Groq API key for resilient extraction
        progress_callback: Progress callback function
        
    Returns:
        List of WO patent dictionaries
    """
    # Build query
    query_parts = [molecule]
    if dev_codes:
        query_parts.extend(dev_codes[:5])  # Limit to 5 dev codes
    if cas:
        query_parts.append(cas)
        
    query = ' OR '.join(query_parts)
    
    logger.info(f"🌐 WIPO search initiated: {query}")
    
    async with WIPOCrawler(groq_api_key=groq_api_key) as crawler:
        results = await crawler.search_wipo(
            query=query,
            max_results=max_results,
            progress_callback=progress_callback
        )
        
    logger.info(f"✅ WIPO search complete: {len(results)} WO patents")
    
    return results


# =========================
# STANDALONE TEST
# =========================

async def test_wipo_crawler():
    """Test WIPO crawler standalone"""
    
    print("🧪 Testing WIPO Crawler...")
    print("=" * 60)
    
    # Test search
    results = await search_wipo_patents(
        molecule="darolutamide",
        dev_codes=["ODM-201", "BAY-1841788"],
        max_results=10
    )
    
    print(f"\n✅ Retrieved {len(results)} patents")
    
    if results:
        print("\n📄 Sample patent:")
        sample = results[0]
        print(json.dumps(sample, indent=2)[:500])
        
    return results


if __name__ == "__main__":
    # Test standalone
    asyncio.run(test_wipo_crawler())
