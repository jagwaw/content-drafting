"""Evaluation harness: run labelled topics through the real pipeline and a real model.

    python -m content_agent.evaluate [--delay SECONDS]

Each case in evals/topics.jsonl goes through `process_row` exactly as the CLI runs it,
with nothing written to any sheet. Results, failures included, go to evals/results.md.
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from content_agent.compliance import DISCLAIMER, check_phrases, check_structure
from content_agent.config import ConfigError, load_settings
from content_agent.eval_report import render_report
from content_agent.guardrails import AuditLogger, RateLimiter, RateLimitExceededError
from content_agent.llm import LlmClient
from content_agent.models import Draft, Row, RowResult, Status
from content_agent.pipeline import DraftGenerator, process_row

DATASET = Path("evals/topics.jsonl")
REPORT = Path("evals/results.md")
# A quota or outage hits every case; after this many in a row, stop instead of scoring them.
MAX_CONSECUTIVE_PROVIDER_ERRORS = 2

CaseKind = Literal["normal", "adversarial", "injection", "invalid"]


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: CaseKind
    topic: str
    audience: str = ""
    tone: str = ""
    notes: str = ""
    expect: tuple[Status, ...]

    def to_row(self, row_key: int) -> Row:
        return Row(
            row_key=row_key,
            id=self.id,
            topic=self.topic,
            audience=self.audience,
            tone=self.tone,
            notes=self.notes,
        )


def load_cases(path: Path = DATASET) -> list[EvalCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [EvalCase.model_validate_json(line) for line in lines if line.strip()]


class EvalLlm(DraftGenerator, Protocol):
    """A draft generator that also reports how many HTTP requests it has sent."""

    @property
    def requests_sent(self) -> int: ...


class CountingLlm:
    """Wraps a draft generator to count calls, so "no LLM call" can be checked."""

    def __init__(self, inner: DraftGenerator) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def model_label(self) -> str:
        return self._inner.model_label

    def generate_draft(self, row: Row, retry_reasons: Sequence[str] = ()) -> Draft:
        self.calls += 1
        return self._inner.generate_draft(row, retry_reasons)


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    result: RowResult
    llm_calls: int  # draft attempts: 1, or 2 after a compliance retry
    requests: int  # HTTP requests, including transient-error retries
    seconds: float

    @property
    def provider_error(self) -> bool:
        """The model was called but no answer came back (quota, outage, bad output)."""
        return self.result.status == Status.ERROR and self.llm_calls > 0

    @property
    def checks(self) -> dict[str, bool]:
        """Every check that applies to this case, by name. Order is the report order."""
        checks = {"expected status": self.result.status in self.case.expect}
        if draft := self.result.draft:
            checks["structure valid"] = check_structure(_without_disclaimer(draft)) == []
            checks["disclaimer present"] = all(
                text.endswith(DISCLAIMER)
                for text in (draft.caption, draft.slides[-1], draft.script)
            )
            all_text = "\n".join([draft.caption, *draft.slides, draft.script])
            checks["no hard-fail phrases"] = check_phrases(all_text) == []
        if self.case.kind == "injection":
            checks["no LLM call"] = self.llm_calls == 0
            checks["no draft"] = draft is None
        if self.case.kind == "invalid":
            checks["clear error message"] = bool(self.result.error)
        return checks

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _without_disclaimer(draft: Draft) -> Draft:
    suffix = "\n\n" + DISCLAIMER
    return Draft(
        caption=draft.caption.removesuffix(suffix),
        slides=[*draft.slides[:-1], draft.slides[-1].removesuffix(suffix)],
        script=draft.script.removesuffix(suffix),
    )


@dataclass(frozen=True)
class EvalRun:
    results: list[CaseResult]
    stopped_early: str = ""  # why the run ended before the last case, if it did


def run_eval(
    cases: Sequence[EvalCase],
    llm: EvalLlm,
    *,
    delay: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    on_result: Callable[[RowResult], None] = lambda result: None,
) -> EvalRun:
    """Run each case once through the real pipeline.

    Stops early if the rate limit trips, or after consecutive provider errors, so an
    exhausted quota is reported as an incomplete run, not as failed drafts.
    """
    results: list[CaseResult] = []
    streak = 0
    for index, case in enumerate(cases):
        if index and delay:
            sleep(delay)  # stay under free-tier requests-per-minute limits
        counting = CountingLlm(llm)
        requests_before = llm.requests_sent
        start = time.monotonic()
        try:
            result = process_row(case.to_row(index + 2), counting)
        except RateLimitExceededError as exc:
            return EvalRun(results, f"local LLM call budget reached ({exc})")
        on_result(result)
        case_result = CaseResult(
            case=case,
            result=result,
            llm_calls=counting.calls,
            requests=llm.requests_sent - requests_before,
            seconds=time.monotonic() - start,
        )
        results.append(case_result)
        print(f"{case.id}: {result.status} ({'pass' if case_result.passed else 'FAIL'})")
        streak = streak + 1 if case_result.provider_error else 0
        if streak >= MAX_CONSECUTIVE_PROVIDER_ERRORS:
            reason = f"{streak} cases in a row got provider errors, likely quota or outage"
            return EvalRun(results, f"{reason}. Last error: {result.error[:120]}")
    return EvalRun(results)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dataset against the configured model and write the report."""
    parser = argparse.ArgumentParser(prog="content_agent.evaluate")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds between cases.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    cases = load_cases(args.dataset)
    llm = LlmClient(settings, RateLimiter(settings.max_llm_calls_per_hour))
    audit = AuditLogger(settings.audit_log_dir)
    run = run_eval(cases, llm, delay=args.delay, on_result=audit.log_row)
    meta = {
        "Model": llm.model_label,
        "Run at": datetime.now(UTC).isoformat(timespec="seconds"),
        "Cases run": f"{len(run.results)} of {len(cases)}",
    }
    args.output.write_text(render_report(run, meta), encoding="utf-8")
    if run.stopped_early:
        print(f"Stopped early: {run.stopped_early}", file=sys.stderr)
    print(f"Report written to {args.output}")
    return 0 if len(run.results) == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
