"""
Entry point — scrapes Zillow (Ohio, ≤$300k) and optionally Craigslist cities.

Usage
-----
    python run.py                        # scrape Zillow only (default)
    python run.py --source craigslist    # scrape Milwaukee + Columbus Craigslist
    python run.py --source all           # scrape everything
    python run.py --max-pages 3          # override MAX_PAGES from config
    python run.py --csv data/out.csv     # custom output path
"""
from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.scraper import CraigslistScraper, ZillowScraper
from src.exporter import export_csv, export_json, print_summary
from src.config import BASE_URL_ZILLOW, MAX_PAGES
from src.utils import setup_logger
from loguru import logger

CRAIGSLIST_URLS = {
    "milwaukee": "https://milwaukee.craigslist.org/search/rea",
    "columbus":  "https://columbus.craigslist.org/search/rea",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Real-estate lead scraper")
    parser.add_argument(
        "--source",
        choices=["zillow", "craigslist", "all"],
        default="zillow",
        help="Which source(s) to scrape (default: zillow)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Override MAX_PAGES from config (currently {MAX_PAGES})",
    )
    parser.add_argument(
        "--csv",
        default="data/listings.csv",
        help="Output CSV path (default: data/listings.csv)",
    )
    parser.add_argument(
        "--json",
        default="data/listings.json",
        help="Output JSON path (default: data/listings.json)",
    )
    return parser.parse_args()


def apply_max_pages(max_pages: int | None):
    """Mutate config at runtime if the user passed --max-pages."""
    if max_pages is not None:
        import src.config as cfg
        cfg.MAX_PAGES = max_pages
        logger.info(f"MAX_PAGES overridden → {max_pages}")


def scrape_zillow() -> list[dict]:
    logger.info("── Scraping Zillow (Ohio ≤$300k) ──")
    return ZillowScraper(BASE_URL_ZILLOW).scrape()


def scrape_craigslist() -> list[dict]:
    results: list[dict] = []
    for city, url in CRAIGSLIST_URLS.items():
        logger.info(f"── Scraping Craigslist: {city} ──")
        results += CraigslistScraper(url).scrape()
    return results


def deduplicate(data: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique = []
    for item in data:
        url = item.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)
    return unique


def main():
    args = parse_args()
    setup_logger()
    apply_max_pages(args.max_pages)

    logger.info(f"Starting scraper | source={args.source} | max_pages={args.max_pages or MAX_PAGES}")

    all_data: list[dict] = []

    if args.source in ("zillow", "all"):
        all_data += scrape_zillow()

    if args.source in ("craigslist", "all"):
        all_data += scrape_craigslist()

    if not all_data:
        logger.warning("No listings collected. Check config, proxy, and network.")
        sys.exit(1)

    unique_data = deduplicate(all_data)
    logger.info(f"Total unique listings: {len(unique_data)}")

    export_csv(unique_data, args.csv)
    export_json(unique_data, args.json)
    print_summary(unique_data)
    print(f"✓ Scraped {len(unique_data)} listings → {args.csv} / {args.json}")


if __name__ == "__main__":
    main()