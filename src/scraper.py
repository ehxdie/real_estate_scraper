from __future__ import annotations

from typing import Optional
import re
import time
import random

from loguru import logger
from src.config import (
    HEADERS,
    PROXIES,
    USE_STEALTH,
    MAX_PAGES,
    MAX_LISTINGS,
    REQUEST_DELAY,
    MIN_PRICE,
    MAX_PRICE,
    KEYWORDS,
    PROPERTY_TYPES,
)
from src.parser import parse_zillow_search
from src.utils import delay
from playwright.sync_api import sync_playwright

# playwright-stealth patches ~20 detection vectors that manual init scripts miss.
# Install with: pip install playwright-stealth --break-system-packages
try:
    from playwright_stealth import stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    logger.warning(
        "[stealth] playwright-stealth not installed — falling back to manual init scripts. "
        "Run: pip install playwright-stealth --break-system-packages"
    )


def _apply_stealth(page, context) -> None:
    """Apply the best available stealth method to a Playwright page."""
    if USE_STEALTH:
        if _STEALTH_AVAILABLE:
            stealth_sync(page)
            logger.debug("[stealth] playwright-stealth applied to page")
        else:
            # Minimal manual fallback — weaker but better than nothing
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
            """)
            logger.debug("[stealth] Manual init-script stealth applied")


def _parse_proxy(proxies: dict | None) -> dict | None:
    """Convert a requests-style proxy dict into a Playwright proxy dict."""
    if not proxies:
        return None
    server = proxies.get("https") or proxies.get("http")
    if not server:
        return None
    m = re.match(r"https?://([^:]+):([^@]+)@(.+)", server)
    if m:
        username, password, host = m.group(1), m.group(2), m.group(3)
        return {"server": f"http://{host}", "username": username, "password": password}
    return {"server": server}


def _is_bot_wall(html: str) -> bool:
    """
    Return True only when Zillow's __NEXT_DATA__ is missing AND the page
    contains explicit bot-challenge signals.

    Using __NEXT_DATA__ presence as the primary signal avoids false positives
    where Zillow legitimately includes the word "captcha" somewhere on the page.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    has_next_data = bool(soup.find("script", {"id": "__NEXT_DATA__"}))
    if has_next_data:
        return False  # real page — even if "captcha" appears somewhere

    lower = html.lower()
    bot_signals = (
        "captcha" in lower
        or "are you a human" in lower
        or "robot" in lower
        or "access denied" in lower
        or ("403 forbidden" in lower and len(html) < 3000)
        or len(html) < 2000
    )
    return bot_signals


