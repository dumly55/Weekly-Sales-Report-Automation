"""SQLite history of prediction runs: every actual result seen, and every run's scores.

The results sheet can return a movie's actual gross blank even after it was filled in before
(its cells pull live from a website). Remembering every value seen lets a run fall back on the
last known result instead of dropping the movie from the scores. Storing each run's scores
builds up a history to analyze with SQL (see the sql/ folder).

Each run works on an in-memory copy of the database file, so even runs that don't save (local
runs, the desktop app) can query a history that includes their own results.
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

CREATE TABLE IF NOT EXISTS movie_scores (
    run_date      TEXT NOT NULL,    -- date of the run (YYYY-MM-DD)
    movie         TEXT NOT NULL,
    release_date  TEXT,             -- YYYY-MM-DD, if the results sheet has one
    forecaster    TEXT NOT NULL,    -- 'Predictions' or 'Tracker Projection'
    forecast      REAL NOT NULL,    -- forecast worldwide gross
    actual        REAL,             -- final worldwide gross; NULL until the run is complete
    status        TEXT NOT NULL,    -- 'Final', 'In theaters', 'Upcoming' or 'Awaiting result'
    pct_error     REAL,             -- (forecast - actual) / actual * 100; NULL unless Final
    accuracy_band TEXT,             -- 'Nailed it', 'Close', 'Off' or 'Way off'; NULL unless Final
    gross_so_far  REAL,             -- in theaters only: worldwide gross so far
    days_in_theaters   INTEGER,     -- in theaters only
    days_until_release INTEGER,     -- upcoming only
    PRIMARY KEY (run_date, movie, forecaster)
);
"""

# Columns added to movie_scores after it was first created, for upgrading older history files.
_ADDED_COLUMNS = {"gross_so_far": "REAL", "days_in_theaters": "INTEGER", "days_until_release": "INTEGER"}


def _upgrade(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(movie_scores)")}
    for column, sql_type in _ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE movie_scores ADD COLUMN {column} {sql_type}")
    # Older runs labelled every movie with a result 'Released'.
    conn.execute("UPDATE movie_scores SET status = 'Final' WHERE status = 'Released'")
    conn.commit()


def open_history(path: Path = HISTORY_DB) -> sqlite3.Connection:
    """Returns an in-memory copy of the history file (or an empty history if there's no file)."""
    conn = sqlite3.connect(":memory:")
    if path.exists():
        disk = sqlite3.connect(path)
        try:
            disk.backup(conn)
        finally:
            disk.close()
    conn.executescript(SCHEMA)
    _upgrade(conn)
    return conn


def write_history(conn: sqlite3.Connection, path: Path = HISTORY_DB) -> None:
    """Saves the in-memory history back to the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    disk = sqlite3.connect(path)
    try:
        conn.backup(disk)
    finally:
        disk.close()


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
        for title, actual in zip(actuals["title"], actuals["actual"], strict=True)
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


SCORE_COLUMNS = [
    "movie", "release_date", "forecaster", "forecast", "actual", "status", "pct_error", "accuracy_band",
    "gross_so_far", "days_in_theaters", "days_until_release",
]


def _sql_value(value):
    """Blank cells become NULL, and pandas/numpy numbers become plain Python numbers SQLite can store."""
    if pd.isna(value) or value == "":
        return None
    return value.item() if hasattr(value, "item") else value


def save_scores(scores: pd.DataFrame, conn: sqlite3.Connection, run_date: date) -> int:
    """Records this run's scores (rows shaped like `prediction_report.tableau_rows`; missing columns
    are stored as NULL), replacing any earlier run from the same day. Returns how many rows were saved."""
    day = run_date.isoformat()
    rows = [
        (day, *(_sql_value(v) for v in row))
        for row in scores.reindex(columns=SCORE_COLUMNS).itertuples(index=False)
    ]
    conn.execute("DELETE FROM movie_scores WHERE run_date = ?", (day,))
    conn.executemany(
        f"INSERT OR REPLACE INTO movie_scores (run_date, {', '.join(SCORE_COLUMNS)}) "
        f"VALUES ({', '.join('?' * (len(SCORE_COLUMNS) + 1))})",
        rows,
    )
    conn.commit()
    return len(rows)
