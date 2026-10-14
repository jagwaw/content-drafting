"""Where rows come from and results go: a local CSV or a Google Sheet, same columns.

Both backends re-read a row right before writing it and refuse if it is no longer the
same `New` row. A human may edit the sheet mid-run, and rows the human owns
(Approved, Rejected) must never be overwritten.
"""

import csv
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import gspread
from gspread.utils import rowcol_to_a1

from content_agent.models import Row, RowResult, Status

COLUMNS = (
    "id",
    "topic",
    "audience",
    "tone",
    "notes",
    "status",
    "caption",
    "carousel_outline",
    "video_script",
    "flags",
    "model",
    "drafted_at",
    "error",
)
_SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]


class BackendError(RuntimeError):
    """The sheet is missing, malformed, or changed under us."""


class Backend(Protocol):
    def fetch_new_rows(self, limit: int) -> list[Row]: ...

    def write_result(self, result: RowResult) -> None: ...


def parse_rows(table: Sequence[Sequence[str]], limit: int) -> list[Row]:
    """Return up to `limit` rows whose status is `New`, keyed by 1-based sheet row number."""
    columns = column_index(table[0] if table else [])
    rows = []
    for row_number, values in enumerate(table[1:], start=2):
        if _cell(values, columns, "status").strip() != Status.NEW:
            continue
        fields = {
            name: _cell(values, columns, name) for name in Row.model_fields if name != "row_key"
        }
        rows.append(Row(row_key=row_number, **fields))
        if len(rows) == limit:
            break
    return rows


def column_index(header: Sequence[str]) -> dict[str, int]:
    """Map column name to position. Columns may be in any order; all must be present."""
    positions = {name.strip(): i for i, name in enumerate(header)}
    if missing := [name for name in COLUMNS if name not in positions]:
        raise BackendError(f"sheet is missing columns: {', '.join(missing)}")
    return {name: positions[name] for name in COLUMNS}


def result_cells(result: RowResult) -> dict[str, str]:
    """The values the agent writes for one result. Draft columns are blank unless Drafted."""
    draft = result.draft
    return {
        "id": result.row.id,
        "status": result.status.value,
        "caption": draft.caption if draft else "",
        "carousel_outline": (
            "\n".join(f"{n}. {slide}" for n, slide in enumerate(draft.slides, 1)) if draft else ""
        ),
        "video_script": draft.script if draft else "",
        "flags": "; ".join(result.flags),
        "model": result.model,
        "drafted_at": result.drafted_at.isoformat(timespec="seconds"),
        "error": result.error,
    }


def check_still_new(current: Sequence[str], columns: dict[str, int], row: Row) -> None:
    """Raise unless the sheet row is still the same `New` row that was fetched."""
    status = _cell(current, columns, "status").strip()
    same_row = _cell(current, columns, "topic") == row.topic and (
        _cell(current, columns, "id") in {row.id, ""}
    )
    if status != Status.NEW or not same_row:
        raise BackendError(
            f"row {row.row_key} changed since it was read (status {status!r}); not writing"
        )


def _cell(values: Sequence[str], columns: dict[str, int], name: str) -> str:
    position = columns[name]
    return values[position] if position < len(values) else ""


class CsvBackend:
    """Local CSV with the sheet's columns. For demos, tests and the evaluation."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch_new_rows(self, limit: int) -> list[Row]:
        return parse_rows(self._read(), limit)

    def write_result(self, result: RowResult) -> None:
        table = self._read()
        columns = column_index(table[0])
        row_index = result.row.row_key - 1
        if row_index >= len(table):
            raise BackendError(f"row {result.row.row_key} no longer exists")
        check_still_new(table[row_index], columns, result.row)
        values = table[row_index] + [""] * (len(table[0]) - len(table[row_index]))
        for name, value in result_cells(result).items():
            values[columns[name]] = value
        table[row_index] = values
        self._write(table)

    def _read(self) -> list[list[str]]:
        try:
            with self._path.open(newline="", encoding="utf-8") as csv_file:
                return list(csv.reader(csv_file))
        except FileNotFoundError as exc:
            raise BackendError(f"CSV not found: {self._path}") from exc

    def _write(self, table: list[list[str]]) -> None:
        # Write a temp file and swap it in, so a crash mid-write can't corrupt the CSV.
        temp_path = self._path.with_suffix(".tmp")
        with temp_path.open("w", newline="", encoding="utf-8") as csv_file:
            csv.writer(csv_file).writerows(table)
        os.replace(temp_path, self._path)


class Worksheet(Protocol):
    """The three gspread worksheet calls this backend uses."""

    def get_all_values(self) -> list[list[str]]: ...

    def row_values(self, row: int) -> list[str]: ...

    def batch_update(self, data: list[dict[str, object]], raw: bool = True) -> object: ...


class GoogleSheetBackend:
    """Google Sheet via a service account. One read per fetch, one read + one write per row."""

    def __init__(self, worksheet: Worksheet) -> None:
        self._worksheet = worksheet
        self._columns: dict[str, int] | None = None

    @classmethod
    def connect(cls, sheet_id: str, service_account_file: Path) -> "GoogleSheetBackend":
        """Open the first worksheet, with a Sheets-only scope (no Drive access)."""
        client = gspread.service_account(filename=service_account_file, scopes=_SHEETS_SCOPE)
        return cls(client.open_by_key(sheet_id).sheet1)

    def fetch_new_rows(self, limit: int) -> list[Row]:
        table = self._worksheet.get_all_values()
        self._columns = column_index(table[0] if table else [])
        return parse_rows(table, limit)

    def write_result(self, result: RowResult) -> None:
        if self._columns is None:
            raise BackendError("fetch_new_rows must run before write_result")
        row_number = result.row.row_key
        check_still_new(self._worksheet.row_values(row_number), self._columns, result.row)
        updates: list[dict[str, object]] = [
            {"range": rowcol_to_a1(row_number, self._columns[name] + 1), "values": [[value]]}
            for name, value in result_cells(result).items()
        ]
        # raw=True stores text as-is, so generated text starting with "=" is never a formula.
        self._worksheet.batch_update(updates, raw=True)
