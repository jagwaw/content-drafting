"""Test doubles shared across test modules."""

from collections.abc import Sequence

from content_agent.models import Draft, Row

# A draft that passes every compliance check.
CLEAN = Draft(
    caption="A stop-loss decides your exit before emotions do.",
    slides=[f"Slide point {n}" for n in range(6)],
    script=" ".join(["word"] * 100),
)


class FakeLlm:
    """Returns (or raises) the queued outcomes in order and records every call."""

    model_label = "fake/model"

    def __init__(self, *outcomes: Draft | Exception) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[Row, tuple[str, ...]]] = []

    def generate_draft(self, row: Row, retry_reasons: Sequence[str] = ()) -> Draft:
        self.calls.append((row, tuple(retry_reasons)))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
