from datetime import date

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.prediction_report import (
    build_prediction_workbook,
    prediction_findings,
    run,
    short_money,
    summary_markdown,
    tableau_rows,
)
from src.predictions import compare

AS_OF = date(2026, 10, 8)


def _result(with_released=True):
    # Predictions: A +10%, B -50%, C +300%; D not released yet; E has no result; F has no prediction.
    predictions = pd.DataFrame(
        {"title": ["Alpha", "Bravo", "Charlie", "Delta", "Echo"], "predicted": [110.0, 50.0, 400.0, 100.0, 70.0]}
    )
    actuals = pd.DataFrame(
        {
            "title": ["Alpha", "Bravo", "Charlie", "Delta", "Foxtrot"],
            "actual": [100.0, 100.0, 100.0, None, 90.0] if with_released else [None] * 5,
            "projection": [100.0, 100.0, 100.0, 120.0, 95.0],
            "release_date": pd.to_datetime(["2026-01-02", "2026-01-09", "2026-01-16", "2026-12-18", "2026-02-01"]),
        }
    )
    return compare(predictions, actuals)


class TestFindings:
    def test_findings_cover_accuracy_lean_calls_and_unmatched(self):
        findings = prediction_findings(_result())
        assert findings == [
            "3 of the 4 matched movies have been released and scored so far; 1 is still waiting on results.",
            "The predictions were typically off by 50% (median; the average is 120% because of a few big misses). "
            "1 of 3 landed within 25% of the actual result, and 1 within 10%.",
            "They ran high more often than not: 2 too high vs 1 too low (median error +10%).",
            "Added up, the predictions came to $560 against $300 actual (+86.7%).",
            "Best call: Alpha, predicted $110 vs $100 actual (+10.0%).",
            "Biggest miss: Charlie, predicted $400 vs $100 actual (+300.0%).",
            "Against the tracker's own projections (typically off by 0%), the predictions were closer on 0 of 3 movies.",
            "1 movie in the results sheet has no matching prediction (it may be listed under a different title): Foxtrot.",
            "1 predicted movie doesn't appear in the results sheet; see the Unmatched sheet.",
        ]

    def test_nothing_released_yet(self):
        findings = prediction_findings(_result(with_released=False))
        assert findings[0] == "0 of the 4 matched movies have been released and scored so far; 4 are still waiting on results."
        assert not any("typically off" in f for f in findings)


def test_short_money():
    assert short_money(2_506_720_000) == "$2.51B"
    assert short_money(58_527_279) == "$58.5M"
    assert short_money(940_000) == "$940,000"


class TestWorkbook:
    def test_sheets_and_contents(self, tmp_path):
        result = _result()
        out_path = tmp_path / "accuracy.xlsx"
        build_prediction_workbook(result, prediction_findings(result), out_path, AS_OF)

        wb = load_workbook(out_path)
        assert wb.sheetnames == ["Scorecard", "Movie by Movie", "Upcoming", "Unmatched"]

        scorecard = wb["Scorecard"]
        assert [c.value for c in scorecard[3]][:3] == ["Metric", "Predictions", "Tracker Projection"]
        assert scorecard["A5"].value == "Median error (either direction)"
        assert scorecard["B5"].value == pytest.approx(0.5)
        assert scorecard["B5"].number_format == "0.0%"

        movies = wb["Movie by Movie"]
        assert [movies.cell(row=r, column=1).value for r in (4, 5, 6)] == ["Alpha", "Bravo", "Charlie"]
        assert movies["F4"].value == pytest.approx(0.10)
        assert movies["H4"].value == "Tracker"

        assert wb["Upcoming"]["A4"].value == "Delta"
        assert [c.value for c in wb["Unmatched"][4]] == ["Echo", "Foxtrot"]

    def test_scorecard_without_released_movies(self, tmp_path):
        result = _result(with_released=False)
        out_path = tmp_path / "accuracy.xlsx"
        build_prediction_workbook(result, prediction_findings(result), out_path, AS_OF)
        assert load_workbook(out_path)["Scorecard"]["A3"].value == "No released movies to score yet."


def test_tableau_rows_are_one_per_movie_per_forecaster():
    rows = tableau_rows(_result())
    assert len(rows) == 8
    alpha = rows[(rows["movie"] == "Alpha") & (rows["forecaster"] == "Predictions")].iloc[0]
    assert (alpha["release_date"], alpha["forecast"], alpha["actual"]) == ("2026-01-02", 110.0, 100.0)
    assert (alpha["status"], alpha["error"], alpha["pct_error"], alpha["direction"]) == ("Released", 10.0, 10.0, "Too high")
    delta = rows[(rows["movie"] == "Delta") & (rows["forecaster"] == "Tracker Projection")].iloc[0]
    assert delta["status"] == "Upcoming"
    assert pd.isna(delta["pct_error"]) and delta["direction"] == ""


def test_run_end_to_end_with_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr("src.prediction_report.OUTPUT_DIR", tmp_path)
    (tmp_path / "predictions.csv").write_text("MOVIE TITLE,WORLDWIDE TOTAL\nMERCY,\"$41,470,588.24\"\n", encoding="utf-8")
    (tmp_path / "actuals.csv").write_text(
        "Report,,\nMovie,Release Date,Worldwide Actual\nMercy 👮🏻,23/01/2026,\"$54,709,856\"\n", encoding="utf-8"
    )
    result, findings, out_path = run(str(tmp_path / "predictions.csv"), str(tmp_path / "actuals.csv"), AS_OF)

    assert out_path == tmp_path / "prediction_accuracy_2026-10-08.xlsx" and out_path.exists()
    assert len(pd.read_csv(out_path.with_suffix(".csv"))) == 1
    assert findings[0] == "1 of the 1 matched movies have been released and scored so far."


def test_summary_markdown():
    result = _result()
    markdown = summary_markdown(result, prediction_findings(result), AS_OF)
    assert markdown.startswith("## Prediction Accuracy as of 2026-10-08\n")
    assert "| Median error (either direction) | 50.0% | 0.0% |" in markdown
    assert "- Best call: Alpha, predicted $110 vs $100 actual (+10.0%)." in markdown
