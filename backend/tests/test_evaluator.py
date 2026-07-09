from app.evaluator import summarize
from app.models import EvalResult, UsageStats


def make_result(mode: str, correct: bool, escalated: bool, mechanism_correct: bool | None = None) -> EvalResult:
    return EvalResult(
        incident_id="x",
        mode=mode,
        root_cause_correct=correct,
        mechanism_correct=correct if mechanism_correct is None else mechanism_correct,
        escalated=escalated,
        judge_score=3.0,
        usage=UsageStats(tokens_used=100, latency_ms=10.0, estimated_cost_usd=0.01, calls_made=1),
    )


def test_summarize_distinguishes_escalated_from_confidently_wrong():
    """Escalating and being confidently wrong must score differently: escalation
    costs nothing in utility, confidently-wrong costs -1, correct earns +1."""
    results = [
        make_result("multi_agent", correct=True, escalated=False),  # correct
        make_result("multi_agent", correct=False, escalated=True),  # escalated, not wrong
        make_result("multi_agent", correct=False, escalated=False),  # confidently wrong
        make_result("multi_agent", correct=True, escalated=False),  # correct
    ]
    summary = summarize(results, "multi_agent")

    assert summary.incidents_scored == 4
    assert summary.accuracy == 0.5  # naive: 2/4 correct
    assert summary.coverage == 0.75  # 3/4 committed to an answer (1 escalated)
    assert summary.precision == round(2 / 3, 3)  # 2 correct out of 3 committed
    assert summary.confidently_wrong_rate == 0.25  # 1/4
    assert summary.escalation_rate == 0.25  # 1/4
    assert summary.utility_score == round((1 + 0 - 1 + 1) / 4, 3)  # 0.25


def test_baseline_has_full_coverage_and_precision_equals_accuracy():
    """The baseline has no abstain option -- it always commits, so coverage is
    always 100% and precision collapses to the same thing as naive accuracy."""
    results = [
        make_result("baseline", correct=True, escalated=False),
        make_result("baseline", correct=False, escalated=False),
    ]
    summary = summarize(results, "baseline")

    assert summary.coverage == 1.0
    assert summary.precision == summary.accuracy == 0.5
    assert summary.escalation_rate == 0.0
    assert summary.confidently_wrong_rate == 0.5


def test_mechanism_accuracy_catches_right_domain_wrong_explanation():
    """A system can name the correct domain while fabricating the wrong specific
    mechanism -- domain accuracy alone can't see that gap, mechanism_accuracy can."""
    results = [
        make_result("multi_agent", correct=True, escalated=False, mechanism_correct=True),
        make_result("multi_agent", correct=True, escalated=False, mechanism_correct=False),
    ]
    summary = summarize(results, "multi_agent")

    assert summary.accuracy == 1.0  # both got the domain right
    assert summary.mechanism_accuracy == 0.5  # only one actually explained it correctly


def test_all_escalated_has_zero_precision_not_a_crash():
    results = [make_result("multi_agent", correct=False, escalated=True)]
    summary = summarize(results, "multi_agent")

    assert summary.coverage == 0.0
    assert summary.precision == 0.0  # no committed answers to be precise about
    assert summary.confidently_wrong_rate == 0.0
    assert summary.utility_score == 0.0
