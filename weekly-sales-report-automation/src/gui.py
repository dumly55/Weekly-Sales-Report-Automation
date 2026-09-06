"""Desktop GUI: generate the weekly sales report without touching a terminal.

Launch via the run_gui.bat / run_gui.pyw files at the project root. This module
is intentionally never imported by anything else in `src/` or by the tests --
Tkinter isn't installed on every CI runner, and nothing here needs testing that
the existing pipeline tests don't already cover.
"""

import os
import queue
import threading
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .main import describe_error, run_pipeline, setup_logging

WINDOW_TITLE = "Weekly Sales Report Generator"


class ReportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("600x560")
        self.root.minsize(560, 520)

        style = ttk.Style()
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Good.TLabel", foreground="#1f7a1f")
        style.configure("Bad.TLabel", foreground="#c00000")

        self._work_queue: queue.Queue = queue.Queue()
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 16, "pady": 8}

        ttk.Label(self.root, text="Weekly Sales Report", font=("Segoe UI", 16, "bold")).pack(anchor="w", **pad)
        ttk.Label(
            self.root,
            text="Generates a formatted Excel sales report for one week. No terminal needed.",
            wraplength=560,
        ).pack(anchor="w", padx=16)

        source_frame = ttk.LabelFrame(self.root, text="Data source (optional)")
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

        week_frame = ttk.LabelFrame(self.root, text="Report week")
        week_frame.pack(fill="x", **pad)
        date_row = ttk.Frame(week_frame)
        date_row.pack(fill="x", padx=10, pady=8)
        ttk.Label(date_row, text="Any date in the week (YYYY-MM-DD):").pack(side="left")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(date_row, textvariable=self.date_var, width=14).pack(side="left", padx=8)
        ttk.Label(week_frame, text="Defaults to this week -- just click Generate.", foreground="#666666").pack(
            anchor="w", padx=10, pady=(0, 8)
        )

        self.generate_btn = ttk.Button(self.root, text="Generate Report", command=self._on_generate)
        self.generate_btn.pack(pady=(4, 4))

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status_var).pack(pady=(4, 4))

        self.results_frame = ttk.Frame(self.root)
        self.results_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    def _on_browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a CSV or Excel file",
            filetypes=[("Spreadsheets", "*.csv *.xlsx *.xls"), ("All files", "*.*")],
        )
        if path:
            self.link_var.set(path)

    def _on_generate(self) -> None:
        raw_date = self.date_var.get().strip()
        try:
            as_of_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("Invalid date", f'"{raw_date}" isn\'t a valid date. Use YYYY-MM-DD.')
            return

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

    def _run_pipeline_worker(self, as_of_date: date, data_url: str | None, data_file: str | None) -> None:
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
            self.status_var.set("Done -- but no transactions were found for that week.")
        else:
            self.status_var.set("Done.")

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

        def fmt_wow(value):
            return "n/a" if value is None else f"{value:+.1f}%"

        def wow_tag(value):
            if value is None:
                return ""
            return "good" if value >= 0 else "bad"

        rows = [
            ("Revenue", f"£{report_data.current['revenue']:,.2f}", f"£{report_data.previous['revenue']:,.2f}", report_data.wow["revenue"]),
            ("Units Sold", f"{report_data.current['units']:,}", f"{report_data.previous['units']:,}", report_data.wow["units"]),
            ("Orders", f"{report_data.current['orders']:,}", f"{report_data.previous['orders']:,}", report_data.wow["orders"]),
            ("Unique Customers", f"{report_data.current['customers']:,}", f"{report_data.previous['customers']:,}", report_data.wow["customers"]),
        ]
        for metric, this_week, last_week, wow in rows:
            tree.insert("", "end", values=(metric, this_week, last_week, fmt_wow(wow)), tags=(wow_tag(wow),))

        tree.pack(fill="x", pady=(0, 12))

        button_row = ttk.Frame(self.results_frame)
        button_row.pack(fill="x")
        ttk.Button(button_row, text="Open Report", command=lambda: os.startfile(out_path)).pack(side="left")
        ttk.Button(button_row, text="Open Folder", command=lambda: os.startfile(out_path.parent)).pack(side="left", padx=8)


def main() -> None:
    root = tk.Tk()
    ReportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
