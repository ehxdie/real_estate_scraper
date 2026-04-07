from __future__ import annotations

import json, re
from bs4 import BeautifulSoup
from loguru import logger


def _extract_next_data(html: str) -> dict:
    """Pull the __NEXT_DATA__ JSON blob that Zillow embeds on every page."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        logger.warning("[zillow] __NEXT_DATA__ script tag not found — page may be a CAPTCHA wall")
        return {}
    try:
        return json.loads(script.string)
    except json.JSONDecodeError as e:
        logger.warning(f"[zillow] Failed to parse __NEXT_DATA__: {e}")
        return {}


def parse_zillow_search(html: str) -> list[dict]:
    """
    Parse Zillow search-results page via the embedded __NEXT_DATA__ JSON blob.

    Falls back to anchor heuristics only if the JSON blob is absent (CAPTCHA wall, etc.).
    """
    data = _extract_next_data(html)
    results = []

    # Path in the JSON tree varies slightly by Zillow deploy; try both known paths.
    search_results = (
        data.get("props", {})
            .get("pageProps", {})
            .get("searchPageState", {})
            .get("cat1", {})
            .get("searchResults", {})
            .get("listResults", [])
        or
        data.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("cat1", {})
            .get("searchResults", {})
            .get("listResults", [])
    )

    if search_results:
        logger.debug(f"[zillow] __NEXT_DATA__ yielded {len(search_results)} listings")
        for item in search_results:
            # Skip non-property entries (ads, map-pins, etc.)
            if item.get("hdpData") is None and item.get("detailUrl") is None:
                continue

            url = item.get("detailUrl", "")
            if url and not url.startswith("http"):
                url = "https://www.zillow.com" + url

            price_raw = item.get("price") or item.get("unformattedPrice")
            price = None
            if isinstance(price_raw, int):
                price = price_raw
            elif isinstance(price_raw, str):
                m = re.search(r"[\d,]+", price_raw.replace("$", ""))
                if m:
                    try:
                        price = int(m.group().replace(",", ""))
                    except ValueError:
                        pass

            hdp = item.get("hdpData", {}).get("homeInfo", {})

            results.append({
                "title":       item.get("address") or hdp.get("streetAddress"),
                "price":       price,
                "location":    f"{item.get('addressCity', '')}, {item.get('addressState', '')}".strip(", ") or None,
                "address":     item.get("address") or hdp.get("streetAddress"),
                "url":         url,
                "posted_date": None,
                "bedrooms":    item.get("beds") or hdp.get("bedrooms"),
                "bathrooms":   item.get("baths") or hdp.get("bathrooms"),
                "square_feet": item.get("area") or hdp.get("livingArea"),
                "description": None,
                "zpid":        item.get("zpid"),          # useful for enrichment later
                "zestimate":   hdp.get("zestimate"),      # free valuation data
                "home_type":   hdp.get("homeType"),
            })
        return results

    # ── Fallback: anchor heuristics (CAPTCHA wall / missing JSON) ────────────
    logger.warning("[zillow] Falling back to anchor heuristics — likely a bot-wall page")
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.select('a[href*="/homedetails/"], a[href*="/b/"]')
    seen: set[str] = set()
    for a in anchors:
        href = a.get("href", "")
        url = ("https://www.zillow.com" + href) if href.startswith("/") else href
        if url in seen:
            continue
        seen.add(url)
        # Climb up to find a price token
        price = None
        node = a
        for _ in range(5):
            if node is None:
                break
            txt = node.get_text(" ", strip=True)
            m = re.search(r"\$\s*([\d,]+)", txt)
            if m:
                try:
                    price = int(m.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass
            node = node.parent
        results.append({
            "title": a.get_text(strip=True) or None,
            "price": price,
            "location": None,
            "address": None,
            "url": url,
            "posted_date": None,
            "bedrooms": None,
            "bathrooms": None,
            "square_feet": None,
            "description": None,
        })
    return results


def parse_zillow_detail(html: str) -> dict:
    """
    Extract detail fields from a Zillow property page.
    Prefers __NEXT_DATA__; falls back to meta-tag heuristics.
    """
    nd = _extract_next_data(html)
    if nd:
        props = nd.get("props", {}).get("pageProps", {})
        home = (
            props.get("componentProps", {}).get("gdpClientCache")
            or props.get("initialReduxState", {}).get("gdp", {}).get("homeInfo")
            or {}
        )
        # gdpClientCache is a stringified JSON inside the JSON
        if isinstance(home, str):
            try:
                cache = json.loads(home)
                # The cache is keyed by a query string; grab the first value
                home = next(iter(cache.values()), {}).get("property", {})
            except (json.JSONDecodeError, StopIteration):
                home = {}

        if home:
            return {
                "description":  home.get("description"),
                "address":      home.get("streetAddress"),
                "bedrooms":     home.get("bedrooms"),
                "bathrooms":    home.get("bathrooms"),
                "square_feet":  home.get("livingArea"),
                "zestimate":    home.get("zestimate"),
                "year_built":   home.get("yearBuilt"),
                "lot_size":     home.get("lotSize"),
                "home_type":    home.get("homeType"),
            }

    # ── Fallback: meta tags ───────────────────────────────────────────────────
    soup = BeautifulSoup(html, "lxml")
    description = None
    meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
    if meta:
        description = meta.get("content", "").strip()

    address = None
    og_title = soup.select_one('meta[property="og:title"]')
    if og_title:
        address = og_title.get("content", "").strip()

    text = soup.get_text(" ", strip=True).lower()
    bedrooms = bathrooms = square_feet = None
    m_bd = re.search(r"(\d+)\s*bd\b", text)
    m_ba = re.search(r"(\d+(?:\.\d+)?)\s*ba\b", text)
    m_sq = re.search(r"(\d[\d,]*)\s*(?:sqft|sq ft|sq\. ft)\b", text)
    if m_bd:
        try: bedrooms = int(m_bd.group(1))
        except ValueError: pass
    if m_ba:
        try: bathrooms = float(m_ba.group(1))
        except ValueError: pass
    if m_sq:
        try: square_feet = int(m_sq.group(1).replace(",", ""))
        except ValueError: pass

    return {
        "description": description,
        "address": address,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "square_feet": square_feet,
    }