BASE_URL_MILWAUKEE = "https://milwaukee.craigslist.org/search/rea"
BASE_URL_COLUMBUS = "https://columbus.craigslist.org/search/rea"

# Stealth headers (more realistic browser UA)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PAGES = 5
MAX_LISTINGS = 20  # Stop collecting once this many relevant listings are found
REQUEST_DELAY = 2  # seconds between pages

# Price filter (assignment requirement)
MIN_PRICE = 50000
MAX_PRICE = 250000

# Keywords to match in title/description
KEYWORDS = [
    "single family",
    "single-family",
    "single family home",
    "investment",
    "rental",
    "duplex",
    "multi-family",
    "multifamily",
]

# Property types to allow
PROPERTY_TYPES = [
    "single family",
    "single-family",
    "sfh",
    "duplex",
    "multi-family",
    "multifamily",
]

# Optional proxies — set to dict like {"https": "http://user:pass@host:port"} or None
PROXIES = {"https": "http://uiqydusn:ayprrg8k3u13@31.59.20.176:6754"}

# Enable lightweight stealth init scripts
USE_STEALTH = True