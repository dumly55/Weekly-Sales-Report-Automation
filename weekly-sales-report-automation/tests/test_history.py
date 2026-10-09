import sqlite3
from contextlib import closing
from datetime import date

import pandas as pd
import pytest

from src.history import fill_from_history, open_history, save_actuals, save_scores, write_history
from src.prediction_report import run


def _actuals(rows):
    return pd.DataFrame(rows, columns=["title", "actual"])


def _scores(rows):
    columns = ["movie", "release_date", "forecaster", "forecast", "actual", "status", "pct_error", "accuracy_band"]
    return pd.DataFrame(rows, columns=columns)


class TestActualResults:
    def test_saved_results_fill_later_blanks(self, tmp_path):
        with closing(open_history(tmp_path / "history.sqlite")) as conn:
            save_actuals(_actuals([("Mercy 👮", 54_709_856.0), ("Primate", None)]), conn, date(2026, 10, 5))
            filled, restored = fill_from_history(_actuals([("MERCY", None), ("Primate", None), ("Iron Lung", 5.0)]), conn)

        assert list(filled["actual"].fillna(-1)) == [54_709_856.0, -1, 5.0]
        assert restored == ["MERCY"]

    def test_saving_again_keeps_first_seen_and_updates_the_rest(self, tmp_path):
        with closing(open_history(tmp_path / "history.sqlite")) as conn:
            save_actuals(_actuals([("Mercy", 50.0)]), conn, date(2026, 10, 5))
            save_actuals(_actuals([("Mercy", 55.0)]), conn, date(2026, 10, 12))
            row = conn.execute("SELECT title, actual, first_seen, last_seen FROM actual_results").fetchall()

        assert row == [("Mercy", 55.0, "2026-10-05", "2026-10-12")]

    def test_blank_and_zero_results_are_not_saved(self, tmp_path):
        with closing(open_history(tmp_path / "history.sqlite")) as conn:
            assert save_actuals(_actuals([("A", None), ("B", 0.0), ("C", 3.0)]), conn, date(2026, 10, 5)) == 1


class TestMovieScores:
    def test_scores_are_saved_with_nulls_for_upcoming_movies(self, tmp_path):
        scores = _scores(
            [
                ("Mercy", "2026-01-23", "Predictions", 40.0, 50.0, "Released", -20.0, "Close"),
                ("Dune Part Three", None, "Predictions", 900.0, None, "Upcoming", None, ""),
            ]
        )
        with closing(open_history(tmp_path / "history.sqlite")) as conn:
            assert save_scores(scores, conn, date(2026, 10, 5)) == 2
            rows = conn.execute("SELECT movie, release_date, actual, pct_error, accuracy_band FROM movie_scores ORDER BY movie").fetchall()

        assert rows == [("Dune Part Three", None, None, None, None), ("Mercy", "2026-01-23", 50.0, -20.0, "Close")]

    def test_rerunning_on_the_same_day_replaces_that_days_scores(self, tmp_path):
        first = _scores([("Mercy", None, "Predictions", 40.0, 50.0, "Released", -20.0, "Close")])
        second = _scores([("Mercy", None, "Predictions", 45.0, 50.0, "Released", -10.0, "Nailed it")])
        with closing(open_history(tmp_path / "history.sqlite")) as conn:
            save_scores(first, conn, date(2026, 10, 5))
            save_scores(first, conn, date(2026, 10, 12))
            save_scores(second, conn, date(2026, 10, 12))
            rows = conn.execute("SELECT run_date, forecast FROM movie_scores ORDER BY run_date").fetchall()

        assert rows == [("2026-10-05", 40.0), ("2026-10-12", 45.0)]


class TestHistoryFile:
    def test_round_trip_through_the_file(self, tmp_path):
        path = tmp_path / "history.sqlite"
        with closing(open_history(path)) as conn:
            save_actuals(_actuals([("Mercy", 50.0)]), conn, date(2026, 10, 5))
            write_history(conn, path)
        with closing(sqlite3.connect(path)) as disk:
            assert disk.execute("SELECT title FROM actual_results").fetchall() == [("Mercy",)]
        with closing(open_history(path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM actual_results").fetchone() == (1,)


class TestRunWithHistory:
    @pytest.fixture
    def sheets(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.prediction_report.OUTPUT_DIR", tmp_path / "out")
        predictions = tmp_path / "predictions.csv"
        predictions.write_text("MOVIE TITLE,WORLDWIDE TOTAL\nMERCY,$40\nSEND HELP,$60\n", encoding="utf-8")
        actuals = tmp_path / "actuals.csv"
        return predictions, actuals

    def test_blank_result_is_restored_and_reported(self, tmp_path, sheets):
        predictions, actuals = sheets
        history = tmp_path / "history.sqlite"
        actuals.write_text("Movie,Worldwide Actual\nMercy,$50\nSend Help,$100\n", encoding="utf-8")
        run(str(predictions), str(actuals), date(2026, 10, 5), history_path=history, save_history=True)

        actuals.write_text("Movie,Worldwide Actual\nMercy,\nSend Help,$100\n", encoding="utf-8")
        output = run(str(predictions), str(actuals), date(2026, 10, 12), history_path=history, save_history=True)

        assert output.result.movies["released"].sum() == 2
        assert output.result.restored_from_history == ["Mercy"]
        assert (
            "1 movie had no result in the results sheet this time, so the last known result was used: Mercy."
            in output.findings
        )
        with closing(sqlite3.connect(history)) as disk:
            runs = disk.execute("SELECT run_date, COUNT(*) FROM movie_scores GROUP BY run_date").fetchall()
        assert runs == [("2026-10-05", 2), ("2026-10-12", 2)]

    def test_no_history_file_is_created_unless_saving(self, tmp_path, sheets):
        predictions, actuals = sheets
        actuals.write_text("Movie,Worldwide Actual\nMercy,$50\n", encoding="utf-8")
        run(str(predictions), str(actuals), date(2026, 10, 5), history_path=tmp_path / "history.sqlite")
        assert not (tmp_path / "history.sqlite").exists()

