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
from concurrent.futures import ThreadPoolExecutor

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


def contested_confidence_floor() -> float:
    """A rival must also clear this absolute confidence bar to count as a genuine
    contest -- being *numerically close* to the top bid isn't enough on its own.
    Without this, an easy case whose top bid has unusually low confidence on one
    sampling (pure noise, not a real signal) could let an equally low-confidence
    rival look like a real dispute, when neither side is actually sure of anything.
    Observed directly: inc-04 (an unambiguous frontend bug) spuriously escalated on
    one real run despite normally winning outright at ~0.95 confidence."""
    return float(os.getenv("CONTESTED_CONFIDENCE_FLOOR", "0.4"))


def collect_bids(incident: Incident) -> tuple[list[Bid], UsageStats]:
    """Every specialist independently bids: confidence plus an estimated cost. Run
    concurrently -- each specialist checks their own domain, so there's no reason
    to make one wait on another; that's the whole point of dividing the work."""
    usage_total = UsageStats()
    with ThreadPoolExecutor(max_workers=len(SPECIALIST_AGENTS)) as executor:
        results = list(executor.map(lambda agent: agent.bid(incident), SPECIALIST_AGENTS.values()))
    bids = [bid for bid, _ in results]
    for _, usage in results:
        usage_total = usage_total + usage
    return bids, usage_total


def allocate(bids: list[Bid]) -> Allocation:
    ranked = sorted(bids, key=lambda b: b.confidence, reverse=True)
    top = ranked[0]
    threshold = conflict_threshold()
    floor = contested_confidence_floor()

    contested_specialists = [top.specialist]
    for bid in ranked[1:]:
        if top.confidence - bid.confidence <= threshold and bid.confidence >= floor:
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

    tool = incident.tools.get(specialist)
    evidence_text = tool.result if tool else "No specific monitoring data was available in this domain."
    user_prompt = (
        f"Incident alert: {incident.alert}\n\n"
        f"Specialist: {specialist.value}\n"
        f"Their domain's monitoring data: {evidence_text}\n\n"
        f"Proposed root cause: {root_cause}\n"
        f"Proposed remediation: {remediation}\n\n"
        "Respond with JSON matching exactly this shape:\n"
        '{"plausible": true or false, "reasoning": "<single string>"}'
    )
    result, usage = qwen_client.complete_json("bid", SANITY_CHECK_PERSONA, user_prompt, mock)
    check = SanityCheck(plausible=bool(result.get("plausible", True)), reasoning=str(result.get("reasoning", "")))
    return check, usage
