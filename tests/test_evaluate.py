from collections import Counter

import pytest

from content_agent.eval_report import render_report
from content_agent.evaluate import EvalCase, load_cases, run_eval
from content_agent.guardrails import RateLimitExceededError, sanitize_for_prompt
from content_agent.models import RowResult, Status
from content_agent.pipeline import screen_row, validate_row
from fakes import CLEAN, FakeLlm

CASES = load_cases()
PROFIT = CLEAN.model_copy(update={"caption": "Guaranteed profit, every week."})
META = {"Model": "fake/model"}


def by_kind(kind: str) -> list[EvalCase]:
    return [case for case in CASES if case.kind == kind]


def outcomes_for_all_cases_passing() -> FakeLlm:
    """LLM answers in dataset order. Injection and invalid cases make no calls."""
    normal = [CLEAN] * 6
    # Adversarial: clean first time; flagged after retry; clean on retry.
    adversarial = [CLEAN, PROFIT, PROFIT, PROFIT, CLEAN]
    return FakeLlm(*normal, *adversarial)


# --- Dataset labels (deterministic, no LLM) -------------------------------------------


def test_dataset_has_the_planned_mix() -> None:
    assert Counter(case.kind for case in CASES) == {
        "normal": 6,
        "adversarial": 3,
        "injection": 2,
        "invalid": 1,
    }
    assert len({case.id for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_injection_labels_agree_with_the_screen(case: EvalCase) -> None:
    flagged = screen_row(case.to_row(2)) != ()

    assert flagged == (case.kind == "injection")
    if case.kind == "injection":
        assert sanitize_for_prompt(case.notes).flagged


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_invalid_labels_agree_with_validation(case: EvalCase) -> None:
    assert (validate_row(case.to_row(2)) != "") == (case.kind == "invalid")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_expected_statuses_match_case_kind(case: EvalCase) -> None:
    expected = {
        "normal": (Status.DRAFTED,),
        "adversarial": (Status.DRAFTED, Status.FLAGGED),
        "injection": (Status.FLAGGED,),
        "invalid": (Status.ERROR,),
    }
    assert case.expect == expected[case.kind]


# --- Scoring --------------------------------------------------------------------------


def test_well_behaved_model_passes_every_case() -> None:
    results = run_eval(CASES, outcomes_for_all_cases_passing())

    assert [r.case.id for r in results if not r.passed] == []


def test_injection_cases_make_no_llm_calls() -> None:
    results = run_eval(CASES, outcomes_for_all_cases_passing())

    for result in results:
        if result.case.kind == "injection":
            assert result.llm_calls == 0
            assert result.checks["no LLM call"] is True


def test_drafted_case_runs_all_draft_checks() -> None:
    (result,) = run_eval(by_kind("normal")[:1], FakeLlm(CLEAN))

    assert result.checks == {
        "expected status": True,
        "structure valid": True,
        "disclaimer present": True,
        "no hard-fail phrases": True,
    }


def test_flagged_normal_topic_is_a_failure() -> None:
    (result,) = run_eval(by_kind("normal")[:1], FakeLlm(PROFIT, PROFIT))

    assert result.passed is False
    assert result.checks == {"expected status": False}
    assert result.llm_calls == 2


def test_report_lists_every_failure_with_its_reason() -> None:
    results = run_eval(by_kind("normal")[:2], FakeLlm(PROFIT, PROFIT, CLEAN))

    report = render_report(results, META)

    assert "**Overall pass rate (every check passed): 1/2 (50%)**" in report
    assert "- `normal-stop-loss` (normal): failed expected status." in report
    assert "(guaranteed)" in report
    assert "| expected status | 1/2 (50%) |" in report


def test_report_says_none_when_nothing_failed() -> None:
    report = render_report(run_eval(CASES, outcomes_for_all_cases_passing()), META)

    assert "## Failures\n\nNone." in report
    assert "| injection | 2/2 (100%) |" in report
    assert "<details><summary><code>normal-stop-loss</code>" in report


def test_delay_sleeps_between_cases_only() -> None:
    sleeps: list[float] = []

    run_eval(by_kind("invalid") + by_kind("injection"), FakeLlm(), delay=2, sleep=sleeps.append)

    assert sleeps == [2, 2]


def test_rate_limit_stops_early_with_partial_results() -> None:
    llm = FakeLlm(CLEAN, RateLimitExceededError("budget spent"))

    results = run_eval(CASES, llm)

    assert len(results) == 1


def test_every_result_is_passed_to_the_callback() -> None:
    seen: list[RowResult] = []

    run_eval(by_kind("injection"), FakeLlm(), on_result=seen.append)

    assert len(seen) == 2
