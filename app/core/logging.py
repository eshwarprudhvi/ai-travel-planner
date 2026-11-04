import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(debug: bool) -> None:
    """Configure the root logger once at startup.

    Rules for anything logged in this app:
    - Never log passwords, API keys, tokens or database URLs with credentials.
    - Log IDs (trip_id), not personal data (preferences, names).
    """
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()  # avoid duplicate lines if called twice (e.g. reload)
    root.addHandler(handler)
    root.setLevel(level)

    # Third-party libraries are noisy at DEBUG; keep them at WARNING.
    # SQL statements are still printed when the engine uses echo=True.
    for noisy in ("asyncio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
