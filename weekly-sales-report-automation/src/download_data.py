"""Fetches the UCI Online Retail dataset if it isn't already cached locally."""

import io
import logging
import zipfile
from pathlib import Path

import requests
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

DATASET_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
RAW_FILE = RAW_DIR / "Online Retail.xlsx"

logger = logging.getLogger(__name__)


def _download_with_progress(url: str) -> bytes:
    """Streams the dataset download and renders a progress bar in the console."""
    with requests.get(url, timeout=60, stream=True) as response:
        response.raise_for_status()
        total_size = int(response.headers.get("Content-Length", 0))

        chunks = []
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("Downloading dataset", total=total_size or None)
            for chunk in response.iter_content(chunk_size=1024 * 64):
                chunks.append(chunk)
                progress.update(task, advance=len(chunk))

        return b"".join(chunks)


def ensure_raw_data() -> Path:
    """Returns the path to the raw dataset, downloading it if needed."""
    if RAW_FILE.exists():
        logger.info("Raw dataset already present at %s", RAW_FILE)
        return RAW_FILE

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading dataset from %s", DATASET_URL)
    content = _download_with_progress(DATASET_URL)

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
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
