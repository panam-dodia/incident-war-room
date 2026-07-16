"""Ties bidding, allocation, negotiation, and the baseline together into full
incident runs. Shared by the live WebSocket-streamed demo and the offline
batch evaluator -- `on_event` is a no-op for batch runs and a websocket
publisher for live ones."""

from __future__ import annotations

from typing import Callable, Optional

from app.baseline import run_baseline as _run_baseline_agent
from app.coordinator import allocate, collect_bids, contested_confidence_floor, sanity_check
from app.models import Bid, Claim, Incident, Resolution, RunResult, Specialist, UsageStats
from app.negotiation import run_negotiation
from app.specialists import SPECIALIST_AGENTS

EventFn = Optional[Callable[[str, dict], None]]


def _secondary_perspectives(incident: Incident, bids: list[Bid], winner: Specialist) -> list[Claim]:
    """Other specialists' bids that cleared the contest floor but weren't close
    enough to the winner to trigger a debate -- a genuinely plausible, separate
    concern, not just noise. Without this, an uncontested clear winner's
    diagnose() call only ever saw its own domain's data and had no way to know a
    real, distinct concern showed up in someone else's monitoring, even though
    that specialist already checked and found it."""
    floor = contested_confidence_floor()
    perspectives = []
    for bid in bids:
        if bid.specialist == winner or bid.confidence < floor:
            continue
        tool = incident.tools.get(bid.specialist)
        evidence = tool.result if tool else "No specific monitoring data available in this domain."
        perspectives.append(
            Claim(specialist=bid.specialist, claim=bid.reasoning, evidence=[evidence], confidence=bid.confidence)
        )
    return perspectives


def run_multi_agent(incident: Incident, on_event: EventFn = None) -> RunResult:
    usage_total = UsageStats()

    bids, usage = collect_bids(incident)
    usage_total = usage_total + usage
    if on_event:
        on_event("bids", {"bids": [b.model_dump() for b in bids]})

    allocation = allocate(bids)
    if on_event:
        on_event("allocation", allocation.model_dump())

    if not allocation.contested:
        winner = allocation.winner
        other_perspectives = _secondary_perspectives(incident, bids, winner)
        diag, usage = SPECIALIST_AGENTS[winner].diagnose(incident, other_perspectives=other_perspectives)
        usage_total = usage_total + usage
        winner_confidence = next(b.confidence for b in bids if b.specialist == winner)

        check, usage = sanity_check(
            incident, winner, diag["root_cause"], diag["remediation"], other_perspectives=other_perspectives
        )
        usage_total = usage_total + usage
        if on_event:
            on_event("sanity_check", check.model_dump())

        if check.plausible:
            resolution = Resolution(
                outcome="clear_winner",
                winning_specialist=winner,
                root_cause=diag["root_cause"],
                remediation=diag["remediation"],
                confidence=winner_confidence,
            )
        else:
            resolution = Resolution(
                outcome="escalated",
                escalation_reason=(
                    f"The {winner.value} specialist's uncontested diagnosis failed an "
                    f"independent sanity check: {check.reasoning}"
                ),
                judge_reasoning=check.reasoning,
            )
    else:
        contested_bids = [b for b in bids if b.specialist in allocation.contested_specialists]
        resolution, usage = run_negotiation(incident, contested_bids)
        usage_total = usage_total + usage
        if on_event:
            for round_ in resolution.rounds:
                on_event("negotiation_round", round_.model_dump())

        if resolution.outcome == "consensus":
            other_claims = (
                [c for c in resolution.rounds[0].claims if c.specialist != resolution.winning_specialist]
                if resolution.rounds
                else []
            )
            check, usage = sanity_check(
                incident,
                resolution.winning_specialist,
                resolution.root_cause,
                resolution.remediation,
                other_perspectives=other_claims,
            )
            usage_total = usage_total + usage
            if on_event:
                on_event("sanity_check", check.model_dump())

            if not check.plausible:
                resolution = Resolution(
                    outcome="escalated",
                    rounds=resolution.rounds,
                    escalation_reason=(
                        f"The negotiated consensus (won by {resolution.winning_specialist.value}) failed an "
                        f"independent sanity check: {check.reasoning}"
                    ),
                    judge_reasoning=resolution.judge_reasoning,
                )

    if on_event:
        on_event("resolution", resolution.model_dump())

    tools_checked = [b.tool_checked for b in bids if b.tool_checked]
    return RunResult(
        incident_id=incident.id,
        mode="multi_agent",
        allocation=allocation,
        resolution=resolution,
        usage=usage_total,
        tools_checked=tools_checked,
    )


def run_baseline_run(incident: Incident, on_event: EventFn = None) -> RunResult:
    resolution, usage, tools_checked = _run_baseline_agent(incident)
    if on_event:
        on_event("baseline_resolution", {**resolution.model_dump(), "tools_checked": tools_checked})
    return RunResult(
        incident_id=incident.id,
        mode="baseline",
        allocation=None,
        resolution=resolution,
        usage=usage,
        tools_checked=tools_checked,
    )
