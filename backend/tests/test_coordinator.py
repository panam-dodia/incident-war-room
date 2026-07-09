from app.coordinator import allocate, sanity_check
from app.incidents import get_incident
from app.models import Bid, Specialist


def make_bid(specialist: Specialist, confidence: float, cost: float) -> Bid:
    return Bid(specialist=specialist, confidence=confidence, estimated_cost=cost, reasoning="test")


def test_clear_winner_when_one_bid_dominates():
    bids = [
        make_bid(Specialist.SECURITY, 0.9, 1.0),
        make_bid(Specialist.PERFORMANCE, 0.3, 1.0),
        make_bid(Specialist.DATABASE, 0.2, 1.0),
        make_bid(Specialist.NETWORKING, 0.1, 1.0),
        make_bid(Specialist.FRONTEND, 0.1, 1.0),
    ]
    allocation = allocate(bids)
    assert allocation.winner == Specialist.SECURITY
    assert allocation.contested is False
    assert allocation.contested_specialists == []


def test_contested_when_top_scores_are_close(monkeypatch):
    monkeypatch.setenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.15")
    bids = [
        make_bid(Specialist.SECURITY, 0.60, 1.0),
        make_bid(Specialist.PERFORMANCE, 0.58, 1.0),
        make_bid(Specialist.DATABASE, 0.10, 1.0),
        make_bid(Specialist.NETWORKING, 0.10, 1.0),
        make_bid(Specialist.FRONTEND, 0.10, 1.0),
    ]
    allocation = allocate(bids)
    assert allocation.contested is True
    assert Specialist.SECURITY in allocation.contested_specialists
    assert Specialist.PERFORMANCE in allocation.contested_specialists
    assert Specialist.DATABASE not in allocation.contested_specialists


def test_not_contested_outside_threshold(monkeypatch):
    monkeypatch.setenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.05")
    bids = [
        make_bid(Specialist.SECURITY, 0.60, 1.0),
        make_bid(Specialist.PERFORMANCE, 0.45, 1.0),
        make_bid(Specialist.DATABASE, 0.10, 1.0),
        make_bid(Specialist.NETWORKING, 0.10, 1.0),
        make_bid(Specialist.FRONTEND, 0.10, 1.0),
    ]
    allocation = allocate(bids)
    assert allocation.contested is False


def test_higher_confidence_wins_even_with_higher_cost(monkeypatch):
    """Regression test for the inc-03 real-world failure: Database bid higher
    confidence (1.00) but also a higher cost (1.50) than Performance (0.95 /
    1.20). The old confidence/cost score formula let Performance's cheaper
    cost estimate win despite being less confident and factually wrong."""
    monkeypatch.setenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.10")
    bids = [
        make_bid(Specialist.DATABASE, 1.00, 1.50),
        make_bid(Specialist.PERFORMANCE, 0.95, 1.20),
        make_bid(Specialist.SECURITY, 0.10, 0.50),
        make_bid(Specialist.NETWORKING, 0.10, 0.50),
        make_bid(Specialist.FRONTEND, 0.00, 0.50),
    ]
    allocation = allocate(bids)
    assert allocation.winner == Specialist.DATABASE


def test_low_confidence_rival_does_not_spuriously_contest(monkeypatch):
    """Regression test: inc-04 (an unambiguous frontend bug that normally wins
    outright at ~0.95 confidence) spuriously escalated on one real run because a
    low-confidence rival happened to land within the gap threshold of an
    unusually-low top bid. Neither side being genuinely confident should not
    count as a real dispute."""
    monkeypatch.setenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.10")
    monkeypatch.setenv("CONTESTED_CONFIDENCE_FLOOR", "0.4")
    bids = [
        make_bid(Specialist.FRONTEND, 0.35, 1.0),
        make_bid(Specialist.PERFORMANCE, 0.30, 1.0),
        make_bid(Specialist.SECURITY, 0.05, 1.0),
        make_bid(Specialist.DATABASE, 0.05, 1.0),
        make_bid(Specialist.NETWORKING, 0.10, 1.0),
    ]
    allocation = allocate(bids)
    assert allocation.contested is False
    assert allocation.winner == Specialist.FRONTEND


def test_two_genuinely_confident_rivals_still_contest(monkeypatch):
    """The floor must not swallow real disputes -- two specialists both actually
    sure of themselves should still trigger negotiation."""
    monkeypatch.setenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.10")
    monkeypatch.setenv("CONTESTED_CONFIDENCE_FLOOR", "0.4")
    bids = [
        make_bid(Specialist.DATABASE, 0.90, 1.0),
        make_bid(Specialist.NETWORKING, 0.85, 1.0),
        make_bid(Specialist.SECURITY, 0.10, 1.0),
        make_bid(Specialist.PERFORMANCE, 0.10, 1.0),
        make_bid(Specialist.FRONTEND, 0.05, 1.0),
    ]
    allocation = allocate(bids)
    assert allocation.contested is True
    assert Specialist.DATABASE in allocation.contested_specialists
    assert Specialist.NETWORKING in allocation.contested_specialists


def test_sanity_check_flags_a_wrong_lone_winner(monkeypatch):
    """Problem #3: an uncontested clear winner previously had no review at all.
    A wrong-domain diagnosis with nobody to challenge it should now be caught."""
    monkeypatch.delenv("QWEN_API_KEY", raising=False)  # force mock mode
    incident = get_incident("inc-02")  # ground truth: security

    wrong_check, _ = sanity_check(
        incident, Specialist.FRONTEND, "A frontend rendering bug", "Roll back the frontend deploy"
    )
    assert wrong_check.plausible is False

    right_check, _ = sanity_check(
        incident,
        Specialist.SECURITY,
        incident.ground_truth_root_cause,
        incident.reference_remediation,
    )
    assert right_check.plausible is True