import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from content_agent.backends import (
    COLUMNS,
    BackendError,
    CsvBackend,
    GoogleSheetBackend,
)
from content_agent.models import Draft, Row, RowResult, Status

NOW = datetime(2026, 10, 15, 8, 30, tzinfo=UTC)
DRAFT = Draft(caption='Caption, with "quotes"\nand a newline', slides=["a", "b"], script="s")


def write_csv(path: Path, rows: list[dict[str, str]], header: tuple[str, ...] = COLUMNS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def drafted(row: Row) -> RowResult:
    return RowResult(row=row, status=Status.DRAFTED, draft=DRAFT, model="fake/m", drafted_at=NOW)


@pytest.fixture
def sheet(tmp_path: Path) -> Path:
    return write_csv(
        tmp_path / "topics.csv",
        [
            {"id": "t1", "topic": "Stop-losses", "audience": "beginners", "status": "New"},
            {"id": "t2", "topic": "Done already", "status": "Approved"},
            {"id": "", "topic": "Position sizing", "status": "New"},
            {"id": "t4", "topic": "Rejected one", "status": "Rejected"},
            {"id": "t5", "topic": "Journaling", "status": " New "},
        ],
    )


# --- Fetching -------------------------------------------------------------------------


def test_fetches_only_new_rows_keyed_by_sheet_row_number(sheet: Path) -> None:
    rows = CsvBackend(sheet).fetch_new_rows(limit=10)

    assert [(row.row_key, row.topic) for row in rows] == [
        (2, "Stop-losses"),
        (4, "Position sizing"),
        (6, "Journaling"),
    ]
    assert rows[0].audience == "beginners"


def test_fetch_respects_limit(sheet: Path) -> None:
    assert len(CsvBackend(sheet).fetch_new_rows(limit=1)) == 1


@pytest.mark.parametrize("status", ["Drafted", "Flagged", "Error", "Approved", "Rejected", ""])
def test_non_new_rows_are_never_fetched(tmp_path: Path, status: str) -> None:
    path = write_csv(tmp_path / "s.csv", [{"topic": "x", "status": status}])

    assert CsvBackend(path).fetch_new_rows(limit=10) == []


def test_columns_may_be_in_any_order(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "s.csv", [{"topic": "Reordered", "status": "New"}], header=COLUMNS[::-1]
    )

    (row,) = CsvBackend(path).fetch_new_rows(limit=10)
    assert row.topic == "Reordered"


def test_missing_column_is_reported_by_name(tmp_path: Path) -> None:
    header = tuple(name for name in COLUMNS if name != "video_script")
    path = write_csv(tmp_path / "s.csv", [], header=header)

    with pytest.raises(BackendError, match="video_script"):
        CsvBackend(path).fetch_new_rows(limit=10)


def test_missing_file_is_a_backend_error(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="CSV not found"):
        CsvBackend(tmp_path / "nope.csv").fetch_new_rows(limit=10)


def test_empty_file_is_a_backend_error(tmp_path: Path) -> None:
    (tmp_path / "empty.csv").write_text("")

    with pytest.raises(BackendError, match="missing columns"):
        CsvBackend(tmp_path / "empty.csv").fetch_new_rows(limit=10)


# --- Writing --------------------------------------------------------------------------


def test_drafted_result_fills_agent_columns_only(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    (row, *_) = backend.fetch_new_rows(limit=10)

    backend.write_result(drafted(row))

    written = read_csv(sheet)[0]
    assert written["status"] == "Drafted"
    assert written["caption"] == DRAFT.caption
    assert written["carousel_outline"] == "1. a\n2. b"
    assert written["video_script"] == "s"
    assert written["model"] == "fake/m"
    assert written["drafted_at"] == "2026-10-15T08:30:00+00:00"
    assert written["audience"] == "beginners"


def test_other_rows_are_untouched(sheet: Path) -> None:
    before = read_csv(sheet)
    backend = CsvBackend(sheet)
    backend.write_result(drafted(backend.fetch_new_rows(limit=1)[0]))

    assert read_csv(sheet)[1:] == before[1:]


def test_generated_id_is_written_for_row_without_id(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    row = backend.fetch_new_rows(limit=10)[1].model_copy(update={"id": "abc12345"})

    backend.write_result(drafted(row))

    assert read_csv(sheet)[2]["id"] == "abc12345"


def test_flagged_result_writes_flags_and_clears_stale_draft_text(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "s.csv", [{"topic": "x", "status": "New", "caption": "old"}])
    backend = CsvBackend(path)
    (row,) = backend.fetch_new_rows(limit=10)
    result = RowResult(
        row=row, status=Status.FLAGGED, flags=("a", "b"), model="fake/m", drafted_at=NOW
    )

    backend.write_result(result)

    written = read_csv(path)[0]
    assert written["status"] == "Flagged"
    assert written["flags"] == "a; b"
    assert written["caption"] == ""


def test_error_result_writes_message(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    row = backend.fetch_new_rows(limit=1)[0]

    backend.write_result(
        RowResult(row=row, status=Status.ERROR, error="boom", model="m", drafted_at=NOW)
    )

    assert read_csv(sheet)[0]["error"] == "boom"


def test_rerun_after_writing_finds_nothing_new(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "s.csv", [{"topic": "x", "status": "New"}])
    backend = CsvBackend(path)
    backend.write_result(drafted(backend.fetch_new_rows(limit=10)[0]))

    assert backend.fetch_new_rows(limit=10) == []


def test_refuses_to_overwrite_row_a_human_approved_mid_run(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    row = backend.fetch_new_rows(limit=1)[0]
    edited = read_csv(sheet)
    edited[0]["status"] = "Approved"
    write_csv(sheet, edited)

    with pytest.raises(BackendError, match="changed since it was read"):
        backend.write_result(drafted(row))
    assert read_csv(sheet)[0]["status"] == "Approved"


def test_refuses_to_write_when_rows_shifted(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    row = backend.fetch_new_rows(limit=1)[0]
    write_csv(sheet, [{"id": "new", "topic": "Inserted above", "status": "New"}, *read_csv(sheet)])

    with pytest.raises(BackendError, match="changed since it was read"):
        backend.write_result(drafted(row))


def test_unknown_extra_columns_are_preserved(tmp_path: Path) -> None:
    header = (*COLUMNS, "reviewer")
    path = write_csv(
        tmp_path / "s.csv", [{"topic": "x", "status": "New", "reviewer": "Ana"}], header
    )
    backend = CsvBackend(path)

    backend.write_result(drafted(backend.fetch_new_rows(limit=10)[0]))

    assert read_csv(path)[0]["reviewer"] == "Ana"


def test_no_temp_file_left_behind(sheet: Path) -> None:
    backend = CsvBackend(sheet)
    backend.write_result(drafted(backend.fetch_new_rows(limit=1)[0]))

    assert [p.name for p in sheet.parent.iterdir()] == [sheet.name]


# --- Google Sheet (fake worksheet) ------------------------------------------------------


class FakeWorksheet:
    def __init__(self, table: list[list[str]]) -> None:
        self.table = table
        self.calls: list[str] = []
        self.updates: list[dict[str, object]] = []

    def get_all_values(self) -> list[list[str]]:
        self.calls.append("get_all_values")
        return [list(values) for values in self.table]

    def row_values(self, row: int) -> list[str]:
        self.calls.append("row_values")
        values = list(self.table[row - 1])
        while values and values[-1] == "":  # gspread drops trailing empty cells
            values.pop()
        return values

    def batch_update(self, data: list[dict[str, object]], raw: bool = True) -> object:
        self.calls.append(f"batch_update(raw={raw})")
        self.updates = data
        return {}


def sheet_table(*rows: dict[str, str]) -> list[list[str]]:
    return [list(COLUMNS)] + [[row.get(name, "") for name in COLUMNS] for row in rows]


def test_sheet_fetch_is_one_batch_read() -> None:
    worksheet = FakeWorksheet(sheet_table({"topic": "x", "status": "New"}, {"status": "Drafted"}))

    rows = GoogleSheetBackend(worksheet).fetch_new_rows(limit=10)

    assert [row.row_key for row in rows] == [2]
    assert worksheet.calls == ["get_all_values"]


def test_sheet_write_is_one_read_and_one_raw_batch_update() -> None:
    worksheet = FakeWorksheet(sheet_table({"topic": "x", "status": "New"}))
    backend = GoogleSheetBackend(worksheet)
    (row,) = backend.fetch_new_rows(limit=10)

    backend.write_result(drafted(row.model_copy(update={"id": "abc"})))

    assert worksheet.calls == ["get_all_values", "row_values", "batch_update(raw=True)"]
    cells = {update["range"]: update["values"] for update in worksheet.updates}
    assert cells["A2"] == [["abc"]]  # id
    assert cells["F2"] == [["Drafted"]]  # status
    assert cells["H2"] == [["1. a\n2. b"]]  # carousel_outline
    assert not {"B2", "C2", "D2", "E2"} & cells.keys()  # human columns untouched


def test_sheet_refuses_to_overwrite_rejected_row() -> None:
    worksheet = FakeWorksheet(sheet_table({"topic": "x", "status": "New"}))
    backend = GoogleSheetBackend(worksheet)
    (row,) = backend.fetch_new_rows(limit=10)
    worksheet.table[1][COLUMNS.index("status")] = "Rejected"

    with pytest.raises(BackendError):
        backend.write_result(drafted(row))
    assert worksheet.updates == []


def test_sheet_write_before_fetch_is_an_error() -> None:
    row = Row(row_key=2, topic="x")

    with pytest.raises(BackendError, match="fetch_new_rows must run"):
        GoogleSheetBackend(FakeWorksheet(sheet_table())).write_result(drafted(row))
