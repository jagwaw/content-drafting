from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from content_agent.models import Draft, Row, RowResult, Status

ROW = Row(row_key=2, id="t1", topic="What is a stop-loss?")
DRAFT = Draft(caption="c", slides=["one", "two"], script="s")
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def test_drafted_result_carries_a_draft() -> None:
    result = RowResult(row=ROW, status=Status.DRAFTED, draft=DRAFT, model="m", drafted_at=NOW)

    assert result.draft == DRAFT


def test_drafted_result_without_draft_is_invalid() -> None:
    with pytest.raises(ValidationError, match="draft is present exactly"):
        RowResult(row=ROW, status=Status.DRAFTED, model="m", drafted_at=NOW)


def test_flagged_result_must_not_carry_draft_text() -> None:
    with pytest.raises(ValidationError, match="draft is present exactly"):
        RowResult(
            row=ROW,
            status=Status.FLAGGED,
            draft=DRAFT,
            flags=("profit promise",),
            model="m",
            drafted_at=NOW,
        )


def test_error_result_requires_a_message() -> None:
    with pytest.raises(ValidationError, match="error message"):
        RowResult(row=ROW, status=Status.ERROR, model="m", drafted_at=NOW)


@pytest.mark.parametrize("status", [Status.NEW, Status.APPROVED, Status.REJECTED])
def test_agent_cannot_produce_human_owned_statuses(status: Status) -> None:
    with pytest.raises(ValidationError, match="never sets status"):
        RowResult(row=ROW, status=status, model="m", drafted_at=NOW)


def test_row_is_immutable() -> None:
    with pytest.raises(ValidationError):
        ROW.topic = "changed"  # type: ignore[misc]


def test_status_values_match_sheet_text() -> None:
    assert Status("Drafted") is Status.DRAFTED
    assert str(Status.FLAGGED) == "Flagged"
