"""Shared logging setup: a daily-rotating file handler, 14 days retained.

Both entry points (main.py, and mqtt_subscriber.py when run standalone) call
configure_logging() once at startup instead of using logging.basicConfig
directly, so there's a single place controlling where logs go.
"""

import logging
from logging.handlers import TimedRotatingFileHandler

LOG_FILE = "sensors_tft35.log"


def configure_logging(level=logging.INFO):
    handler = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler])
