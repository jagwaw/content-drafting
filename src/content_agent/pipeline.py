"""Per-row orchestration: validate -> screen -> generate -> check (retry once) -> disclaimer.

No I/O happens here: the LLM is passed in, and the caller writes and audits the result.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from content_agent.compliance import add_disclaimer, check_draft
from content_agent.config import MAX_NOTES_CHARS, MAX_SHORT_FIELD_CHARS, MAX_TOPIC_CHARS
from content_agent.guardrails import sanitize_for_prompt
from content_agent.llm import LlmError
from content_agent.models import Draft, Row, RowResult, Status


class DraftGenerator(Protocol):
    @property
    def model_label(self) -> str: ...

    def generate_draft(self, row: Row, retry_reasons: Sequence[str] = ()) -> Draft: ...


def process_row(
    row: Row,
    llm: DraftGenerator,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RowResult:
    """Turn one `New` row into a Drafted, Flagged or Error result.

    Makes at most two LLM calls. RateLimitExceededError is not caught: it ends the run.
    """
    if not row.id:
        row = row.model_copy(update={"id": uuid.uuid4().hex[:8]})

    def result(
        status: Status, draft: Draft | None = None, flags: tuple[str, ...] = (), error: str = ""
    ) -> RowResult:
        return RowResult(
            row=row,
            status=status,
            draft=draft,
            flags=flags,
            error=error,
            model=llm.model_label,
            drafted_at=now(),
        )

    if problem := validate_row(row):
        return result(Status.ERROR, error=problem)
    if injection_flags := screen_row(row):
        return result(Status.FLAGGED, flags=injection_flags)
    try:
        draft, reasons = _generate_checked(row, llm)
    except LlmError as exc:
        return result(Status.ERROR, error=str(exc))
    if reasons:
        return result(Status.FLAGGED, flags=tuple(reasons))
    return result(Status.DRAFTED, draft=add_disclaimer(draft))


def validate_row(row: Row) -> str:
    """Return a message describing the first invalid field, or "" if the row is valid."""
    if not row.topic.strip():
        return "topic is required"
    limits = {
        "topic": (row.topic, MAX_TOPIC_CHARS),
        "audience": (row.audience, MAX_SHORT_FIELD_CHARS),
        "tone": (row.tone, MAX_SHORT_FIELD_CHARS),
        "notes": (row.notes, MAX_NOTES_CHARS),
    }
    for name, (value, limit) in limits.items():
        if len(value) > limit:
            return f"{name} is too long ({len(value)} > {limit} chars)"
    return ""


def screen_row(row: Row) -> tuple[str, ...]:
    """Return one flag per field that looks like a prompt-injection attempt."""
    fields = {"topic": row.topic, "audience": row.audience, "tone": row.tone, "notes": row.notes}
    return tuple(
        f"possible prompt injection in {name}"
        for name, value in fields.items()
        if sanitize_for_prompt(value).flagged
    )


def _generate_checked(row: Row, llm: DraftGenerator) -> tuple[Draft, list[str]]:
    draft = llm.generate_draft(row)
    reasons = check_draft(draft)
    if reasons:
        draft = llm.generate_draft(row, retry_reasons=reasons)
        reasons = check_draft(draft)
    return draft, reasons
