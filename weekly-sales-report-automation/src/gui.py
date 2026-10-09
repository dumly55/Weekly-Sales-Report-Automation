"""Desktop GUI: generate the reports without touching a terminal, one tab per report.

Launch via the run_gui.bat / run_gui.pyw files at the project root. This module
is intentionally never imported by anything else in `src/` or by the tests --
Tkinter isn't installed on every CI runner, and nothing here needs testing that
the existing pipeline tests don't already cover.
"""

import os
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkcalendar import DateEntry

from .download_data import StatusCallback
from .main import describe_error, run_pipeline, setup_logging
from .prediction_findings import scorecard
from .prediction_report import PredictionRun
from .prediction_report import run as run_predictions
from .predictions import IN_THEATERS, parse_title_map
from .report import format_wow

WINDOW_TITLE = "Report Generator"
SPREADSHEET_TYPES = [("Spreadsheets", "*.csv *.xlsx *.xls"), ("All files", "*.*")]
SOURCE_HINT = (
    'Paste a link (CSV/Excel file, or a Google Sheet shared as "Anyone with the link can view"), '
    "or click Browse to pick a file from your computer."
)
PAD = {"padx": 16, "pady": 8}


class ReportTab:
    """A tab that runs a report in the background: a heading, the tab's own inputs, a run button,
    a progress bar and status line, and a results area. Subclasses add the inputs, start the work
    from `_on_run`, and show the outcome in `_on_success`."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_title: str, heading: str, description: str, button: str):
        self.root = root
        self.tab_title = tab_title
        self.tab = ttk.Frame(notebook)
        notebook.add(self.tab, text=tab_title)
        self._work_queue: queue.Queue = queue.Queue()

        ttk.Label(self.tab, text=heading, font=("Segoe UI", 16, "bold")).pack(anchor="w", **PAD)
        ttk.Label(self.tab, text=description, wraplength=600).pack(anchor="w", padx=16)
        self._build_inputs()
        self.run_btn = ttk.Button(self.tab, text=button, command=self._on_run)
        self.run_btn.pack(pady=(4, 4))
        self.progress = ttk.Progressbar(self.tab, mode="indeterminate")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.tab, textvariable=self.status_var).pack(pady=(4, 4))
        self.results_frame = ttk.Frame(self.tab)
        self.results_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    def _build_inputs(self) -> None:
        raise NotImplementedError

    def _on_run(self) -> None:
        raise NotImplementedError

    def _on_success(self, outcome) -> None:
        raise NotImplementedError

    def _start(self, work: Callable[[StatusCallback], object], message: str) -> None:
        """Runs `work(status_callback)` on a background thread so the window stays responsive."""
        self.run_btn.config(state="disabled")
        for widget in self.results_frame.winfo_children():
            widget.destroy()
        self.progress.pack(fill="x", padx=16, pady=(0, 4))
        self.progress.start(12)
        self.status_var.set(message)
        threading.Thread(target=self._worker, args=(work,), daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _worker(self, work: Callable[[StatusCallback], object]) -> None:
        try:
            self._work_queue.put(("success", work(lambda msg: self._work_queue.put(("status", msg)))))
        except Exception as exc:
            self._work_queue.put(("error", describe_error(exc)))

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, payload = self._work_queue.get_nowait()
            except queue.Empty:
                self.root.after(100, self._poll_queue)
                return
            if kind == "status":
                self.status_var.set(payload)
                continue
            self.progress.stop()
            self.progress.pack_forget()
            self.run_btn.config(state="normal")
            if kind == "error":
                self.status_var.set("Something went wrong.")
                messagebox.showerror(f"{self.tab_title} failed", payload)
            else:
                self._on_success(payload)
            return

    def _source_row(self, parent: ttk.Frame, variable: tk.StringVar, label: str | None = None) -> None:
        """A text box for a link or file path, with a Browse button."""
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=(8, 0))
        if label:
            ttk.Label(row, text=label, width=14).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

        def browse() -> None:
            path = filedialog.askopenfilename(title="Select a CSV or Excel file", filetypes=SPREADSHEET_TYPES)
            if path:
                variable.set(path)

        ttk.Button(row, text="Browse...", command=browse).pack(side="left", padx=(6, 0))


def _hint(parent: ttk.Frame, text: str) -> None:
    ttk.Label(parent, text=text, wraplength=580, foreground="#666666").pack(anchor="w", padx=10, pady=(2, 8))


class SalesTab(ReportTab):
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook) -> None:
        super().__init__(
            root, notebook, "Sales Report", "Weekly Sales Report",
            "Generates a formatted Excel sales report for one week. No terminal needed.", "Generate Report",
        )

    def _build_inputs(self) -> None:
        source_frame = ttk.LabelFrame(self.tab, text="Data source (optional)")
        source_frame.pack(fill="x", **PAD)
        self.link_var = tk.StringVar()
        self._source_row(source_frame, self.link_var)
        _hint(source_frame, SOURCE_HINT + " Leave blank to use the built-in demo dataset.")

        week_frame = ttk.LabelFrame(self.tab, text="Report week")
        week_frame.pack(fill="x", **PAD)
        self.use_latest_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(
            week_frame, text="Latest week in the data", variable=self.use_latest_var, value=True, command=self._sync_date_picker
        ).pack(anchor="w", padx=10, pady=(8, 2))
        date_row = ttk.Frame(week_frame)
        date_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Radiobutton(
            date_row, text="Week containing:", variable=self.use_latest_var, value=False, command=self._sync_date_picker
        ).pack(side="left")
        self.date_picker = DateEntry(date_row, date_pattern="yyyy-mm-dd", width=12, firstweekday="monday")
        self.date_picker.pack(side="left", padx=8)
        self._sync_date_picker()

    def _sync_date_picker(self) -> None:
        self.date_picker.configure(state="disabled" if self.use_latest_var.get() else "normal")

    def _on_run(self) -> None:
        # get_date() never raises: tkcalendar reverts unparseable text to the last valid date.
        as_of_date = None if self.use_latest_var.get() else self.date_picker.get_date()
        source = self.link_var.get().strip()
        data_url = source if source.lower().startswith(("http://", "https://")) else None
        data_file = source if source and data_url is None else None

        def work(status_callback: StatusCallback):
            setup_logging(as_of_date)
            report_data, _quality_log, out_path = run_pipeline(
                as_of_date, data_url=data_url, data_file=data_file, status_callback=status_callback
            )
            return report_data, out_path

        self._start(work, "Starting...")

    def _on_success(self, outcome) -> None:
        report_data, out_path = outcome
        if report_data.current["orders"] == 0:
            self.status_var.set(f"Done -- but no transactions were found for {report_data.week_label}.")
        else:
            self.status_var.set(f"Done. Report covers {report_data.week_label}.")

        # Start the calendar near the data's dates instead of today. tkcalendar ignores set_date while disabled.
        self.date_picker.configure(state="normal")
        self.date_picker.set_date(report_data.week_start.date())
        self._sync_date_picker()

        columns = ("metric", "this_week", "last_week", "wow")
        tree = ttk.Treeview(self.results_frame, columns=columns, show="headings", height=4)
        headings = {"metric": "Metric", "this_week": "This Week", "last_week": "Last Week", "wow": "WoW % Change"}
        for col, text in headings.items():
            tree.heading(col, text=text)
            tree.column(col, anchor="e" if col != "metric" else "w", width=130)
        tree.tag_configure("good", foreground="#1f7a1f")
        tree.tag_configure("bad", foreground="#c00000")
        for metric, this_week, last_week, wow in report_data.summary_rows():
            tag = "" if wow is None else ("good" if wow >= 0 else "bad")
            tree.insert("", "end", values=(metric, this_week, last_week, format_wow(wow)), tags=(tag,))
        tree.pack(fill="x", pady=(0, 12))

        _add_open_buttons(self.results_frame, out_path)
        _add_findings_box(self.results_frame, report_data.findings)


class PredictionTab(ReportTab):
    """Scores a predictions sheet against a sheet of actual results."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook) -> None:
        super().__init__(
            root, notebook, "Movie Predictions", "Predictions vs Actual Results",
            "Scores movie box-office predictions against the actual worldwide results, and shows how accurate they were.",
            "Score Predictions",
        )

    def _build_inputs(self) -> None:
        sheets_frame = ttk.LabelFrame(self.tab, text="Sheets")
        sheets_frame.pack(fill="x", **PAD)
        self.predictions_var = tk.StringVar()
        self.actuals_var = tk.StringVar()
        self._source_row(sheets_frame, self.predictions_var, "Predictions:")
        self._source_row(sheets_frame, self.actuals_var, "Actual results:")
        _hint(sheets_frame, SOURCE_HINT)

        titles_frame = ttk.LabelFrame(self.tab, text="Title matches (optional)")
        titles_frame.pack(fill="x", **PAD)
        self.title_map_var = tk.StringVar()
        ttk.Entry(titles_frame, textvariable=self.title_map_var).pack(fill="x", padx=10, pady=(8, 2))
        _hint(
            titles_frame,
            "For movies the two sheets name differently, as Results Title=Predictions Title, "
            "separated by semicolons. Example: Minions 3=MINIONS AND MONSTERS",
        )

    def _on_run(self) -> None:
        predictions, actuals = self.predictions_var.get().strip(), self.actuals_var.get().strip()
        if not predictions or not actuals:
            messagebox.showerror("Missing sheet", "Choose both a predictions sheet and an actual-results sheet.")
            return
        try:
            title_map = parse_title_map(self.title_map_var.get())
        except ValueError as exc:
            messagebox.showerror("Title matches", str(exc))
            return

        def work(_status_callback: StatusCallback) -> PredictionRun:
            today = date.today()
            setup_logging(today, "predictions")
            return run_predictions(predictions, actuals, today, title_map)

        self._start(work, "Reading both sheets and scoring the predictions...")

    def _on_success(self, outcome: PredictionRun) -> None:
        movies = outcome.result.movies
        scored = int(movies["scored"].sum())
        showing = int((movies["run_status"] == IN_THEATERS).sum())
        status = f"Done. Scored {scored} finished movie{'' if scored == 1 else 's'}"
        self.status_var.set(status + (f"; {showing} still in theaters." if showing else "."))

        _add_open_buttons(self.results_frame, outcome.out_path)
        entries = [
            (f"{row.measure}: ", row.result + (f"\n{', '.join(row.movies)}" if row.movies else ""))
            for row in scorecard(outcome.result)
        ]
        _add_text_box(self.results_frame, "Scorecard", entries)
        _add_findings_box(self.results_frame, outcome.findings)


