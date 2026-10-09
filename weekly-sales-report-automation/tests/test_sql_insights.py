from contextlib import closing
from datetime import date

import pandas as pd
import pytest

from src.history import open_history, save_scores
from src.prediction_report import tableau_rows
from src.predictions import accuracy_stats
from src.sql_insights import SQL_DIR, run_insights
from tests.test_prediction_report import _result

# _result(): Predictions are off by +10% (Alpha), -50% (Bravo), +300% (Charlie); the tracker is exact.


@pytest.fixture
def insights(tmp_path):
    with closing(open_history(tmp_path / "history.sqlite")) as conn:
        save_scores(tableau_rows(_result()), conn, date(2026, 10, 5))
        return {i.name: i for i in run_insights(conn)}


def _row(table: pd.DataFrame, **match) -> dict:
    rows = table
    for column, value in match.items():
        rows = rows[rows[column] == value]
    assert len(rows) == 1, f"expected one row matching {match}, got {len(rows)}"
    return rows.iloc[0].to_dict()


def test_every_query_has_a_question_and_runs(insights):
    assert len(insights) == len(list(SQL_DIR.glob("*.sql"))) == 7
    for insight in insights.values():
        assert insight.question != insight.name, f"{insight.name} is missing its '-- Question:' line"


def test_accuracy_by_forecaster(insights):
    row = _row(insights["01_accuracy_by_forecaster"].table, forecaster="Predictions")
    assert (row["movies_scored"], row["avg_miss_pct"], row["within_25_pct"]) == (3, 120.0, 1)
    assert (row["too_high"], row["too_low"]) == (2, 1)


def test_median_matches_the_python_calculation(insights):
    table = insights["02_median_miss"].table
    for forecaster, column in [("Predictions", "predicted"), ("Tracker Projection", "projection")]:
        expected = accuracy_stats(_result().movies, column)["median_abs_pct"]
        assert _row(table, forecaster=forecaster)["median_miss_pct"] == pytest.approx(expected)


def test_median_with_an_odd_number_of_movies(tmp_path):
    misses = [5.0, -10.0, 30.0, 100.0, -2.0]
    scores = pd.DataFrame(
        {
            "movie": [f"Movie {i}" for i in range(5)],
            "release_date": None,
            "forecaster": "Predictions",
            "forecast": 1.0,
            "actual": 1.0,
            "status": "Released",
            "pct_error": misses,
            "accuracy_band": "",
        }
    )
    with closing(open_history(tmp_path / "history.sqlite")) as conn:
        save_scores(scores, conn, date(2026, 10, 5))
        table = {i.name: i for i in run_insights(conn)}["02_median_miss"].table
    assert table.loc[0, "median_miss_pct"] == 10.0


def test_accuracy_bands_in_order(insights):
    table = insights["03_accuracy_bands"].table
    predictions = table[table["forecaster"] == "Predictions"]
    assert list(predictions["accuracy_band"]) == ["Nailed it", "Off", "Way off"]
    assert list(predictions["titles"]) == ["Alpha", "Bravo", "Charlie"]


def test_head_to_head(insights):
    assert _row(insights["04_head_to_head"].table, closer="Tracker Projection")["movies"] == 3


def test_accuracy_by_release_month(insights):
    row = _row(insights["05_accuracy_by_release_month"].table, release_month="2026-01")
    assert (row["movies"], row["avg_miss_pct"]) == (3, 120.0)


def test_biggest_misses(insights):
    table = insights["06_biggest_misses"].table
    assert _row(table, forecaster="Predictions", miss_rank=1)["movie"] == "Charlie"


def test_accuracy_over_time_compares_with_the_previous_run(tmp_path):
    first = tableau_rows(_result())
    second = first.copy()
    second.loc[(second["movie"] == "Charlie") & (second["forecaster"] == "Predictions"), "pct_error"] = 150.0
    with closing(open_history(tmp_path / "history.sqlite")) as conn:
        save_scores(first, conn, date(2026, 10, 5))
        save_scores(second, conn, date(2026, 10, 12))
        table = {i.name: i for i in run_insights(conn)}["07_accuracy_over_time"].table

    rows = table[table["forecaster"] == "Predictions"]
    assert list(rows["avg_miss_pct"]) == [120.0, 70.0]
    assert pd.isna(rows.iloc[0]["change_vs_previous_run"])
    assert rows.iloc[1]["change_vs_previous_run"] == -50.0
