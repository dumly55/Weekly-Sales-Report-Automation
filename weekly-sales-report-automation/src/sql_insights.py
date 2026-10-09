"""Runs the analysis queries in the sql/ folder against the prediction history database."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@dataclass
class Insight:
    name: str  # file name without .sql, e.g. "02_median_miss"
    question: str  # from the file's "-- Question:" line
    table: pd.DataFrame


def _question(sql: str, fallback: str) -> str:
    for line in sql.splitlines():
        if line.startswith("-- Question:"):
            return line.removeprefix("-- Question:").strip()
    return fallback


def run_insights(conn: sqlite3.Connection, sql_dir: Path = SQL_DIR) -> list[Insight]:
    """Runs every .sql file in `sql_dir`, in file-name order."""
    insights = []
    for path in sorted(sql_dir.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        insights.append(Insight(path.stem, _question(sql, path.stem), pd.read_sql_query(sql, conn)))
    return insights