def _add_open_buttons(parent: ttk.Frame, out_path: Path) -> None:
    button_row = ttk.Frame(parent)
    button_row.pack(fill="x")
    ttk.Button(button_row, text="Open Report", command=lambda: os.startfile(out_path)).pack(side="left")
    ttk.Button(button_row, text="Open Folder", command=lambda: os.startfile(out_path.parent)).pack(side="left", padx=8)


def _add_text_box(parent: ttk.Frame, title: str, entries: list[tuple[str, str]]) -> None:
    """A titled, scrollable, read-only box of entries, each a (bold part, plain part) pair."""
    if not entries:
        return
    ttk.Label(parent, text=title, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    scrollbar = ttk.Scrollbar(frame, orient="vertical")
    text = tk.Text(
        frame, wrap="word", height=8, relief="flat", font=("Segoe UI", 10),
        padx=8, pady=6, yscrollcommand=scrollbar.set,
    )
    text.tag_configure("bold", font=("Segoe UI", 10, "bold"))
    scrollbar.config(command=text.yview)
    scrollbar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    for i, (bold, plain) in enumerate(entries):
        if i:
            text.insert("end", "\n\n")
        text.insert("end", bold, "bold")
        text.insert("end", plain)
    text.config(state="disabled")


def _add_findings_box(parent: ttk.Frame, findings: list[str]) -> None:
    _add_text_box(parent, "Key findings", [("", f"•  {finding}") for finding in findings])


def build_window(root: tk.Tk) -> tuple[SalesTab, PredictionTab]:
    root.title(WINDOW_TITLE)
    # Keep the window on screen when Windows display scaling shrinks the usable height.
    height = min(800, root.winfo_screenheight() - 80)
    root.geometry(f"660x{height}")
    root.minsize(600, min(640, height))
    style = ttk.Style()
    for theme in ("vista", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    return SalesTab(root, notebook), PredictionTab(root, notebook)


def main() -> None:
    root = tk.Tk()
    build_window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
