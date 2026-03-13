# Real Estate Web Scraper

A small scraper that collects Craigslist real-estate listings (default targets: Milwaukee, WI and Columbus, OH). The scraper parses search-result pages and listing detail pages to produce enriched records and exports CSV/JSON.

## What it does

- Scrapes Craigslist real-estate listings for configured base URLs.
- Fetches listing-search pages and listing-detail pages to collect additional fields.
- Filters listings by price, keywords and property types; supports proxies and lightweight stealth.
- Writes progressive CSV output during a run and final CSV/JSON exports.

## Scraped data fields

- `title` — listing title (string)
- `price` — listing price, normalized to integer (USD)
- `location` — neighborhood / listing location text (string)
- `address` — full listing address when available (string or null)
- `bedrooms` — number of bedrooms (int or null)
- `bathrooms` — number of bathrooms (float or null)
- `square_feet` — size in square feet (int or null)
- `url` — full listing URL (string)
- `description` — full posting body text from the detail page (string or null)
- `posted_date` — listing timestamp (ISO datetime string or null)

## Features

- Pagination (up to `MAX_PAGES`, ~120 results per page)
- Price filtering (`MIN_PRICE` / `MAX_PRICE` in `src/config.py`)
- Keyword and property-type filtering (title → description → address)
- Duplicate detection (visited URL set)
- Retry logic and error handling (tenacity + Playwright)
- Optional proxy support and lightweight stealth init script
- Progressive CSV writing to `data/listings_progress.csv`
- Final CSV/JSON export: `data/listings.csv`, `data/listings.json`
- Structured logging via `loguru`

## Important files

- `run.py` — entrypoint to start scraping
- `src/config.py` — defaults and filters (base URLs, price range, keywords, proxies)
- `src/scraper.py` — main scraping logic and page fetching
- `src/parser.py` — listing-list and detail HTML parsers
- `src/exporter.py` — CSV/JSON export and summary
- Output directory: `data/`

## Quick start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run:

```bash
python run.py
```

## Customization

- Change target cities or base URLs in `src/config.py` (`BASE_URL_MILWAUKEE`, `BASE_URL_COLUMBUS`).
- Adjust price filters via `MIN_PRICE` and `MAX_PRICE` in `src/config.py`.
- Update `KEYWORDS` and `PROPERTY_TYPES` in `src/config.py` to tune relevance.

## Notes

- The parser extracts beds/baths/area from Craigslist "housing" strings and from the detail page attribute groups.
- `price` is normalized to an integer; other numeric fields may be null if unavailable.
- Progressive export ensures partial results are saved to `data/listings_progress.csv` during runs.
