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
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

DATASET_URL = "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
RAW_FILE = RAW_DIR / "Online Retail.xlsx"

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


def _stream_download(url: str, on_progress: Callable[[int, int], None]) -> bytes:
    """Streams a download, calling `on_progress(bytes_so_far, total_bytes)` per chunk (total is 0 if unknown)."""
    with requests.get(url, timeout=60, stream=True) as response:
        response.raise_for_status()
        total_size = int(response.headers.get("Content-Length", 0))

        chunks = []
        downloaded = 0
        for chunk in response.iter_content(chunk_size=1024 * 256):
            chunks.append(chunk)
            downloaded += len(chunk)
            on_progress(downloaded, total_size)

        return b"".join(chunks)


def _download_with_console_bar(url: str) -> bytes:
    with Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Downloading dataset", total=None)
        return _stream_download(url, lambda done, total: progress.update(task, completed=done, total=total or None))


def _download_with_status_messages(url: str, status_callback: StatusCallback) -> bytes:
    # The GUI runs under pythonw.exe with no stdout, so it can't use rich's console bar.
    def report(done: int, total: int) -> None:
        done_mb = done / 1_048_576
        if total:
            status_callback(f"Downloading dataset... {done_mb:.1f}/{total / 1_048_576:.1f} MB")
        else:
            status_callback(f"Downloading dataset... {done_mb:.1f} MB")

    return _stream_download(url, report)


def ensure_raw_data(status_callback: StatusCallback | None = None) -> Path:
    """Returns the path to the cached built-in demo dataset, downloading it if needed."""
    if RAW_FILE.exists():
        logger.info("Raw dataset already present at %s", RAW_FILE)
        return RAW_FILE

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading dataset from %s", DATASET_URL)
    if status_callback:
        content = _download_with_status_messages(DATASET_URL, status_callback)
    else:
        content = _download_with_console_bar(DATASET_URL)

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
    return _read_spreadsheet(
        io.BytesIO(response.content),
        is_csv=looks_like_csv,
        error_message=(
            "Couldn't read that link as a spreadsheet. Make sure it's a direct link to a "
            "CSV or Excel file, or a Google Sheet shared as \"Anyone with the link can view\"."
        ),
    )


def load_local_dataset(path: Path, status_callback: StatusCallback | None = None) -> pd.DataFrame:
    """Reads a user-selected local CSV/Excel file (e.g. via the GUI's file picker)
    and parses it into a DataFrame. No download involved -- just local file I/O.
    """
    notify = status_callback or (lambda _msg: None)
    notify(f"Reading {path.name}...")
    return _read_spreadsheet(
        path,
        is_csv=path.suffix.lower() == ".csv",
        error_message=f"Couldn't read '{path.name}' as a spreadsheet. Make sure it's a valid CSV or Excel file.",
    )


def _read_spreadsheet(source: Path | io.BytesIO, is_csv: bool, error_message: str) -> pd.DataFrame:
    try:
        return pd.read_csv(source) if is_csv else pd.read_excel(source)
    except Exception as exc:
        raise ValueError(error_message) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_raw_data()
