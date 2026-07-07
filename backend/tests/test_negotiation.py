from app.incidents import get_incident
from app.models import Bid, Specialist
from app.negotiation import run_negotiation


def test_negotiation_converges_on_ground_truth_specialist(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)  # force mock mode
    monkeypatch.setenv("NEGOTIATION_MAX_ROUNDS", "3")

    incident = get_incident("inc-06")  # ground truth: security, distractor: performance
    contested_bids = [
        Bid(specialist=Specialist.SECURITY, confidence=0.6, estimated_cost=1.0, reasoning="test"),
        Bid(specialist=Specialist.PERFORMANCE, confidence=0.6, estimated_cost=1.0, reasoning="test"),
    ]

    resolution, usage = run_negotiation(incident, contested_bids)

    assert resolution.outcome == "consensus"
    assert resolution.winning_specialist == incident.ground_truth_specialist
    assert len(resolution.rounds) >= 1
    assert usage.calls_made > 0


def test_negotiation_escalates_when_judge_cannot_decide(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("NEGOTIATION_MAX_ROUNDS", "2")

    incident = get_incident("inc-06")  # ground truth is security -- not a candidate here
    contested_bids = [
        Bid(specialist=Specialist.PERFORMANCE, confidence=0.6, estimated_cost=1.0, reasoning="test"),
        Bid(specialist=Specialist.DATABASE, confidence=0.6, estimated_cost=1.0, reasoning="test"),
    ]

    resolution, _ = run_negotiation(incident, contested_bids)

    # ground truth isn't among the candidates, and their claim confidences tie exactly,
    # so the (mock) judge should say the evidence is too balanced to call
    assert resolution.outcome == "escalated"
    assert resolution.escalation_reason is not None


def test_negotiation_rounds_have_no_self_refereed_stances(monkeypatch):
    """Regression test: specialists no longer vote on their own outcome (that let two
    specialists concede to each other into a shared wrong answer in real testing).
    Round 1 is claims only; rounds 2+ are rebuttal-only."""
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("NEGOTIATION_MAX_ROUNDS", "3")

    incident = get_incident("inc-06")
    contested_bids = [
        Bid(specialist=Specialist.SECURITY, confidence=0.6, estimated_cost=1.0, reasoning="test"),
        Bid(specialist=Specialist.PERFORMANCE, confidence=0.6, estimated_cost=1.0, reasoning="test"),
    ]

    resolution, _ = run_negotiation(incident, contested_bids)

    assert resolution.rounds[0].round_number == 1
    assert len(resolution.rounds[0].claims) == 2
    for round_ in resolution.rounds[1:]:
        assert len(round_.claims) == 0
        assert len(round_.rebuttals) == 2
    assert resolution.judge_reasoning
