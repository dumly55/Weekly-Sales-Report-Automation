from datetime import date

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.prediction_report import (
    accuracy_band,
    build_prediction_workbook,
    prediction_findings,
    run,
    scorecard,
    short_money,
    summary_markdown,
    tableau_rows,
)
from src.predictions import compare
from src.sql_insights import Insight

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


def test_scorecard_lists_the_movies_behind_each_measure():
    rows = [(r.measure, r.result, r.movies) for r in scorecard(_result())]
    assert rows == [
        ("Movies scored", "3 of 4 matched movies", []),
        ("Typical miss (median)", "50%", []),
        ("Nailed it (within 10%)", "1 of 3", ["Alpha (+10%)"]),
        ("Close (10-25% off)", "0 of 3", []),
        ("Off (25-50% off)", "1 of 3", ["Bravo (-50%)"]),
        ("Way off (more than 50% off)", "1 of 3", ["Charlie (+300%)"]),
        ("Predicted too high", "2 of 3", ["Charlie (+300%)", "Alpha (+10%)"]),
        ("Predicted too low", "1 of 3", ["Bravo (-50%)"]),
        ("Closer than the tracker", "0 of 3", []),
        ("Tracker's typical miss (median)", "0%", []),
        ("Total predicted vs actual", "$560 vs $300 (+86.7%)", []),
    ]


def test_scorecard_is_empty_before_any_release():
    assert scorecard(_result(with_released=False)) == []


def test_accuracy_bands_have_inclusive_upper_limits():
    assert [accuracy_band(v) for v in (0, 10, 10.1, 25, 50, 50.1)] == ["Nailed it", "Nailed it", "Close", "Close", "Off", "Way off"]


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

        ws = wb["Scorecard"]
        assert [c.value for c in ws[3]][:3] == ["Measure", "Result", "Movies"]
        assert [ws["A6"].value, ws["B6"].value, ws["C6"].value] == ["Nailed it (within 10%)", "1 of 3", "Alpha (+10%)"]
        detailed = next(r for r in range(1, ws.max_row + 1) if ws.cell(row=r, column=1).value == "Detailed stats")
        assert ws.cell(row=detailed + 1, column=1).value == "Metric"
        assert ws.cell(row=detailed + 3, column=1).value == "Median error (either direction)"
        assert ws.cell(row=detailed + 3, column=2).value == pytest.approx(0.5)
        assert ws.cell(row=detailed + 3, column=2).number_format == "0.0%"

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
    assert alpha["accuracy_band"] == "Nailed it"
    delta = rows[(rows["movie"] == "Delta") & (rows["forecaster"] == "Tracker Projection")].iloc[0]
    assert delta["status"] == "Upcoming"
    assert pd.isna(delta["pct_error"]) and delta["direction"] == ""


def test_run_end_to_end_with_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr("src.prediction_report.OUTPUT_DIR", tmp_path)
    (tmp_path / "predictions.csv").write_text("MOVIE TITLE,WORLDWIDE TOTAL\nMERCY,\"$41,470,588.24\"\n", encoding="utf-8")
    (tmp_path / "actuals.csv").write_text(
        "Report,,\nMovie,Release Date,Worldwide Actual\nMercy 👮🏻,23/01/2026,\"$54,709,856\"\n", encoding="utf-8"
    )
    output = run(
        str(tmp_path / "predictions.csv"), str(tmp_path / "actuals.csv"), AS_OF, history_path=tmp_path / "h.sqlite"
    )

    assert output.out_path == tmp_path / "prediction_accuracy_2026-10-08.xlsx" and output.out_path.exists()
    assert len(pd.read_csv(output.out_path.with_suffix(".csv"))) == 1
    assert output.findings[0] == "1 of the 1 matched movies have been released and scored so far."

    assert len(output.insights) == 7
    assert "SQL Insights" in load_workbook(output.out_path).sheetnames
    history = pd.read_csv(output.history_csv)
    assert list(history[["run_date", "movie", "forecaster"]].iloc[0]) == ["2026-10-08", "Mercy", "Predictions"]


def test_summary_markdown():
    result = _result()
    markdown = summary_markdown(result, prediction_findings(result), AS_OF)
    assert markdown.startswith("## Prediction Accuracy as of 2026-10-08\n")
    assert "| **Nailed it (within 10%)** | 1 of 3 | Alpha (+10%) |" in markdown
    assert "<details><summary>Detailed stats</summary>" in markdown
    assert "| Median error (either direction) | 50.0% | 0.0% |" in markdown
    assert "- Best call: Alpha, predicted $110 vs $100 actual (+10.0%)." in markdown
    assert "SQL insights" not in markdown


def test_summary_markdown_with_sql_insights():
    result = _result()
    insights = [
        Insight("02_median_miss", "What is the median miss?", pd.DataFrame({"forecaster": ["Predictions"], "median_miss_pct": [50.0]})),
        Insight("03_accuracy_bands", "Which band?", pd.DataFrame({"accuracy_band": ["Off"], "movies": [1], "titles": ["Bravo"]})),
    ]
    markdown = summary_markdown(result, prediction_findings(result), AS_OF, insights)
    assert "<details><summary>SQL insights (queries in the sql/ folder)</summary>" in markdown
    assert "**What is the median miss?** (`02_median_miss.sql`)" in markdown
    assert "| Predictions | 50.0 |" in markdown
    assert "| accuracy_band | movies |" in markdown, "the long titles column is left out"
