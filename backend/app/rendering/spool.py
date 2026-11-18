"""Big CSV / Excel files written to disk while the records are read (background runs over REPORT_MAX_ROWS).

Only one batch of records is in memory at a time, so a report of millions of records (gigabytes) does not fill the
server's memory. The file is the report's fields as one table with their column labels (no title, groups, totals or
charts: those need every record in memory and belong in a PDF of a narrower selection).

Where: REPORTING_EXPORT_DIR (default <data directory>/exports). With several servers behind a load balancer, point it
at a shared disk so every server can hand out every file."""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Iterable

from backend.app.rendering import engine as designer_render
from backend.app.rendering.outputs import _export_cell_value
from backend.app.storage.files import data_directory

EXCEL_MAX_ROWS = 1_048_575  # one sheet: 1,048,576 rows including the heading


def export_directory() -> Path:
    configured = os.getenv("REPORTING_EXPORT_DIR", "").strip()
    folder = Path(configured).expanduser().resolve() if configured else data_directory() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def export_path(name: str) -> Path:
    return export_directory() / name


def _formula_safe(value: Any) -> Any:
    """Text that Excel would read as a formula stays text (same rule as the normal exports)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def write_csv(path: Path, columns: list[str], labels: dict[str, str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    partial = path.with_suffix(path.suffix + ".part")
    try:
        with partial.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([designer_render.csv_safe(labels.get(key, key)) for key in columns])
            for row in rows:
                writer.writerow([designer_render.csv_safe(_export_cell_value(row.get(key, ""))) for key in columns])
                count += 1
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return count


def write_xlsx(path: Path, title: str, columns: list[str], labels: dict[str, str], rows: Iterable[dict[str, Any]]) -> int:
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("Install openpyxl for XLSX output") from exc
    book = Workbook(write_only=True)             # rows go to a temporary file, not into memory
    sheet = book.create_sheet((title or "Report")[:31].translate(str.maketrans("", "", "[]:*?/\\")) or "Report")
    names = [labels.get(key, key) for key in columns]
    for index, name in enumerate(names, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(len(name) + 3, 12), 36)
    sheet.freeze_panes = "A2"
    heading = []
    for name in names:
        cell = WriteOnlyCell(sheet, value=name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17324D")
        heading.append(cell)
    sheet.append(heading)
    count = 0
    for row in rows:
        if count >= EXCEL_MAX_ROWS:
            raise ValueError(f"Excel holds at most {EXCEL_MAX_ROWS:,} records in a sheet; choose CSV for this report")
        sheet.append([_formula_safe(_export_cell_value(row.get(key, ""))) for key in columns])
        count += 1
    partial = path.with_suffix(path.suffix + ".part")
    try:
        book.save(partial)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return count
