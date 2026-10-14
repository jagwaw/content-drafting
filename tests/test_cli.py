import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from content_agent.backends import BackendError, CsvBackend
from content_agent.cli import EXIT_CONFIG_ERROR, EXIT_OK, EXIT_RUN_STOPPED, main, run
from content_agent.compliance import DISCLAIMER
from content_agent.guardrails import AuditLogger, RateLimitExceededError
from content_agent.models import Row, RowResult
from fakes import CLEAN, FakeLlm

DEMO_CSV = Path(__file__).parent.parent / "data" / "demo_topics.csv"


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    return Path(shutil.copy(DEMO_CSV, tmp_path / "topics.csv"))


def statuses(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        return {row["id"] or row["topic"]: row["status"] for row in csv.DictReader(csv_file)}


def audit_events(log_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for f in log_dir.glob("*.jsonl") for line in f.read_text().splitlines()
    ]


def run_demo(csv_path: Path, log_dir: Path, llm: FakeLlm, *, dry_run: bool) -> tuple[int, str]:
    out = io.StringIO()
    code = run(CsvBackend(csv_path), llm, AuditLogger(log_dir), limit=10, dry_run=dry_run, out=out)
    return code, out.getvalue()


# The demo has 8 rows: 6 reach the LLM, 1 is an injection, 1 has an empty topic.
LLM_ROWS = 6


def test_demo_run_drafts_flags_and_errors_the_right_rows(demo: Path, tmp_path: Path) -> None:
    code, output = run_demo(demo, tmp_path / "logs", FakeLlm(*[CLEAN] * LLM_ROWS), dry_run=False)

    assert code == EXIT_OK
    result = statuses(demo)
    assert result["demo-07"] == "Flagged"  # injection in notes
    assert result["demo-08"] == "Error"  # empty topic
    assert [result[f"demo-0{n}"] for n in range(1, 7)] == ["Drafted"] * LLM_ROWS
    assert "Done. Drafted: 6, Error: 1, Flagged: 1" in output


def test_dry_run_writes_nothing_and_prints_drafts(demo: Path, tmp_path: Path) -> None:
    before = demo.read_bytes()

    code, output = run_demo(demo, tmp_path / "logs", FakeLlm(*[CLEAN] * LLM_ROWS), dry_run=True)

    assert code == EXIT_OK
    assert demo.read_bytes() == before
    assert "dry run: nothing is written" in output
    assert DISCLAIMER in output
    assert "--- carousel" in output


def test_every_row_outcome_is_audited(demo: Path, tmp_path: Path) -> None:
    run_demo(demo, tmp_path / "logs", FakeLlm(*[CLEAN] * LLM_ROWS), dry_run=False)

    events = audit_events(tmp_path / "logs")
    row_events = [e for e in events if e["event"] == "row_processed"]
    assert len(row_events) == 8
    assert {e["outcome"] for e in row_events} == {"Drafted", "Flagged", "Error"}
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_finished"


def test_second_run_does_nothing(demo: Path, tmp_path: Path) -> None:
    run_demo(demo, tmp_path / "logs", FakeLlm(*[CLEAN] * LLM_ROWS), dry_run=False)
    llm = FakeLlm()

    code, output = run_demo(demo, tmp_path / "logs", llm, dry_run=False)

    assert code == EXIT_OK
    assert llm.calls == []
    assert "nothing to do" in output


def test_rate_limit_stops_run_and_leaves_remaining_rows_new(demo: Path, tmp_path: Path) -> None:
    llm = FakeLlm(CLEAN, RateLimitExceededError("budget spent"))

    code, _ = run_demo(demo, tmp_path / "logs", llm, dry_run=False)

    assert code == EXIT_RUN_STOPPED
    result = statuses(demo)
    assert result["demo-01"] == "Drafted"
    assert result["demo-02"] == "New"
    assert any(e["event"] == "rate_limit_tripped" for e in audit_events(tmp_path / "logs"))


class RefusingBackend(CsvBackend):
    """Simulates a human approving every row between fetch and write."""

    def write_result(self, result: RowResult) -> None:
        raise BackendError("row changed since it was read")


def test_failed_write_is_reported_and_run_continues(demo: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    llm = FakeLlm(*[CLEAN] * LLM_ROWS)

    code = run(RefusingBackend(demo), llm, AuditLogger(tmp_path), limit=10, dry_run=False, out=out)

    assert code == EXIT_OK
    assert "Not written: 8" in out.getvalue()
    assert len([e for e in audit_events(tmp_path) if e["event"] == "write_failed"]) == 8


def test_unreadable_csv_stops_the_run(tmp_path: Path) -> None:
    code, _ = run_demo(tmp_path / "missing.csv", tmp_path / "logs", FakeLlm(), dry_run=False)

    assert code == EXIT_RUN_STOPPED


def test_limit_caps_rows_per_run(demo: Path, tmp_path: Path) -> None:
    llm = FakeLlm(CLEAN, CLEAN)

    run(CsvBackend(demo), llm, AuditLogger(tmp_path), limit=2, dry_run=False, out=io.StringIO())

    assert list(statuses(demo).values()).count("New") == 6


def test_dry_run_still_audits_with_a_dry_run_marker(demo: Path, tmp_path: Path) -> None:
    run_demo(demo, tmp_path, FakeLlm(*[CLEAN] * LLM_ROWS), dry_run=True)

    first = audit_events(tmp_path)[0]
    assert (first["event"], first["detail"]) == ("run_started", "dry run")


# --- main -----------------------------------------------------------------------------


def test_missing_api_key_exits_with_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run"], env={}) == EXIT_CONFIG_ERROR
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_sheet_backend_flag_without_sheet_id_is_config_error() -> None:
    env = {"ANTHROPIC_API_KEY": "test"}

    assert main(["run", "--backend", "sheet"], env=env) == EXIT_CONFIG_ERROR


def test_command_is_required() -> None:
    with pytest.raises(SystemExit):
        main([], env={})


def test_demo_csv_has_eight_new_rows() -> None:
    rows: list[Row] = CsvBackend(DEMO_CSV).fetch_new_rows(limit=100)

    assert len(rows) == 8
