"""
Entry point — scrapes Milwaukee and Columbus Craigslist real-estate listings,
then exports combined results to CSV and JSON.

Usage
-----
    python run.py                   # scrape both cities
    python run.py --city milwaukee  # single city
    python run.py --city columbus
    python run.py --max-pages 3     # override MAX_PAGES from config
"""
from __future__ import annotations

import argparse
import sys
import os

# Allow `python run.py` from the project root without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from src.scraper import CraigslistScraper
from src.exporter import export_csv, export_json, print_summary
from src.config import BASE_URL_MILWAUKEE, BASE_URL_COLUMBUS, MAX_PAGES
from src.utils import setup_logger
from loguru import logger


def parse_args():
    parser = argparse.ArgumentParser(description="Craigslist real-estate scraper")
    parser.add_argument(
        "--city",
        choices=["milwaukee", "columbus", "both"],
        default="both",
        help="Which city to scrape (default: both)",
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


def run_scraper(base_url: str, max_pages: int | None = None) -> list[dict]:
    import src.config as cfg
    if max_pages is not None:
        cfg.MAX_PAGES = max_pages          # runtime override

    scraper = CraigslistScraper(base_url)
    return scraper.scrape()


def main():
    args = parse_args()
    setup_logger()

    logger.info(f"Starting scraper | city={args.city} | max_pages={args.max_pages or MAX_PAGES}")

    all_data: list[dict] = []

    if args.city in ("milwaukee", "both"):
        logger.info("── Scraping Milwaukee ──")
        all_data += run_scraper(BASE_URL_MILWAUKEE, args.max_pages)

    if args.city in ("columbus", "both"):
        logger.info("── Scraping Columbus ──")
        all_data += run_scraper(BASE_URL_COLUMBUS, args.max_pages)

    if not all_data:
        logger.warning("No listings collected. Check your config and network.")
        sys.exit(1)

    # Deduplicate across cities (same URL may appear on both searches)
    seen: set[str] = set()
    unique_data = []
    for item in all_data:
        url = item.get("url", "")
        if url not in seen:
            seen.add(url)
            unique_data.append(item)

    logger.info(f"Total unique listings: {len(unique_data)}")

    export_csv(unique_data, args.csv)
    export_json(unique_data, args.json)
    print_summary(unique_data)

    print(f"✓ Scraped {len(unique_data)} listings → {args.csv} / {args.json}")


if __name__ == "__main__":
    main()