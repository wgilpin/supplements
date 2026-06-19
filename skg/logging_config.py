"""Logging setup — to BOTH the terminal and a rotating file (constitution).

Call `setup_logging()` once at an app entrypoint (e.g. the FastAPI app). Library
modules just use `logging.getLogger(__name__)` and inherit these handlers.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Wire the root logger with a terminal handler and a rotating file handler.
    Idempotent — safe to call more than once (e.g. under reload)."""
    global _configured
    if _configured:
        return

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        config.LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    _configured = True
    root.info("logging configured -> terminal + %s", config.LOG_PATH)
