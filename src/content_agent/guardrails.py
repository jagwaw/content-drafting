"""Guardrail primitives: LLM rate limit, append-only audit log, prompt-injection screen.

Copied and adapted from the sibling inbox-triage-agent repo (not imported across repos).
"""

import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from content_agent.config import MAX_NOTES_CHARS
from content_agent.models import RowResult


class RateLimitExceededError(RuntimeError):
    """Raised when a caller would exceed the configured LLM call budget."""


class RateLimiter:
    """Sliding one-hour window on LLM calls, so a bug can't run up an unbounded bill.

    In-process only: the cap resets on restart. Fine for a single-process CLI.
    """

    def __init__(
        self, max_calls_per_hour: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_calls_per_hour = max_calls_per_hour
        self._clock = clock
        self._timestamps: deque[float] = deque()

    def check_and_record(self) -> None:
        """Record one call, or raise if it would exceed the hourly budget."""
        now = self._clock()
        while self._timestamps and self._timestamps[0] <= now - 3600:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls_per_hour:
            raise RateLimitExceededError(
                f"LLM call budget exceeded: {self.max_calls_per_hour}/hour."
            )
        self._timestamps.append(now)

    @property
    def calls_in_window(self) -> int:
        return len(self._timestamps)


RunEvent = Literal["run_started", "run_finished", "rate_limit_tripped", "run_error", "write_failed"]


@dataclass(frozen=True)
class AuditLogger:
    """Append-only JSONL log, one file per UTC day. Lines are only ever appended.

    Only fields named here are written, so credentials can't end up in the log.
    """

    log_dir: Path

    def log_row(self, result: RowResult) -> None:
        """Record one row outcome: row id, outcome, flags, model, timestamp."""
        self._append(
            {
                "event": "row_processed",
                "row_id": result.row.id,
                "row_key": result.row.row_key,
                "outcome": result.status.value,
                "flags": list(result.flags),
                "model": result.model,
                "error": result.error,
            }
        )

    def log_event(self, event: RunEvent, detail: str = "") -> None:
        """Record a run-level event such as a rate-limit trip."""
        self._append({"event": event, "detail": detail})

    def _append(self, record: dict[str, object]) -> None:
        now = datetime.now(UTC)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{now:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps({"timestamp": now.isoformat(), **record}) + "\n")


# \s+ rather than a literal space, so line breaks or extra spaces between words
# can't slip a phrase past the screen.
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above)", re.I),
    # Role reassignment ("you are now in admin mode"), not "you are now ready to trade".
    re.compile(
        r"you\s+are\s+now\s+(in\s+\w+\s+mode|an?\s+(\w+\s+)?(ai|assistant|bot|model)\b"
        r"|unrestricted|jailbroken)",
        re.I,
    ),
    re.compile(r"new\s+instructions?:", re.I),
    # A fake role label at the start of a line; mid-line "Trading system:" is ordinary text.
    re.compile(r"^\s*system\s*:", re.I | re.M),
    re.compile(r"</?(system|assistant|user)>", re.I),
)


@dataclass(frozen=True)
class SanitizedInput:
    text: str
    flagged: bool
    matched_patterns: tuple[str, ...] = ()


def sanitize_for_prompt(raw_text: str, *, max_len: int = MAX_NOTES_CHARS) -> SanitizedInput:
    """Screen untrusted sheet text for injection attempts before it reaches a prompt.

    A detector, not a silent stripper: the pipeline refuses to draft flagged rows and
    records the matched patterns, so a human can see what the row tried.
    """
    truncated = raw_text[:max_len]
    matched = tuple(p.pattern for p in _INJECTION_PATTERNS if p.search(truncated))
    return SanitizedInput(text=truncated, flagged=bool(matched), matched_patterns=matched)
