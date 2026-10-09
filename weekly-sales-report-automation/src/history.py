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
    actual        REAL,             -- actual worldwide gross; NULL until released
    status        TEXT NOT NULL,    -- 'Released' or 'Upcoming'
    pct_error     REAL,             -- (forecast - actual) / actual * 100; NULL until released
    accuracy_band TEXT,             -- 'Nailed it', 'Close', 'Off' or 'Way off'; NULL until released
    PRIMARY KEY (run_date, movie, forecaster)
);
"""


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


def save_scores(scores: pd.DataFrame, conn: sqlite3.Connection, run_date: date) -> int:
    """Records this run's scores (rows shaped like `prediction_report.tableau_rows`), replacing any
    earlier run from the same day. Returns how many rows were saved."""
    day = run_date.isoformat()

    def value(v):
        return None if pd.isna(v) or v == "" else v

    rows = [
        (day, r.movie, value(r.release_date), r.forecaster, float(r.forecast), value(r.actual), r.status,
         value(r.pct_error), value(r.accuracy_band))
        for r in scores.itertuples(index=False)
    ]
    conn.execute("DELETE FROM movie_scores WHERE run_date = ?", (day,))
    conn.executemany(
        """
        INSERT OR REPLACE INTO movie_scores
            (run_date, movie, release_date, forecaster, forecast, actual, status, pct_error, accuracy_band)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)
