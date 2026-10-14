import pytest

from content_agent.compliance import (
    DISCLAIMER,
    HARD_FAIL_PATTERNS,
    add_disclaimer,
    check_draft,
    check_phrases,
    check_structure,
)
from content_agent.config import MAX_CAPTION_CHARS
from content_agent.models import Draft


def make_draft(
    caption: str = "Learn how a stop-loss limits your downside.",
    slide_count: int = 6,
    script_words: int = 120,
) -> Draft:
    return Draft(
        caption=caption,
        slides=[f"Slide {n} about position sizing" for n in range(1, slide_count + 1)],
        script=" ".join(["word"] * script_words),
    )


# --- Hard-fail phrases ----------------------------------------------------------------

# Every pattern label must have at least one positive case here.
FLAGGED_CASES = [
    ("guaranteed", "This setup is GUARANTEED to work."),
    ("guaranteed", "Guaranteed returns every month"),
    ("risk-free", "A risk-free way to trade"),
    ("risk-free", "totally risk free"),
    ("risk-free", "riskfree entries"),
    ("no risk", "There is no risk with this method."),
    ("no risk", "no\nrisk at all"),
    ("can't lose", "You can't lose with this setup"),
    ("can't lose", "you can\u2019t lose"),
    ("can't lose", "you cannot lose"),
    ("can't lose", "you cant lose"),
    ("get rich", "Get rich trading forex"),
    ("get rich", "a get-rich-quick plan"),
    ("double your money", "Double your money in a month"),
    ("sure profit", "sure profit every week"),
    ("sure profit", "sure-profits strategy"),
    ("easy money", "It's easy money"),
    ("100% win", "100% win rate"),
    ("100% win", "a 100 % winning system"),
    ("income claim", "make $500 a day"),
    ("income claim", "Earn ₱1,000 per day from home"),
    ("income claim", "earning 10% a week"),
    ("income claim", "I made $3k/month"),
    ("income claim", "earn up to 5% weekly"),
    ("income claim", "make 500 dollars every day"),
    ("income claim", "earn 20% in returns a month"),
    ("income claim", "make PHP 2000 daily"),
]


@pytest.mark.parametrize(("label", "text"), FLAGGED_CASES)
def test_profit_promise_is_flagged(label: str, text: str) -> None:
    reasons = check_phrases(text)

    assert any(f"({label})" in reason for reason in reasons), reasons


def test_every_pattern_has_a_flagged_case() -> None:
    tested_labels = {label for label, _ in FLAGGED_CASES}

    assert tested_labels == {label for label, _ in HARD_FAIL_PATTERNS}


@pytest.mark.parametrize(
    "text",
    [
        "There is no guarantee in trading.",
        "Past results don't guarantee future returns.",
        "Risk management comes first.",
        "There is no risk management in gambling.",
        "Know your risk before every trade.",
        "Freedom comes from discipline, not from risky bets.",
        "Losses are part of trading; you can lose money.",
        "Don't chase easy wins.",
        "Rich data helps you journal trades.",
        "Doubling down on a losing trade is dangerous.",
        "Surely profit isn't the only goal.",
        "A 10% drawdown is normal.",
        "Risk 1% per trade.",
        "Make 3 trades a day at most.",
        "Earn the right to size up by journaling 50 trades.",
        "Win rate around 55% is realistic.",
        "100% of traders face losing streaks.",
        "Money management keeps you in the game.",
    ],
)
def test_near_miss_is_not_flagged(text: str) -> None:
    assert check_phrases(text) == []


def test_reason_quotes_the_matched_text() -> None:
    (reason,) = check_phrases("This is Risk-Free!")

    assert reason == 'profit promise "Risk-Free" (risk-free)'


def test_reasons_never_contain_flag_separator() -> None:
    texts = [text for _, text in FLAGGED_CASES]

    assert all(";" not in reason for text in texts for reason in check_phrases(text))


# --- Structure ------------------------------------------------------------------------


def test_valid_draft_has_no_structure_reasons() -> None:
    assert check_structure(make_draft()) == []


@pytest.mark.parametrize("slide_count", [5, 8])
def test_slide_count_at_limits_is_valid(slide_count: int) -> None:
    assert check_structure(make_draft(slide_count=slide_count)) == []


@pytest.mark.parametrize("slide_count", [0, 4, 9])
def test_slide_count_outside_limits_is_flagged(slide_count: int) -> None:
    (reason,) = check_structure(make_draft(slide_count=slide_count))

    assert f"carousel has {slide_count} slides" in reason


@pytest.mark.parametrize("script_words", [80, 200])
def test_script_length_at_limits_is_valid(script_words: int) -> None:
    assert check_structure(make_draft(script_words=script_words)) == []


@pytest.mark.parametrize("script_words", [79, 201])
def test_script_length_outside_limits_is_flagged(script_words: int) -> None:
    (reason,) = check_structure(make_draft(script_words=script_words))

    assert f"script has {script_words} words" in reason


def test_caption_limit_leaves_room_for_disclaimer() -> None:
    longest_allowed = MAX_CAPTION_CHARS - len("\n\n" + DISCLAIMER)

    assert check_structure(make_draft(caption="x" * longest_allowed)) == []
    (reason,) = check_structure(make_draft(caption="x" * (longest_allowed + 1)))
    assert "caption too long" in reason


def test_blank_slide_is_flagged() -> None:
    draft = make_draft().model_copy(update={"slides": ["a", "b", "  ", "d", "e"]})

    assert check_structure(draft) == ["carousel has an empty slide"]


# --- Whole draft ----------------------------------------------------------------------


def test_check_draft_scans_caption_slides_and_script() -> None:
    draft = Draft(
        caption="Learn the basics.",
        slides=["a", "b", "This is easy money", "d", "e"],
        script=" ".join(["word"] * 100) + " guaranteed",
    )

    reasons = check_draft(draft)

    assert any("(easy money)" in reason for reason in reasons)
    assert any("(guaranteed)" in reason for reason in reasons)


def test_check_draft_combines_phrase_and_structure_reasons() -> None:
    draft = make_draft(caption="Get rich fast", slide_count=3)

    assert len(check_draft(draft)) == 2


def test_clean_draft_passes() -> None:
    assert check_draft(make_draft()) == []


# --- Disclaimer -----------------------------------------------------------------------


def test_disclaimer_added_to_caption_last_slide_and_script() -> None:
    result = add_disclaimer(make_draft())

    assert result.caption.endswith(DISCLAIMER)
    assert result.slides[-1].endswith(DISCLAIMER)
    assert result.script.endswith(DISCLAIMER)
    assert all(DISCLAIMER not in slide for slide in result.slides[:-1])


def test_disclaimer_is_not_duplicated() -> None:
    once = add_disclaimer(make_draft())

    assert add_disclaimer(once) == once


def test_disclaimer_does_not_change_the_original_draft() -> None:
    draft = make_draft()

    add_disclaimer(draft)

    assert DISCLAIMER not in draft.caption
    assert DISCLAIMER not in draft.slides[-1]


def test_draft_with_disclaimer_still_fits_caption_limit() -> None:
    draft = make_draft(caption="x" * (MAX_CAPTION_CHARS - len("\n\n" + DISCLAIMER)))

    assert len(add_disclaimer(draft).caption) == MAX_CAPTION_CHARS


def test_negated_banned_word_is_still_flagged() -> None:
    # Deliberately strict: negation detection is easy to fool ("not only guaranteed..."),
    # and a false positive costs one retry, while a false negative costs compliance.
    assert check_phrases("Profits are not guaranteed.") != []
