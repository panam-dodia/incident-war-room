import app.orchestrator as orchestrator
from app.incidents import get_incident
from app.models import Allocation, Bid, Resolution, Specialist, UsageStats


def test_consensus_downgraded_to_escalated_when_sanity_check_fails(monkeypatch):
    """Problem: only the uncontested clear-winner path had an independent sanity
    check -- a negotiated consensus that reached the wrong domain entirely had no
    safety net. Force a negotiation outcome that "wins" for the wrong specialist
    and confirm the orchestrator now catches it and downgrades to escalation."""
    monkeypatch.delenv("QWEN_API_KEY", raising=False)  # force mock mode
    incident = get_incident("inc-02")  # ground truth: security

    fake_bids = [Bid(specialist=s, confidence=0.5, estimated_cost=1.0, reasoning="test") for s in Specialist]
    fake_allocation = Allocation(
        bids=fake_bids,
        winner=Specialist.FRONTEND,
        contested=True,
        contested_specialists=[Specialist.FRONTEND, Specialist.SECURITY],
    )
    monkeypatch.setattr(orchestrator, "allocate", lambda bids: fake_allocation)

    wrong_resolution = Resolution(
        outcome="consensus",
        winning_specialist=Specialist.FRONTEND,
        root_cause="A frontend rendering bug",
        remediation="Roll back the frontend deploy",
        confidence=0.7,
        judge_reasoning="test",
    )
    monkeypatch.setattr(
        orchestrator, "run_negotiation", lambda incident, contested_bids: (wrong_resolution, UsageStats())
    )

    result = orchestrator.run_multi_agent(incident)

    assert result.resolution.outcome == "escalated"
    assert "sanity check" in result.resolution.escalation_reason.lower()


def test_consensus_kept_when_sanity_check_passes(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    incident = get_incident("inc-02")  # ground truth: security

    fake_bids = [Bid(specialist=s, confidence=0.5, estimated_cost=1.0, reasoning="test") for s in Specialist]
    fake_allocation = Allocation(
        bids=fake_bids,
        winner=Specialist.SECURITY,
        contested=True,
        contested_specialists=[Specialist.SECURITY, Specialist.PERFORMANCE],
    )
    monkeypatch.setattr(orchestrator, "allocate", lambda bids: fake_allocation)

    right_resolution = Resolution(
        outcome="consensus",
        winning_specialist=Specialist.SECURITY,
        root_cause=incident.ground_truth_root_cause,
        remediation=incident.reference_remediation,
        confidence=0.9,
        judge_reasoning="test",
    )
    monkeypatch.setattr(
        orchestrator, "run_negotiation", lambda incident, contested_bids: (right_resolution, UsageStats())
    )

    result = orchestrator.run_multi_agent(incident)

    assert result.resolution.outcome == "consensus"
    assert result.resolution.winning_specialist == Specialist.SECURITY


def test_specialist_with_no_tool_skips_llm_call(monkeypatch):
    """A specialist with no monitoring channel for this incident has nothing to
    reason about -- bid() should return a fixed low-confidence bid without
    spending an LLM call, even in real (non-mock) accounting terms."""
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    from app.specialists import SPECIALIST_AGENTS

    incident = get_incident("inc-04")  # no Database or Security tool defined
    bid, usage = SPECIALIST_AGENTS[Specialist.DATABASE].bid(incident)

    assert bid.confidence == 0.05
    assert bid.tool_checked is None
    assert usage.calls_made == 0
