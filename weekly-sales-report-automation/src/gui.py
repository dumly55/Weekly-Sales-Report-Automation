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
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkcalendar import DateEntry

from .main import describe_error, run_pipeline, setup_logging
from .prediction_findings import scorecard
from .prediction_report import run as run_predictions
from .predictions import IN_THEATERS, parse_title_map
from .report import format_wow

WINDOW_TITLE = "Report Generator"


class ReportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        # Keep the window on screen when Windows display scaling shrinks the usable height.
        height = min(800, self.root.winfo_screenheight() - 80)
        self.root.geometry(f"660x{height}")
        self.root.minsize(600, min(640, height))

        style = ttk.Style()
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.tab = ttk.Frame(self.notebook)
        self.notebook.add(self.tab, text="Sales Report")

        self._work_queue: queue.Queue = queue.Queue()
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 8}

        ttk.Label(self.tab, text="Weekly Sales Report", font=("Segoe UI", 16, "bold")).pack(anchor="w", **pad)
        ttk.Label(
            self.tab,
            text="Generates a formatted Excel sales report for one week. No terminal needed.",
            wraplength=560,
        ).pack(anchor="w", padx=16)

        source_frame = ttk.LabelFrame(self.tab, text="Data source (optional)")
        source_frame.pack(fill="x", **pad)
        source_row = ttk.Frame(source_frame)
        source_row.pack(fill="x", padx=10, pady=(8, 2))
        self.link_var = tk.StringVar()
        ttk.Entry(source_row, textvariable=self.link_var).pack(side="left", fill="x", expand=True)
        ttk.Button(source_row, text="Browse...", command=self._on_browse).pack(side="left", padx=(6, 0))
        ttk.Label(
            source_frame,
            text='Paste a link (CSV/Excel file, or a Google Sheet shared as "Anyone with the link '
            'can view"), or click Browse to pick a file from your computer. Leave blank to use '
            "the built-in demo dataset.",
            wraplength=540,
            foreground="#666666",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        week_frame = ttk.LabelFrame(self.tab, text="Report week")
        week_frame.pack(fill="x", **pad)
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

        self.generate_btn = ttk.Button(self.tab, text="Generate Report", command=self._on_generate)
        self.generate_btn.pack(pady=(4, 4))

        self.progress = ttk.Progressbar(self.tab, mode="indeterminate")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.tab, textvariable=self.status_var).pack(pady=(4, 4))

        self.results_frame = ttk.Frame(self.tab)
        self.results_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    def _sync_date_picker(self) -> None:
        self.date_picker.configure(state="disabled" if self.use_latest_var.get() else "normal")

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a CSV or Excel file",
            filetypes=SPREADSHEET_TYPES,
        )
        if path:
            self.link_var.set(path)

    def _on_generate(self) -> None:
        # get_date() never raises: tkcalendar reverts unparseable text to the last valid date.
        as_of_date = None if self.use_latest_var.get() else self.date_picker.get_date()

        source = self.link_var.get().strip()
        data_url = source if source.lower().startswith(("http://", "https://")) else None
        data_file = source if source and data_url is None else None

        self.generate_btn.config(state="disabled")
        self._clear_results()
        self.progress.pack(fill="x", padx=16, pady=(0, 4))
        self.progress.start(12)
        self.status_var.set("Starting...")

        thread = threading.Thread(target=self._run_pipeline_worker, args=(as_of_date, data_url, data_file), daemon=True)
        thread.start()
        self.root.after(100, self._poll_queue)

    def _run_pipeline_worker(self, as_of_date: date | None, data_url: str | None, data_file: str | None) -> None:
        try:
            setup_logging(as_of_date)
            report_data, _quality_log, out_path = run_pipeline(
                as_of_date,
                data_url=data_url,
                data_file=data_file,
                status_callback=lambda msg: self._work_queue.put(("status", msg)),
            )
            self._work_queue.put(("success", (report_data, out_path)))
        except Exception as exc:
            self._work_queue.put(("error", describe_error(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._work_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "success":
                    self._on_success(*payload)
                    return
                elif kind == "error":
                    self._on_error(payload)
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_success(self, report_data, out_path: Path) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.generate_btn.config(state="normal")

        if report_data.current["orders"] == 0:
            self.status_var.set(f"Done -- but no transactions were found for {report_data.week_label}.")
        else:
            self.status_var.set(f"Done. Report covers {report_data.week_label}.")

        # Start the calendar near the data's dates instead of today. tkcalendar ignores set_date while disabled.
        self.date_picker.configure(state="normal")
        self.date_picker.set_date(report_data.week_start.date())
        self._sync_date_picker()

        self._show_results(report_data, out_path)

    def _on_error(self, message: str) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.generate_btn.config(state="normal")
        self.status_var.set("Something went wrong.")
        messagebox.showerror("Report failed", message)

    def _clear_results(self) -> None:
        for widget in self.results_frame.winfo_children():
            widget.destroy()

    def _show_results(self, report_data, out_path: Path) -> None:
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


SPREADSHEET_TYPES = [("Spreadsheets", "*.csv *.xlsx *.xls"), ("All files", "*.*")]


class PredictionTab:
    """Scores a predictions sheet against a sheet of actual results."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook) -> None:
        self.root = root
        self.tab = ttk.Frame(notebook)
        notebook.add(self.tab, text="Movie Predictions")
        self._work_queue: queue.Queue = queue.Queue()
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 8}
        ttk.Label(self.tab, text="Predictions vs Actual Results", font=("Segoe UI", 16, "bold")).pack(anchor="w", **pad)
        ttk.Label(
            self.tab,
            text="Scores movie box-office predictions against the actual worldwide results, "
            "and shows how accurate they were.",
            wraplength=600,
        ).pack(anchor="w", padx=16)

        sheets_frame = ttk.LabelFrame(self.tab, text="Sheets")
        sheets_frame.pack(fill="x", **pad)
        self.predictions_var = tk.StringVar()
        self.actuals_var = tk.StringVar()
        self._source_row(sheets_frame, "Predictions:", self.predictions_var)
        self._source_row(sheets_frame, "Actual results:", self.actuals_var)
        ttk.Label(
            sheets_frame,
            text='Paste a link (CSV/Excel file, or a Google Sheet shared as "Anyone with the link can view"), '
            "or click Browse to pick a file from your computer.",
            wraplength=580,
            foreground="#666666",
        ).pack(anchor="w", padx=10, pady=(2, 8))

        titles_frame = ttk.LabelFrame(self.tab, text="Title matches (optional)")
        titles_frame.pack(fill="x", **pad)
        self.title_map_var = tk.StringVar()
        ttk.Entry(titles_frame, textvariable=self.title_map_var).pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(
            titles_frame,
            text="For movies the two sheets name differently, as Results Title=Predictions Title, "
            "separated by semicolons. Example: Minions 3=MINIONS AND MONSTERS",
            wraplength=580,
            foreground="#666666",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        self.score_btn = ttk.Button(self.tab, text="Score Predictions", command=self._on_score)
        self.score_btn.pack(pady=(4, 4))
        self.progress = ttk.Progressbar(self.tab, mode="indeterminate")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.tab, textvariable=self.status_var).pack(pady=(4, 4))
        self.results_frame = ttk.Frame(self.tab)
        self.results_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    def _source_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(row, text=label, width=14).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

        def browse() -> None:
            path = filedialog.askopenfilename(title="Select a CSV or Excel file", filetypes=SPREADSHEET_TYPES)
            if path:
                variable.set(path)

        ttk.Button(row, text="Browse...", command=browse).pack(side="left", padx=(6, 0))

    def _on_score(self) -> None:
        predictions, actuals = self.predictions_var.get().strip(), self.actuals_var.get().strip()
        if not predictions or not actuals:
            messagebox.showerror("Missing sheet", "Choose both a predictions sheet and an actual-results sheet.")
            return
        try:
            title_map = parse_title_map(self.title_map_var.get())
        except ValueError as exc:
            messagebox.showerror("Title matches", str(exc))
            return

        self.score_btn.config(state="disabled")
        for widget in self.results_frame.winfo_children():
            widget.destroy()
        self.progress.pack(fill="x", padx=16, pady=(0, 4))
        self.progress.start(12)
        self.status_var.set("Reading both sheets and scoring the predictions...")
        threading.Thread(target=self._worker, args=(predictions, actuals, title_map), daemon=True).start()
        self.root.after(100, self._poll_queue)

    def _worker(self, predictions: str, actuals: str, title_map: list[tuple[str, str]]) -> None:
        try:
            today = date.today()
            setup_logging(today, "predictions")
            self._work_queue.put(("success", run_predictions(predictions, actuals, today, title_map)))
        except Exception as exc:
            self._work_queue.put(("error", describe_error(exc)))

    def _poll_queue(self) -> None:
        try:
            kind, payload = self._work_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_queue)
            return
        self.progress.stop()
        self.progress.pack_forget()
        self.score_btn.config(state="normal")
        if kind == "error":
            self.status_var.set("Something went wrong.")
            messagebox.showerror("Scoring failed", payload)
            return
        result, findings, out_path = payload.result, payload.findings, payload.out_path
        scored = int(result.movies["scored"].sum())
        showing = int((result.movies["run_status"] == IN_THEATERS).sum())
        status = f"Done. Scored {scored} finished movie{'' if scored == 1 else 's'}"
        self.status_var.set(status + (f"; {showing} still in theaters." if showing else "."))
        self._show_results(result, findings, out_path)

    def _show_results(self, result, findings: list[str], out_path: Path) -> None:
        _add_open_buttons(self.results_frame, out_path)
        entries = [
            (f"{row.measure}: ", row.result + (f"\n{', '.join(row.movies)}" if row.movies else ""))
            for row in scorecard(result)
        ]
        _add_text_box(self.results_frame, "Scorecard", entries)
        _add_findings_box(self.results_frame, findings)


def main() -> None:
    root = tk.Tk()
    app = ReportApp(root)
    PredictionTab(root, app.notebook)
    root.mainloop()


if __name__ == "__main__":
    main()
