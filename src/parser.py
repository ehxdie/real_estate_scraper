from __future__ import annotations

import re
from bs4 import BeautifulSoup


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_from_housing(housing_text: str):
    """Parse beds / baths / sqft from a Craigslist housing-info string."""
    beds = baths = sqft = None
    if not housing_text:
        return beds, baths, sqft

    m_b = re.search(r"(\d+)\s*br", housing_text, re.I)
    m_ba = re.search(r"(\d+(?:\.\d+)?)\s*ba", housing_text, re.I)
    m_sq = re.search(r"(\d[\d,]*)\s*ft", housing_text, re.I)

    if m_b:
        beds = int(m_b.group(1))
    if m_ba:
        try:
            baths = float(m_ba.group(1))
        except ValueError:
            pass
    if m_sq:
        try:
            sqft = int(m_sq.group(1).replace(",", ""))
        except ValueError:
            pass

    return beds, baths, sqft


# ── listing-page parser ───────────────────────────────────────────────────────

def parse_listing_page(html: str) -> list[dict]:
    """
    Parse the Craigslist search-results page.

    Craigslist serves two layouts depending on region / A-B test:
      • Classic  → li.result-row
      • New (2023+) → li[data-pid]  inside  ol.cl-search-view-mode-list

    We try both and merge.
    """
    soup = BeautifulSoup(html, "lxml")
    data = []

    from loguru import logger

    # ── STATIC fallback layout (served when JS doesn't execute, e.g. via proxy) ──
    # selector: li.cl-static-search-result
    static_items = soup.select("li.cl-static-search-result")
    logger.debug(f"[parser] static-layout items (li.cl-static-search-result): {len(static_items)}")

    if static_items:
        for item in static_items:
            anchor = item.select_one("a")
            title_el = item.select_one(".title")
            price_el = item.select_one(".price")
            location_el = item.select_one(".location")

            data.append({
                "title":       title_el.get_text(strip=True) if title_el else (anchor.get_text(strip=True) if anchor else None),
                "price":       price_el.get_text(strip=True) if price_el else None,
                "location":    location_el.get_text(strip=True) if location_el else None,
                "url":         anchor.get("href") if anchor else None,
                "posted_date": None,  # not present in static layout
                "bedrooms":    None,
                "bathrooms":   None,
                "square_feet": None,
                "description": None,
                "address":     None,
            })
        return data

    # ── NEW layout ────────────────────────────────────────────────────────────
    new_items = soup.select("li[data-pid]")
    logger.debug(f"[parser] new-layout items (li[data-pid]): {len(new_items)}")
    classic_items_check = soup.select("li.result-row")
    logger.debug(f"[parser] classic-layout items (li.result-row): {len(classic_items_check)}")
    for item in new_items:
        title_el = item.select_one("a.cl-app-anchor, a[data-id]")
        if not title_el:
            title_el = item.select_one("a")

        price_el = item.select_one(".priceinfo, .price")
        hood_el = item.select_one(".supertitle, .meta .separator ~ span")
        date_el = item.select_one("time")
        housing_el = item.select_one(".housing")

        beds = baths = sqft = None
        if housing_el:
            beds, baths, sqft = _extract_from_housing(housing_el.get_text(" "))

        data.append({
            "title":       title_el.get_text(strip=True) if title_el else None,
            "price":       price_el.get_text(strip=True) if price_el else None,
            "location":    hood_el.get_text(strip=True).strip("() ") if hood_el else None,
            "url":         title_el.get("href") if title_el else None,
            "posted_date": date_el.get("datetime") if date_el else None,
            "bedrooms":    beds,
            "bathrooms":   baths,
            "square_feet": sqft,
            "description": None,
            "address":     None,
        })

    if data:
        return data

    # ── CLASSIC layout ────────────────────────────────────────────────────────
    classic_items = soup.select("li.result-row")
    for item in classic_items:
        title_el = item.select_one("a.result-title")
        price_el = item.select_one(".result-price")
        hood_el  = item.select_one(".result-hood")
        date_el  = item.select_one("time")
        housing_el = item.select_one(".housing")

        beds = baths = sqft = None
        if housing_el:
            beds, baths, sqft = _extract_from_housing(housing_el.get_text(" "))

        data.append({
            "title":       title_el.get_text(strip=True) if title_el else None,
            "price":       price_el.get_text(strip=True) if price_el else None,
            "location":    hood_el.get_text(strip=True).strip("() ") if hood_el else None,
            "url":         title_el.get("href") if title_el else None,
            "posted_date": date_el.get("datetime") if date_el else None,
            "bedrooms":    beds,
            "bathrooms":   baths,
            "square_feet": sqft,
            "description": None,
            "address":     None,
        })

    return data


# ── detail-page parser ────────────────────────────────────────────────────────

def parse_listing_detail(html: str) -> dict:
    """
    Parse a single Craigslist listing detail page.
    Returns a dict with description, address, bedrooms, bathrooms, square_feet.
    """
    soup = BeautifulSoup(html, "lxml")

    # Description — strip the "QR Code Link to This Post" boilerplate
    desc_el = soup.select_one("#postingbody")
    description = None
    if desc_el:
        # Remove the "QR Code" notice that Craigslist injects
        for tag in desc_el.select(".print-qrcode-container, .printqrcode"):
            tag.decompose()
        description = desc_el.get_text("\n").strip()

    # Address
    addr_el = soup.select_one("div.mapaddress")
    if not addr_el:
        addr_el = soup.select_one(".postingtitletext .mapaddress")
    address = addr_el.get_text(strip=True) if addr_el else None

    # Beds / baths / sqft from attribute groups
    bedrooms = bathrooms = square_feet = None
    for pg in soup.select("p.attrgroup, .attrgroup"):
        for span in pg.select("span"):
            txt = span.get_text(" ").lower()
            b, ba, s = _extract_from_housing(txt)
            if b and bedrooms is None:
                bedrooms = b
            if ba and bathrooms is None:
                bathrooms = ba
            if s and square_feet is None:
                square_feet = s

    # Fallback: scan the whole posting title block
    if bedrooms is None or bathrooms is None:
        title_block = soup.select_one(".postingtitletext, .titletextonly")
        if title_block:
            b, ba, s = _extract_from_housing(title_block.get_text(" ").lower())
            if b and bedrooms is None:
                bedrooms = b
            if ba and bathrooms is None:
                bathrooms = ba
            if s and square_feet is None:
                square_feet = s

    return {
        "description": description,
        "address":     address,
        "bedrooms":    bedrooms,
        "bathrooms":   bathrooms,
        "square_feet": square_feet,
    }