class ZillowScraper:
    """
    Dedicated Zillow scraper with proper pagination and __NEXT_DATA__ extraction.

    Key differences from CraigslistScraper:
    • Pagination via ?currentPage=N  (not offset)
    • Skips per-listing detail fetches — beds/baths/sqft come from the search JSON
    • Bot-wall detection: uses __NEXT_DATA__ presence, not just keyword matching
    • playwright-stealth integration (falls back gracefully if not installed)
    • Locale + timezone set to match a real US browser
    • Writes zpid + zestimate columns to CSV for later enrichment
    """

    PAGE_PARAM = "currentPage"

    def __init__(self, base_url: str):
        self.base_url = base_url.split("?")[0]
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(base_url)
        self._qs = parse_qs(parsed.query)
        self.results: list[dict] = []
        self.visited: set[str] = set()

    def _build_url(self, page: int) -> str:
        from urllib.parse import urlencode, urlparse, urlunparse
        qs = {k: v[0] for k, v in self._qs.items()}
        qs[self.PAGE_PARAM] = str(page)
        parsed = urlparse(self.base_url)
        return urlunparse(parsed._replace(query=urlencode(qs)))

    def _price_to_int(self, val) -> Optional[int]:
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            m = re.search(r"[\d,]+", val.replace("$", ""))
            if m:
                try:
                    return int(m.group().replace(",", ""))
                except ValueError:
                    pass
        return None

    def _is_relevant(self, item: dict) -> bool:
        """Check property type against allowed types."""
        home_type = (item.get("home_type") or "").lower()
        allowed = {"single_family", "multi_family", "duplex", "sfh"}
        if home_type:
            return any(a in home_type for a in allowed)
        text = f"{item.get('title', '')} {item.get('address', '')}".lower()
        return any(k in text for k in KEYWORDS + PROPERTY_TYPES)

    def scrape(self) -> list[dict]:
        consecutive_empty = 0

        with sync_playwright() as p:
            launch_kwargs: dict = {"headless": True}

            proxy_cfg = _parse_proxy(PROXIES)
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
                logger.info(f"[zillow] Using proxy: {proxy_cfg['server']}")
            else:
                logger.info("[zillow] No proxy configured — scraping direct")

            browser = p.chromium.launch(**launch_kwargs)

            context = browser.new_context(
                # Up-to-date Chrome 124 UA — Zillow fingerprints stale UAs
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/avif,image/webp,*/*;q=0.8"
                    ),
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                },
            )

            page = context.new_page()
            _apply_stealth(page, context)

            for page_number in range(1, MAX_PAGES + 1):
                if len(self.results) >= MAX_LISTINGS:
                    logger.info(f"[zillow] Reached {MAX_LISTINGS} listings — stopping.")
                    break

                url = self._build_url(page_number)
                logger.info(f"[zillow] Fetching page {page_number}: {url}")

                try:
                    page.goto(url, timeout=120_000, wait_until="domcontentloaded")

                    # Wait for either the data script tag or a property card
                    try:
                        page.wait_for_selector(
                            'script#__NEXT_DATA__, [data-test="property-card"]',
                            timeout=30_000,
                        )
                    except Exception:
                        pass  # parse whatever loaded

                    # Human-like scroll pattern to trigger lazy loading
                    for scroll_y in [300, 700, 1100, 1500, 1100, 700, 300]:
                        page.evaluate(f"window.scrollTo(0, {scroll_y})")
                        time.sleep(random.uniform(0.2, 0.5))

                    html = page.content()

                    # Save debug HTML
                    import os
                    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
                    os.makedirs(log_dir, exist_ok=True)
                    dbg = os.path.join(log_dir, f"zillow_page_{page_number}.html")
                    with open(dbg, "w", encoding="utf-8") as fh:
                        fh.write(html)
                    logger.debug(f"[zillow] Debug HTML → {dbg}")

                    # Smarter bot-wall detection: requires __NEXT_DATA__ to be absent
                    if _is_bot_wall(html):
                        logger.warning(
                            f"[zillow] Bot wall on page {page_number} — "
                            "__NEXT_DATA__ missing + bot signals present. "
                            "Consider rotating to a residential proxy."
                        )
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            logger.error("[zillow] Two consecutive bot walls — aborting.")
                            break
                        wait = random.uniform(20, 40)
                        logger.info(f"[zillow] Backing off {wait:.0f}s before retry…")
                        time.sleep(wait)
                        continue

                except Exception as e:
                    logger.error(f"[zillow] Failed to load page {page_number}: {e}")
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        break
                    continue

                listings = parse_zillow_search(html)
                logger.info(f"[zillow] Page {page_number}: parsed {len(listings)} raw listings")

                if not listings:
                    consecutive_empty += 1
                    logger.warning(
                        f"[zillow] No listings on page {page_number} "
                        f"({consecutive_empty} empty page(s) in a row)"
                    )
                    if consecutive_empty >= 2:
                        logger.info("[zillow] Two empty pages — end of results or bot wall. Stopping.")
                        break
                    time.sleep(random.uniform(5, 10))
                    continue

                consecutive_empty = 0  # reset on success

                for item in listings:
                    if len(self.results) >= MAX_LISTINGS:
                        break

                    item_url = item.get("url", "")
                    if not item_url or item_url in self.visited:
                        continue

                    # ── Price filter ──────────────────────────────────────────
                    price_val = self._price_to_int(item.get("price"))
                    if price_val is None or not (MIN_PRICE <= price_val <= MAX_PRICE):
                        logger.debug(
                            f"  ✗ Price filtered: {item.get('address')} @ {item.get('price')}"
                        )
                        continue
                    item["price"] = price_val

                    # ── Relevance / property type filter ─────────────────────
                    if not self._is_relevant(item):
                        logger.debug(
                            f"  ✗ Type filtered: {item.get('home_type')} — {item.get('address')}"
                        )
                        continue

                    # ── Accept ────────────────────────────────────────────────
                    self.visited.add(item_url)
                    self.results.append(item)

                    zestimate = item.get("zestimate")
                    zest_str = (
                        f" | Zestimate: ${zestimate:,}" if isinstance(zestimate, int) else ""
                    )
                    logger.info(
                        f"  [{len(self.results)}/{MAX_LISTINGS}] ✓ "
                        f"{item.get('address')} @ ${price_val:,} | "
                        f"{item.get('bedrooms')}bd {item.get('bathrooms')}ba"
                        f"{zest_str}"
                    )
                    self._flush_csv()

                # Polite inter-page delay
                delay(random.uniform(REQUEST_DELAY + 3, REQUEST_DELAY + 8))

            browser.close()

        logger.info(f"[zillow] Collected {len(self.results)} listings total")
        return self.results

    def _flush_csv(self):
        import pandas as pd
        import os
        os.makedirs("data", exist_ok=True)
        pd.DataFrame(self.results).to_csv("data/zillow_progress.csv", index=False)


class CraigslistScraper:
    """
    Craigslist scraper using Playwright for JS-rendered pages.

    Paginates via the `s=` offset parameter, applies price/type filters,
    and returns a list of dicts compatible with the exporter.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.split("?")[0]
        self.results: list[dict] = []
        self.visited: set[str] = set()

    def _build_url(self, page: int) -> str:
        per_page = 120
        offset = (page - 1) * per_page
        return f"{self.base_url}?s={offset}"

    def _price_to_int(self, val) -> Optional[int]:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)
        try:
            s = str(val).replace("$", "").replace(",", "").strip()
            return int(s)
        except Exception:
            return None

    def scrape(self) -> list[dict]:
        import requests
        from bs4 import BeautifulSoup
        from src.config import (
            HEADERS, MAX_PAGES, MAX_LISTINGS, REQUEST_DELAY,
            MIN_PRICE, MAX_PRICE, KEYWORDS, PROPERTY_TYPES,
        )
        from src.utils import delay

        for page_number in range(1, MAX_PAGES + 1):
            if len(self.results) >= MAX_LISTINGS:
                logger.info(f"[craigslist] Reached {MAX_LISTINGS} listings — stopping.")
                break

            url = self._build_url(page_number)
            logger.info(f"[craigslist] Fetching page {page_number}: {url}")

            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"[craigslist] Failed to fetch {url}: {e}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select(".result-row, .result-info")
            parsed = 0

            for r in rows:
                if len(self.results) >= MAX_LISTINGS:
                    break

                a = r.select_one("a.result-title") or r.select_one("a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href")

                price_el = r.select_one(".result-price")
                price = self._price_to_int(price_el.get_text() if price_el else None)
                if price is None or not (MIN_PRICE <= price <= MAX_PRICE):
                    continue

                addr_el = r.select_one(".result-hood")
                address = (
                    addr_el.get_text(strip=True).strip("() ") if addr_el else title
                )

                text = f"{title} {address}".lower()
                if not any(k in text for k in KEYWORDS + PROPERTY_TYPES):
                    continue

                if not href or href in self.visited:
                    continue

                item = {
                    "title": title,
                    "url": href,
                    "price": price,
                    "address": address,
                }

                self.visited.add(href)
                self.results.append(item)
                parsed += 1
                logger.info(
                    f"  [{len(self.results)}/{MAX_LISTINGS}] ✓ {title} @ ${price:,}"
                )

            logger.info(f"[craigslist] Page {page_number}: accepted {parsed} listings")
            delay(random.uniform(REQUEST_DELAY, REQUEST_DELAY + 2))

        logger.info(f"[craigslist] Collected {len(self.results)} listings total")
        return self.results