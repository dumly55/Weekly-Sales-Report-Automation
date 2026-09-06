"""Fetches the raw transaction data: the cached UCI demo dataset, a
user-supplied link (a direct CSV/Excel file, or a public Google Sheet), or a
local file the user picked from disk.
"""

import io
import logging
import zipfile
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

from .clean import validate_raw_columns

DATASET_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
RAW_FILE = RAW_DIR / "Online Retail.xlsx"

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


def _download_with_progress(url: str) -> bytes:
    """Streams the dataset download and renders a progress bar in the console.

    Only used when there's a real console to draw into (the CLI path). The GUI
    path uses `_download_silently` instead, since a windowed app launched via
    pythonw.exe has no stdout for rich to write to.
    """
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


def _download_silently(url: str, status_callback: StatusCallback) -> bytes:
    """Streams a download while reporting progress via a plain callback instead
    of a console progress bar (used by the GUI)."""
    with requests.get(url, timeout=60, stream=True) as response:
        response.raise_for_status()
        total_size = int(response.headers.get("Content-Length", 0))

        chunks = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=1024 * 256):
            chunks.append(chunk)
            downloaded += len(chunk)
            downloaded_mb = downloaded / 1_048_576
            if total_size:
                status_callback(f"Downloading dataset... {downloaded_mb:.1f}/{total_size / 1_048_576:.1f} MB")
            else:
                status_callback(f"Downloading dataset... {downloaded_mb:.1f} MB")

        return b"".join(chunks)


def ensure_raw_data(status_callback: StatusCallback | None = None) -> Path:
    """Returns the path to the cached built-in demo dataset, downloading it if needed."""
    if RAW_FILE.exists():
        logger.info("Raw dataset already present at %s", RAW_FILE)
        return RAW_FILE

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading dataset from %s", DATASET_URL)
    content = _download_silently(DATASET_URL, status_callback) if status_callback else _download_with_progress(DATASET_URL)

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(RAW_DIR)

    if not RAW_FILE.exists():
        extracted = list(RAW_DIR.glob("*.xlsx"))
        if not extracted:
            raise FileNotFoundError("Download succeeded but no .xlsx file was found in the archive")
        extracted[0].rename(RAW_FILE)

    logger.info("Dataset downloaded to %s", RAW_FILE)
    return RAW_FILE


def _normalize_google_sheet_url(url: str) -> str:
    """Rewrites a Google Sheets share link into its public CSV export link.

    Only works if the sheet is shared as "Anyone with the link can view" --
    there's no authentication here, so a private sheet will just fail to parse.
    """
    if "docs.google.com" not in url or "/spreadsheets/d/" not in url:
        return url

    sheet_id = url.split("/spreadsheets/d/")[1].split("/")[0]
    gid = "0"
    if "gid=" in url:
        gid = url.split("gid=")[1].split("&")[0].split("#")[0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_remote_dataset(url: str, status_callback: StatusCallback | None = None) -> pd.DataFrame:
    """Downloads a user-supplied data link and parses it into a DataFrame.

    Accepts a direct CSV/Excel file link, or a Google Sheet share link (rewritten
    to its CSV export endpoint). Never cached to disk, since a custom link is
    assumed to point at data that keeps changing.
    """
    notify = status_callback or (lambda _msg: None)
    resolved_url = _normalize_google_sheet_url(url)

    notify("Downloading data from the provided link...")
    try:
        response = requests.get(resolved_url, timeout=60)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise requests.exceptions.RequestException(f"Couldn't download from that link: {exc}") from exc

    content_type = response.headers.get("Content-Type", "")
    looks_like_csv = "csv" in content_type or resolved_url.lower().endswith(".csv")

    notify("Reading the downloaded data...")
    try:
        df = pd.read_csv(io.BytesIO(response.content)) if looks_like_csv else pd.read_excel(io.BytesIO(response.content))
    except Exception as exc:
        raise ValueError(
            "Couldn't read that link as a spreadsheet. Make sure it's a direct link to a "
            "CSV or Excel file, or a Google Sheet shared as \"Anyone with the link can view\"."
        ) from exc

    validate_raw_columns(df)
    return df


def load_local_dataset(path: Path, status_callback: StatusCallback | None = None) -> pd.DataFrame:
    """Reads a user-selected local CSV/Excel file (e.g. via the GUI's file picker)
    and parses it into a DataFrame. No download involved -- just local file I/O.
    """
    notify = status_callback or (lambda _msg: None)
    notify(f"Reading {path.name}...")

    try:
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    except Exception as exc:
        raise ValueError(f"Couldn't read '{path.name}' as a spreadsheet. Make sure it's a valid CSV or Excel file.") from exc

    validate_raw_columns(df)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_raw_data()
