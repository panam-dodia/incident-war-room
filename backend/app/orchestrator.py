"""Ties bidding, allocation, negotiation, and the baseline together into full
incident runs. Shared by the live WebSocket-streamed demo and the offline
batch evaluator -- `on_event` is a no-op for batch runs and a websocket
publisher for live ones."""

from __future__ import annotations

from typing import Callable, Optional

from app.baseline import run_baseline as _run_baseline_agent
from app.coordinator import allocate, collect_bids, sanity_check
from app.models import Incident, Resolution, RunResult, UsageStats
from app.negotiation import run_negotiation
from app.specialists import SPECIALIST_AGENTS

EventFn = Optional[Callable[[str, dict], None]]


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
        diag, usage = SPECIALIST_AGENTS[winner].diagnose(incident)
        usage_total = usage_total + usage
        winner_confidence = next(b.confidence for b in bids if b.specialist == winner)

        check, usage = sanity_check(incident, winner, diag["root_cause"], diag["remediation"])
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

    if on_event:
        on_event("resolution", resolution.model_dump())

    return RunResult(incident_id=incident.id, mode="multi_agent", allocation=allocation, resolution=resolution, usage=usage_total)


def run_baseline_run(incident: Incident, on_event: EventFn = None) -> RunResult:
    resolution, usage = _run_baseline_agent(incident)
    if on_event:
        on_event("baseline_resolution", resolution.model_dump())
    return RunResult(incident_id=incident.id, mode="baseline", allocation=None, resolution=resolution, usage=usage)
