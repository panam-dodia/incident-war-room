"""Auction-style allocation: specialists bid, the coordinator picks the most
confident specialist -- or flags overlapping claims for negotiation when two or
more are close -- and either resolves.

Allocation ranks by *confidence*, not confidence/cost. A specialist's cost
estimate is its own guess at investigation effort, not a signal of correctness,
so it must not be able to override who is actually more sure. Cost stays on
the Bid for display/efficiency reporting (see Bid.score), it just doesn't
decide the winner or who's contested.
"""

from __future__ import annotations

import os

from app.models import Allocation, Bid, Incident, SanityCheck, Specialist, UsageStats
from app.qwen_client import qwen_client
from app.specialists import SPECIALIST_AGENTS

SANITY_CHECK_PERSONA = (
    "You are a skeptical incident-review checker. A single specialist proposed this root cause "
    "and remediation with no rival specialist close enough in confidence to trigger a debate -- "
    "so nobody has challenged it yet. Check whether the proposed root cause is genuinely "
    "supported by the concrete evidence in the incident description, or whether it's a mismatch, "
    "an unsupported guess, or plausibly the wrong domain entirely."
)


def conflict_threshold() -> float:
    """Confidence-point gap (not a percentage) within which a runner-up bid is
    considered a genuine rival to the top bid, triggering negotiation."""
    return float(os.getenv("CONFLICT_CONFIDENCE_THRESHOLD", "0.10"))


def collect_bids(incident: Incident) -> tuple[list[Bid], UsageStats]:
    """Every specialist independently bids: confidence plus an estimated cost."""
    bids: list[Bid] = []
    usage_total = UsageStats()
    for agent in SPECIALIST_AGENTS.values():
        bid, usage = agent.bid(incident)
        bids.append(bid)
        usage_total = usage_total + usage
    return bids, usage_total


def allocate(bids: list[Bid]) -> Allocation:
    ranked = sorted(bids, key=lambda b: b.confidence, reverse=True)
    top = ranked[0]
    threshold = conflict_threshold()

    contested_specialists = [top.specialist]
    for bid in ranked[1:]:
        if top.confidence - bid.confidence <= threshold:
            contested_specialists.append(bid.specialist)

    contested = len(contested_specialists) > 1
    return Allocation(
        bids=bids,
        winner=top.specialist,
        contested=contested,
        contested_specialists=contested_specialists if contested else [],
    )


def sanity_check(
    incident: Incident, specialist: Specialist, root_cause: str, remediation: str
) -> tuple[SanityCheck, UsageStats]:
    """One cheap, independent check on an uncontested clear-winner diagnosis. Uses the
    fast/cheap 'bid' model tier deliberately -- this must stay proportionate to a fast
    path that exists specifically because most incidents don't need a full debate."""

    def mock() -> dict:
        plausible = specialist == incident.ground_truth_specialist
        reasoning = (
            "Root cause and remediation are consistent with the incident's concrete evidence."
            if plausible
            else "The proposed root cause does not appear to match the incident's concrete evidence."
        )
        return {"plausible": plausible, "reasoning": reasoning}

    user_prompt = (
        f"Incident: {incident.title}\n\n{incident.description}\n\n"
        f"Specialist: {specialist.value}\n"
        f"Proposed root cause: {root_cause}\n"
        f"Proposed remediation: {remediation}\n\n"
        "Respond with JSON matching exactly this shape:\n"
        '{"plausible": true or false, "reasoning": "<single string>"}'
    )
    result, usage = qwen_client.complete_json("bid", SANITY_CHECK_PERSONA, user_prompt, mock)
    check = SanityCheck(plausible=bool(result.get("plausible", True)), reasoning=str(result.get("reasoning", "")))
    return check, usage
