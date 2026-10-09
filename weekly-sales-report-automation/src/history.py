"""SQLite history of the actual results each prediction run has seen.

The results sheet can return a movie's actual gross blank even after it was filled in before
(its cells pull live from a website). Remembering every value seen lets a run fall back on the
last known result instead of dropping the movie from the scores.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from .predictions import title_key

HISTORY_DB = Path(__file__).resolve().parent.parent / "data" / "prediction_history.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS actual_results (
    title_key   TEXT PRIMARY KEY,   -- title with case, spacing and punctuation removed
    title       TEXT NOT NULL,      -- title as last written in the results sheet
    actual      REAL NOT NULL,      -- latest actual worldwide gross seen
    first_seen  TEXT NOT NULL,      -- date (YYYY-MM-DD) a result was first seen
    last_seen   TEXT NOT NULL       -- date the result was last seen in the sheet
);
"""


def connect(path: Path = HISTORY_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def fill_from_history(actuals: pd.DataFrame, conn: sqlite3.Connection) -> tuple[pd.DataFrame, list[str]]:
    """Fills blank actuals with the last known result. Returns (filled actuals, titles that were filled)."""
    known = dict(conn.execute("SELECT title_key, actual FROM actual_results").fetchall())
    keys = actuals["title"].map(title_key)
    restore = actuals["actual"].isna() & keys.isin(known)
    filled = actuals.copy()
    filled.loc[restore, "actual"] = keys[restore].map(known)
    return filled, list(actuals.loc[restore, "title"])


def save_actuals(actuals: pd.DataFrame, conn: sqlite3.Connection, seen_on: date) -> int:
    """Records every actual result present in the sheet. Returns how many were saved."""
    day = seen_on.isoformat()
    rows = [
        (title_key(title), title, float(actual), day, day)
        for title, actual in zip(actuals["title"], actuals["actual"])
        if pd.notna(actual) and actual > 0
    ]
    conn.executemany(
        """
        INSERT INTO actual_results (title_key, title, actual, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (title_key) DO UPDATE SET
            title = excluded.title,
            actual = excluded.actual,
            last_seen = excluded.last_seen
        """,
        rows,
    )
    conn.commit()
    return len(rows)
