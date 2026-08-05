"""Fetches the UCI Online Retail dataset if it isn't already cached locally."""

import io
import logging
import zipfile
from pathlib import Path

import requests

DATASET_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
RAW_FILE = RAW_DIR / "Online Retail.xlsx"

logger = logging.getLogger(__name__)


def ensure_raw_data() -> Path:
    """Returns the path to the raw dataset, downloading it if needed."""
    if RAW_FILE.exists():
        logger.info("Raw dataset already present at %s", RAW_FILE)
        return RAW_FILE

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading dataset from %s", DATASET_URL)
    response = requests.get(DATASET_URL, timeout=60)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(RAW_DIR)

    if not RAW_FILE.exists():
        extracted = list(RAW_DIR.glob("*.xlsx"))
        if not extracted:
            raise FileNotFoundError("Download succeeded but no .xlsx file was found in the archive")
        extracted[0].rename(RAW_FILE)

    logger.info("Dataset downloaded to %s", RAW_FILE)
    return RAW_FILE


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_raw_data()
