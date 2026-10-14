"""Deterministic compliance checks and disclaimer insertion. Pure functions, no I/O.

The business teaches trading, so drafts must never promise profits. These checks run on
the model's output in code; the model is never trusted to police itself.
"""

import re

from content_agent.config import (
    MAX_CAPTION_CHARS,
    MAX_SCRIPT_WORDS,
    MAX_SLIDES,
    MIN_SCRIPT_WORDS,
    MIN_SLIDES,
)
from content_agent.models import Draft

DISCLAIMER = "[ADD RISK DISCLAIMER]"
_DISCLAIMER_SEPARATOR = "\n\n"

_INCOME_CLAIM = re.compile(
    r"""
    \b(?:make|makes|making|made|earn|earns|earning|earned)\s+
    (?:up\s+to\s+|over\s+|at\s+least\s+)?
    (?:\$|₱|php\s*|usd\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|k\b|dollars|pesos)?
    (?:\s+(?:in\s+)?(?:profits?|returns?|gains?|income))?
    (?:\s*/\s*|\s+(?:a|per|each|every)\s+|\s+)
    (?:day|week|month|year|daily|weekly|monthly|yearly)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Each label is what lands in the row's `flags` column, so labels must not contain ";".
HARD_FAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("guaranteed", re.compile(r"\bguaranteed\b", re.I)),
    ("risk-free", re.compile(r"\brisk[\s-]*free\b", re.I)),
    ("no risk", re.compile(r"\bno\s+risk\b(?![\s-]+management)", re.I)),
    ("can't lose", re.compile(r"\b(?:can[\u2019']?t|cannot|can\s+not)\s+lose\b", re.I)),
    ("get rich", re.compile(r"\bget[\s-]+rich\b", re.I)),
    ("double your money", re.compile(r"\bdouble\s+your\s+money\b", re.I)),
    ("sure profit", re.compile(r"\bsure[\s-]+profits?\b", re.I)),
    ("easy money", re.compile(r"\beasy\s+money\b", re.I)),
    ("100% win", re.compile(r"\b100\s*%\s*win", re.I)),
    ("income claim", _INCOME_CLAIM),
)


def check_phrases(text: str) -> list[str]:
    """Return one reason per hard-fail phrase found in `text` (empty list = clean)."""
    return [
        f'profit promise "{match.group(0)}" ({label})'
        for label, pattern in HARD_FAIL_PATTERNS
        if (match := pattern.search(text))
    ]


def check_structure(draft: Draft) -> list[str]:
    """Return one reason per structural limit the draft breaks (empty list = valid).

    The caption limit includes room for the disclaimer that code appends later.
    """
    reasons = []
    caption_chars = len(draft.caption) + len(_DISCLAIMER_SEPARATOR + DISCLAIMER)
    if caption_chars > MAX_CAPTION_CHARS:
        reasons.append(f"caption too long ({caption_chars} > {MAX_CAPTION_CHARS} chars)")
    if not MIN_SLIDES <= len(draft.slides) <= MAX_SLIDES:
        reasons.append(f"carousel has {len(draft.slides)} slides (need {MIN_SLIDES}-{MAX_SLIDES})")
    if any(not slide.strip() for slide in draft.slides):
        reasons.append("carousel has an empty slide")
    script_words = len(draft.script.split())
    if not MIN_SCRIPT_WORDS <= script_words <= MAX_SCRIPT_WORDS:
        reasons.append(
            f"script has {script_words} words (need {MIN_SCRIPT_WORDS}-{MAX_SCRIPT_WORDS})"
        )
    return reasons


def check_draft(draft: Draft) -> list[str]:
    """Run every check on a draft. Any reason is a hard failure."""
    all_text = "\n".join([draft.caption, *draft.slides, draft.script])
    return check_phrases(all_text) + check_structure(draft)


def add_disclaimer(draft: Draft) -> Draft:
    """Append the disclaimer placeholder to the caption, the last slide and the script."""
    slides = list(draft.slides)
    if slides:
        slides[-1] = _with_disclaimer(slides[-1])
    return Draft(
        caption=_with_disclaimer(draft.caption),
        slides=slides,
        script=_with_disclaimer(draft.script),
    )


def _with_disclaimer(text: str) -> str:
    # Idempotent, so a model that echoes the placeholder doesn't produce it twice.
    if text.rstrip().endswith(DISCLAIMER):
        return text
    return text.rstrip() + _DISCLAIMER_SEPARATOR + DISCLAIMER
