import os
import time
from loguru import logger


def setup_logger():
    """Configure loguru to also write to a rotating log file."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "scraper.log")
    logger.add(
        log_path,
        rotation="1 MB",
        retention="7 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    )


def delay(seconds: float):
    """Simple wrapper so callers don't have to import time directly."""
    time.sleep(seconds)