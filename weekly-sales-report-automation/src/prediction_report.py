"""CLI and Excel report for scoring movie predictions against actual results.

Usage:
    python -m src.prediction_report --predictions <link or file> --actuals <link or file>
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .excel_report import BAD_FONT, GOOD_FONT, _add_table_polish, _write_dataframe, _write_findings, _write_title
from .main import LOGS_DIR, OUTPUT_DIR, describe_error, next_free_path
from .predictions import Comparison, accuracy_stats, compare, load_source, read_actuals, read_predictions

FORECASTS = {"predicted": "Predictions", "projection": "Tracker Projection"}
GOOD_ERROR_PCT = 25  # errors within this are shown in green, beyond it in red
MONEY_FORMAT = '"$"#,##0'
PCT_FORMAT = "+0.0%;-0.0%"

console = Console()


def short_money(value: float) -> str:
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def _call(row: pd.Series, forecast: str) -> str:
    return (
        f"{row['title']}, predicted {short_money(row[forecast])} vs "
        f"{short_money(row['actual'])} actual ({row[f'{forecast}_pct_error']:+,.1f}%)"
    )


def prediction_findings(result: Comparison) -> list[str]:
    """Plain-English findings from fixed rules, so the same sheets always give the same text."""
    movies = result.movies
    released = movies[movies["released"]]
    pending = len(movies) - len(released)
    findings = [
        f"{len(released)} of the {len(movies)} matched movies have been released and scored so far"
        + (f"; {pending} {'is' if pending == 1 else 'are'} still waiting on results." if pending else ".")
    ]

    stats = accuracy_stats(movies, "predicted")
    if stats is None:
        return findings + _unmatched_findings(result, released)

    n = stats["scored"]
    typical = f"The predictions were typically off by {stats['median_abs_pct']:.0f}% (median"
    if stats["mean_abs_pct"] - stats["median_abs_pct"] >= 10:
        typical += f"; the average is {stats['mean_abs_pct']:.0f}% because of a few big misses"
    findings.append(
        typical + f"). {stats['within_25']} of {n} landed within 25% of the actual result, "
        f"and {stats['within_10']} within 10%."
    )

    if stats["over"] != stats["under"]:
        lean = "high" if stats["over"] > stats["under"] else "low"
        findings.append(
            f"They ran {lean} more often than not: {stats['over']} too high vs {stats['under']} too low "
            f"(median error {stats['median_pct']:+.0f}%)."
        )
    total = (
        f"Added up, the predictions came to {short_money(stats['total_forecast'])} against "
        f"{short_money(stats['total_actual'])} actual ({stats['total_pct']:+.1f}%)"
    )
    if abs(stats["total_pct"]) < stats["median_abs_pct"] / 2:
        total += ", so misses in both directions largely cancelled out"
    findings.append(total + ".")

    errors = released["predicted_pct_error"].dropna().abs()
    findings.append(f"Best call: {_call(released.loc[errors.idxmin()], 'predicted')}.")
    findings.append(f"Biggest miss: {_call(released.loc[errors.idxmax()], 'predicted')}.")

    tracker = accuracy_stats(movies, "projection")
    if tracker is not None:
        both = released.dropna(subset=["predicted_pct_error", "projection_pct_error"])
        wins = int((both["predicted_pct_error"].abs() < both["projection_pct_error"].abs()).sum())
        findings.append(
            f"Against the tracker's own projections (typically off by {tracker['median_abs_pct']:.0f}%), "
            f"the predictions were closer on {wins} of {len(both)} movies."
        )
    return findings + _unmatched_findings(result, released)


def _unmatched_findings(result: Comparison, released: pd.DataFrame) -> list[str]:
    findings = []
    unmatched_actuals, unmatched_predictions = len(result.unmatched_actuals), len(result.unmatched_predictions)
    if unmatched_actuals:
        names = ", ".join(result.unmatched_actuals[:5])
        if unmatched_actuals > 5:
            names += f" and {unmatched_actuals - 5} more"
        if unmatched_actuals == 1:
            findings.append(
                f"1 movie in the results sheet has no matching prediction "
                f"(it may be listed under a different title): {names}."
            )
        else:
            findings.append(
                f"{unmatched_actuals} movies in the results sheet have no matching prediction "
                f"(they may be listed under a different title): {names}."
            )
    if unmatched_predictions:
        findings.append(
            f"{unmatched_predictions} predicted {'movie doesn' if unmatched_predictions == 1 else 'movies don'}'t "
            "appear in the results sheet; see the Unmatched sheet."
        )
    return findings


def _scorecard_rows(result: Comparison) -> list[tuple[str, list, str]]:
    """(label, [value per forecast], kind) rows for the scorecard table."""
    stats = {f: accuracy_stats(result.movies, f) for f in FORECASTS}
    stats = {f: s for f, s in stats.items() if s is not None}

    def row(label: str, key: str, kind: str):
        return label, [stats[f][key] for f in stats], kind

    rows = [
        row("Movies scored", "scored", "count"),
        row("Median error (either direction)", "median_abs_pct", "pct_abs"),
        row("Average error (either direction)", "mean_abs_pct", "pct_abs"),
        row("Median lean (+ = too high)", "median_pct", "pct"),
        row("Too high", "over", "count"),
        row("Too low", "under", "count"),
        row("Within 10% of actual", "within_10", "count"),
        row("Within 25% of actual", "within_25", "count"),
        row("Total forecast", "total_forecast", "money"),
        row("Total actual", "total_actual", "money"),
        row("Total difference", "total_pct", "pct"),
    ]
    return [(label, values, kind) for label, values, kind in rows], [FORECASTS[f] for f in stats]


def _build_scorecard_sheet(ws, result: Comparison, findings: list[str], as_of: date) -> None:
    _write_title(ws, f"Prediction Accuracy as of {as_of}", span_cols=3)
    rows, headers = _scorecard_rows(result)
    if not headers:
        ws.cell(row=3, column=1, value="No released movies to score yet.")
        _write_findings(ws, findings, start_row=5)
        return

    table = pd.DataFrame([[label, *values] for label, values, _ in rows], columns=["Metric", *headers])
    header_row = 3
    last_row = _write_dataframe(ws, table, header_row)
    formats = {"count": "#,##0", "money": MONEY_FORMAT, "pct": PCT_FORMAT, "pct_abs": "0.0%"}
    for offset, (_, values, kind) in enumerate(rows, start=1):
        for col in range(2, 2 + len(values)):
            cell = ws.cell(row=header_row + offset, column=col)
            if kind.startswith("pct"):
                cell.value = cell.value / 100
            cell.number_format = formats[kind]
    ws.column_dimensions["A"].width = 34
    _write_findings(ws, findings, start_row=last_row + 2)


def _pct_cells(ws, header_row: int, last_row: int, columns: list[int]) -> None:
    for col in columns:
        for (cell,) in ws.iter_rows(min_row=header_row + 1, max_row=last_row, min_col=col, max_col=col):
            if isinstance(cell.value, (int, float)):
                cell.font = GOOD_FONT if abs(cell.value) <= GOOD_ERROR_PCT else BAD_FONT
                cell.value = cell.value / 100
                cell.number_format = PCT_FORMAT


def _for_excel(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype(object).where(df.notna(), None)


def _build_movies_sheet(ws, result: Comparison) -> None:
    released = result.movies[result.movies["released"]].copy()

    def closer(row):
        p, t = row["predicted_pct_error"], row["projection_pct_error"]
        if pd.isna(p) or pd.isna(t):
            return None
        return "Predictions" if abs(p) < abs(t) else ("Tracker" if abs(t) < abs(p) else "Tie")

    table = pd.DataFrame(
        {
            "Movie": released["title"],
            "Release Date": released["release_date"],
            "Predicted": released["predicted"],
            "Tracker Projection": released["projection"],
            "Actual": released["actual"],
            "Predicted Error": released["predicted_pct_error"],
            "Tracker Error": released["projection_pct_error"],
            "Closer": released.apply(closer, axis=1) if len(released) else [],
        }
    )
    _write_title(ws, "Movie by Movie: released movies, forecasts vs actual worldwide gross", span_cols=8)
    header_row = 3
    last_row = _write_dataframe(
        ws, _for_excel(table), header_row, number_formats={1: "yyyy-mm-dd", 2: MONEY_FORMAT, 3: MONEY_FORMAT, 4: MONEY_FORMAT}
    )
    _pct_cells(ws, header_row, last_row, columns=[6, 7])
    ws.column_dimensions["A"].width = 38
    _add_table_polish(ws, header_row, max(last_row, header_row), last_col=len(table.columns))


def _build_upcoming_sheet(ws, result: Comparison) -> None:
    upcoming = result.movies[~result.movies["released"]]
    table = pd.DataFrame(
        {
            "Movie": upcoming["title"],
            "Release Date": upcoming["release_date"],
            "Predicted": upcoming["predicted"],
            "Tracker Projection": upcoming["projection"],
        }
    )
    _write_title(ws, "Upcoming: matched movies still waiting on an actual result", span_cols=4)
    header_row = 3
    last_row = _write_dataframe(
        ws, _for_excel(table), header_row, number_formats={1: "yyyy-mm-dd", 2: MONEY_FORMAT, 3: MONEY_FORMAT}
    )
    ws.column_dimensions["A"].width = 38
    _add_table_polish(ws, header_row, max(last_row, header_row), last_col=len(table.columns))


def _build_unmatched_sheet(ws, result: Comparison) -> None:
    _write_title(ws, "Unmatched: titles found in only one sheet", span_cols=2)
    length = max(len(result.unmatched_predictions), len(result.unmatched_actuals))
    table = pd.DataFrame(
        {
            "In predictions only": result.unmatched_predictions + [None] * (length - len(result.unmatched_predictions)),
            "In results only": result.unmatched_actuals + [None] * (length - len(result.unmatched_actuals)),
        }
    )
    header_row = 3
    last_row = _write_dataframe(ws, table, header_row)
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 44
    _add_table_polish(ws, header_row, max(last_row, header_row), last_col=2)


def build_prediction_workbook(result: Comparison, findings: list[str], out_path: Path, as_of: date) -> None:
    wb = Workbook()
    scorecard = wb.active
    scorecard.title = "Scorecard"
    _build_scorecard_sheet(scorecard, result, findings, as_of)
    _build_movies_sheet(wb.create_sheet("Movie by Movie"), result)
    _build_upcoming_sheet(wb.create_sheet("Upcoming"), result)
    _build_unmatched_sheet(wb.create_sheet("Unmatched"), result)
    for name, color in {"Scorecard": "1F4E78", "Movie by Movie": "2E7D32", "Upcoming": "6A1B9A", "Unmatched": "757575"}.items():
        wb[name].sheet_properties.tabColor = color
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def summary_markdown(result: Comparison, findings: list[str], as_of: date) -> str:
    rows, headers = _scorecard_rows(result)
    lines = [f"## Prediction Accuracy as of {as_of}", ""]
    if headers:
        lines += ["| Metric | " + " | ".join(headers) + " |", "|---|" + "--:|" * len(headers)]
        for label, values, kind in rows:
            lines.append(f"| {label} | " + " | ".join(_format_value(v, kind) for v in values) + " |")
    lines += ["", "### Key findings", ""] + [f"- {finding}" for finding in findings]
    return "\n".join(lines) + "\n"


def _format_value(value: float, kind: str) -> str:
    if kind == "money":
        return short_money(value)
    if kind == "pct":
        return f"{value:+.1f}%"
    if kind == "pct_abs":
        return f"{value:.1f}%"
    return f"{value:,.0f}"


def run(predictions_source: str, actuals_source: str, as_of: date) -> tuple[Comparison, list[str], Path]:
    result = compare(read_predictions(load_source(predictions_source)), read_actuals(load_source(actuals_source)))
    findings = prediction_findings(result)
    out_path = next_free_path(OUTPUT_DIR / f"prediction_accuracy_{as_of}.xlsx")
    build_prediction_workbook(result, findings, out_path, as_of)
    logging.getLogger(__name__).info("Prediction report written to %s", out_path)
    return result, findings, out_path


def _print_summary(result: Comparison, findings: list[str], out_path: Path, as_of: date) -> None:
    rows, headers = _scorecard_rows(result)
    if headers:
        table = Table(title=f"Prediction Accuracy as of {as_of}", title_style="bold cyan")
        table.add_column("Metric", style="bold")
        for header in headers:
            table.add_column(header, justify="right")
        for label, values, kind in rows:
            table.add_row(label, *(_format_value(v, kind) for v in values))
        console.print()
        console.print(table)
    console.print("\n[bold cyan]Key findings[/bold cyan]")
    for finding in findings:
        console.print(f"  • {escape(finding)}")
    console.print(f"\n[bold green]Report written[/bold green] to [bold]{out_path}[/bold]\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score movie box-office predictions against actual results.")
    parser.add_argument("--predictions", required=True, help="Link or file path for the predictions sheet.")
    parser.add_argument("--actuals", required=True, help="Link or file path for the actual-results sheet.")
    parser.add_argument("--markdown-summary", default=None, help="Optional: append the scorecard and findings as Markdown to this file.")
    args = parser.parse_args()

    as_of = date.today()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOGS_DIR / f"predictions_{as_of}.log",
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        result, findings, out_path = run(args.predictions, args.actuals, as_of)
        if args.markdown_summary:
            with open(args.markdown_summary, "a", encoding="utf-8") as summary_file:
                summary_file.write(summary_markdown(result, findings, as_of))
    except Exception as exc:
        logging.getLogger(__name__).exception("Prediction report failed")
        console.print(f"[bold red]Error:[/bold red] {describe_error(exc)}")
        sys.exit(1)
    _print_summary(result, findings, out_path, as_of)


if __name__ == "__main__":
    main()
