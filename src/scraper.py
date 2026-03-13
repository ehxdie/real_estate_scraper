from __future__ import annotations

import random
import time
from urllib.parse import urljoin

from loguru import logger
from playwright.sync_api import sync_playwright, Error as PlaywrightError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from src.config import (
    HEADERS,
    MAX_PAGES,
    MAX_LISTINGS,
    REQUEST_DELAY,
    PROXIES,
    MIN_PRICE,
    MAX_PRICE,
    KEYWORDS,
    PROPERTY_TYPES,
    USE_STEALTH,
)
from src.parser import parse_listing_page, parse_listing_detail
from src.utils import delay


class CraigslistScraper:
    """
    Scrapes Craigslist real-estate listings for a given base URL.

    Features
    --------
    • Pagination (up to MAX_PAGES pages, 120 results each)
    • Price filtering ($MIN_PRICE – $MAX_PRICE)
    • Keyword / property-type filtering (title → description → address fallback)
    • Duplicate filtering via visited URL set
    • Retry logic (3 attempts, 2 s back-off) via tenacity
    • Optional proxy support
    • Optional lightweight stealth scripts
    • Structured logging via loguru
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.results: list[dict] = []
        self.visited: set[str] = set()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _price_to_int(self, price_str: str | None) -> int | None:
        if not price_str:
            return None
        try:
            return int(price_str.replace("$", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            return None

    def _matches_keywords(self, text: str | None) -> bool:
        if not text:
            return False
        txt = text.lower()
        return any(k in txt for k in KEYWORDS)

    def _matches_property_type(self, text: str | None) -> bool:
        if not text:
            return False
        t = text.lower()
        return any(pt in t for pt in PROPERTY_TYPES)

    def _is_relevant(self, text: str | None) -> bool:
        return self._matches_keywords(text) or self._matches_property_type(text)

    # ── network ───────────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(PlaywrightError),
        reraise=True,
    )
    def fetch_page(self, page, url: str) -> str:
        logger.info(f"Fetching: {url}")
        time.sleep(random.uniform(3, 7))  # longer jitter reduces rate-limit blocks

        page.goto(url, timeout=120_000, wait_until="domcontentloaded")  # 2 min for slow proxies

        # Mimic human scrolling
        page.mouse.wheel(0, 600)
        time.sleep(random.uniform(0.5, 1.5))
        page.mouse.wheel(0, -600)

        html = page.content()

        # Only treat as blocked if it's clearly an error page.
        lower = html.lower()
        is_blocked = (
            "your ip has been blocked" in lower
            or "access denied" in lower
            or ("403 forbidden" in lower and len(html) < 2000)
            or (len(html) < 500 and ("blocked" in lower or "forbidden" in lower))
        )
        if is_blocked:
            logger.warning(f"Block detected at {url} (html length: {len(html)})")
            raise PlaywrightError("Blocked by Craigslist")

        logger.debug(f"Page fetched OK, html length={len(html)}")

        return html

    # ── main scrape loop ──────────────────────────────────────────────────────

    def scrape(self) -> list[dict]:
        with sync_playwright() as p:
            launch_kwargs: dict = {"headless": True}

            if PROXIES:
                server = PROXIES.get("https") or PROXIES.get("http")
                if server:
                    # Playwright needs proxy credentials split out separately,
                    # not embedded in the URL like curl/requests uses them.
                    # Parse: http://user:pass@host:port
                    import re
                    m = re.match(r"https?://([^:]+):([^@]+)@(.+)", server)
                    if m:
                        username, password, host = m.group(1), m.group(2), m.group(3)
                        launch_kwargs["proxy"] = {
                            "server": f"http://{host}",
                            "username": username,
                            "password": password,
                        }
                        logger.info(f"Using proxy: {host}")
                    else:
                        launch_kwargs["proxy"] = {"server": server}

            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={
                    k: v for k, v in HEADERS.items() if k != "User-Agent"
                },
            )

            if USE_STEALTH:
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
                    window.chrome = { runtime: {} };
                """)

            page = context.new_page()

            for page_number in range(MAX_PAGES):

                # Stop early if we've hit the target
                if len(self.results) >= MAX_LISTINGS:
                    logger.info(f"Reached {MAX_LISTINGS} listings — stopping early.")
                    break

                offset = page_number * 120
                url = f"{self.base_url}?s={offset}"

                try:
                    html = self.fetch_page(page, url)
                except Exception as e:
                    logger.error(f"Giving up on {url}: {e}")
                    continue

                listings = parse_listing_page(html)
                if not listings:
                    logger.info(f"No listings parsed on page {page_number + 1} — possible block or end of results.")
                    continue

                logger.info(f"Page {page_number + 1}: found {len(listings)} raw listings.")

                for item in listings:

                    # Stop early if we've hit the target
                    if len(self.results) >= MAX_LISTINGS:
                        break

                    raw_url = item.get("url")
                    if not raw_url:
                        continue

                    # Normalise relative URLs
                    if not raw_url.startswith("http"):
                        item["url"] = urljoin(self.base_url, raw_url)

                    # ── Duplicate check ──────────────────────────────────────
                    if item["url"] in self.visited:
                        continue

                    # ── Price filter ─────────────────────────────────────────
                    price_val = self._price_to_int(item.get("price"))
                    if price_val is None or not (MIN_PRICE <= price_val <= MAX_PRICE):
                        logger.debug(f"  ✗ Price filtered: '{item.get('title')}' @ {item.get('price')} (parsed={price_val})")
                        continue

                    item["price"] = price_val  # store as int

                    # ── Relevance check + detail fetch ───────────────────────
                    title_relevant = self._is_relevant(item.get("title"))

                    try:
                        detail_html = self.fetch_page(page, item["url"])
                        detail = parse_listing_detail(detail_html)
                    except Exception as e:
                        logger.warning(f"Could not fetch detail for {item['url']}: {e}")
                        detail = {}

                    # Merge detail fields (don't overwrite already-set values)
                    for k, v in detail.items():
                        if v is not None and item.get(k) is None:
                            item[k] = v

                    # If title wasn't relevant, check description / address
                    if not title_relevant:
                        desc = item.get("description") or ""
                        addr = item.get("address") or ""
                        if not (self._is_relevant(desc) or self._is_relevant(addr)):
                            logger.debug(f"Skipping non-relevant listing: {item.get('title')}")
                            continue

                    # ── Accept listing ────────────────────────────────────────
                    self.visited.add(item["url"])
                    self.results.append(item)
                    logger.info(f"  [{len(self.results)}/{MAX_LISTINGS}] Accepted: {item.get('title')} @ ${price_val}")

                    # ── Write to CSV progressively after each accepted listing ─
                    self._flush_csv()

                delay(REQUEST_DELAY)

            browser.close()

        logger.info(f"Collected {len(self.results)} listings from {self.base_url}")
        return self.results

    def _flush_csv(self):
        """Append current results to CSV so progress is visible in real time."""
        import pandas as pd
        import os
        os.makedirs("data", exist_ok=True)
        df = pd.DataFrame(self.results)
        df.to_csv("data/listings_progress.csv", index=False)