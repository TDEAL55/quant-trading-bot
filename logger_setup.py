import logging
from pathlib import Path


LOG_FILE = Path(__file__).resolve().parent / "bot.log"


def setup_logger(name="trading_bot", log_file=None):
    """Create and return a logger that writes to bot.log."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_file or LOG_FILE)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
