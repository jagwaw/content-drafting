"""Command line entry point: `python -m content_agent.cli run [--dry-run] [--backend csv|sheet]`.

Wires settings, backend, LLM and audit log together, then drafts each `New` row.
Writes only to the review sheet or CSV; it never publishes anything.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TextIO

from content_agent.backends import Backend, BackendError, CsvBackend, GoogleSheetBackend
from content_agent.config import BackendName, ConfigError, Settings, load_settings
from content_agent.guardrails import AuditLogger, RateLimiter, RateLimitExceededError
from content_agent.llm import LlmClient
from content_agent.models import RowResult, Status
from content_agent.pipeline import DraftGenerator, process_row

EXIT_OK = 0
EXIT_RUN_STOPPED = 1
EXIT_CONFIG_ERROR = 2


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Parse arguments, build dependencies, run once. Returns the process exit code.

    `env` defaults to `.env` plus the process environment; tests pass their own.
    """
    args = _parse_args(argv)
    try:
        settings = load_settings(env)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    backend_name: BackendName = args.backend or settings.backend
    try:
        backend = build_backend(settings, backend_name)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    llm = LlmClient(settings, RateLimiter(settings.max_llm_calls_per_hour))
    audit = AuditLogger(settings.audit_log_dir)
    return run(backend, llm, audit, limit=settings.max_rows_per_run, dry_run=args.dry_run)


def build_backend(settings: Settings, backend_name: BackendName) -> Backend:
    """Create the chosen backend. The sheet backend needs SHEET_ID even if BACKEND=csv."""
    if backend_name == "csv":
        return CsvBackend(settings.csv_path)
    if not settings.sheet_id:
        raise ConfigError("SHEET_ID is required for --backend sheet")
    return GoogleSheetBackend.connect(settings.sheet_id, settings.google_service_account_file)


def run(
    backend: Backend,
    llm: DraftGenerator,
    audit: AuditLogger,
    *,
    limit: int,
    dry_run: bool,
    out: TextIO = sys.stdout,
) -> int:
    """Draft every fetched row, write (unless dry run) and audit each one, then summarize."""
    audit.log_event("run_started", "dry run" if dry_run else "")
    try:
        rows = backend.fetch_new_rows(limit)
    except BackendError as exc:
        audit.log_event("run_error", str(exc))
        print(f"Could not read rows: {exc}", file=sys.stderr)
        return EXIT_RUN_STOPPED
    print(f"{len(rows)} new row(s){' (dry run: nothing is written)' if dry_run else ''}", file=out)
    outcomes: Counter[str] = Counter()
    for row in rows:
        try:
            result = process_row(row, llm)
        except RateLimitExceededError as exc:
            audit.log_event("rate_limit_tripped", str(exc))
            print(f"Stopped: {exc}", file=sys.stderr)
            _print_summary(outcomes, out)
            return EXIT_RUN_STOPPED
        audit.log_row(result)
        outcomes[_save(result, backend, audit, dry_run, out)] += 1
    _print_summary(outcomes, out)
    audit.log_event("run_finished", ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))
    return EXIT_OK


def _save(
    result: RowResult, backend: Backend, audit: AuditLogger, dry_run: bool, out: TextIO
) -> str:
    """Print or write one result. Returns the outcome label for the summary."""
    if dry_run:
        print(format_result(result), file=out)
        return result.status.value
    try:
        backend.write_result(result)
    except BackendError as exc:
        audit.log_event("write_failed", f"row {result.row.row_key}: {exc}")
        print(f"Row {result.row.row_key}: not written: {exc}", file=sys.stderr)
        return "Not written"
    print(_one_line(result), file=out)
    return result.status.value


def format_result(result: RowResult) -> str:
    """Full, human-readable view of a result for dry runs."""
    lines = [f"\n=== {_one_line(result)}"]
    if result.draft:
        slides = "\n".join(f"  {n}. {s}" for n, s in enumerate(result.draft.slides, 1))
        lines += [
            f"--- caption\n{result.draft.caption}",
            f"--- carousel\n{slides}",
            f"--- script\n{result.draft.script}",
        ]
    return "\n".join(lines)


def _one_line(result: RowResult) -> str:
    detail = {
        Status.FLAGGED: "; ".join(result.flags),
        Status.ERROR: result.error,
    }.get(result.status, "")
    suffix = f": {detail}" if detail else ""
    row = result.row
    return f"Row {row.row_key} [{row.id}] {row.topic!r} -> {result.status}{suffix}"


def _print_summary(outcomes: Counter[str], out: TextIO) -> None:
    summary = ", ".join(f"{label}: {count}" for label, count in sorted(outcomes.items()))
    print(f"\nDone. {summary or 'nothing to do'}", file=out)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="content_agent", description="Draft social content for human review."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Draft every New row once.")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Run the pipeline and print; write nothing."
    )
    run_parser.add_argument(
        "--backend", choices=["csv", "sheet"], help="Override BACKEND from the environment."
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
