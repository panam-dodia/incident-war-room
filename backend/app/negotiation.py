"""Structured multi-round negotiation protocol for overlapping claims.

Round 1: each contesting specialist states a claim + evidence.
Round 2..N: each specialist rebuts its current top rival's most recent
statement (a real back-and-forth, not a repeated reply to the same opening
claim).

Unlike an earlier version of this protocol, the contesting specialists do not
decide for themselves whether to concede -- that let two specialists debate
each other into a shared wrong answer, or both back down at once with no
handling for it. Instead, after the rounds, a neutral judge (uninvolved in the
debate, using a stronger model) reads the full transcript and decides. Escalation
now only happens when the judge itself says the evidence is genuinely unclear.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from app.models import Bid, Claim, Incident, JudgeVerdict, NegotiationRound, Rebuttal, Resolution, Specialist, UsageStats
from app.qwen_client import qwen_client
from app.specialists import SPECIALIST_AGENTS

JUDGE_PERSONA = (
    "You are a neutral incident-review judge. You were not part of the debate and have no "
    "loyalty to any specialist team. Read the full debate transcript below and decide which "
    "specialist's explanation is best supported by the concrete evidence in the incident "
    "description. Do not reward a more elaborate or 'deeper-sounding' theory over one that is "
    "actually better grounded in the specific evidence quoted. If the evidence genuinely does "
    "not clearly favor one side, say so honestly instead of guessing."
)


def max_rounds() -> int:
    return int(os.getenv("NEGOTIATION_MAX_ROUNDS", "3"))


def judge_votes() -> int:
    """How many independent judge samples to take and majority-vote across. A single
    sample (even at low temperature) was measured to flip-flop on genuinely close
    calls -- the model's own reasoning isn't stably anchored to one answer, so making
    that one sample more deterministic doesn't help. Self-consistency (sample several
    times, take the majority) is the standard fix for exactly this kind of instability."""
    return int(os.getenv("JUDGE_VOTES", "5"))


def _judge_negotiation(
    incident: Incident, claims: dict[Specialist, Claim], rebuttals: list[Rebuttal]
) -> tuple[JudgeVerdict, UsageStats]:
    """Runs judge_votes() independent judge samples and takes the majority verdict.
    A genuine split with no majority is treated as real evidence of ambiguity, not
    resolved by an arbitrary tiebreak."""
    n = judge_votes()
    usage_total = UsageStats()
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(_judge_once, incident, claims, rebuttals) for _ in range(n)]
        results = [f.result() for f in futures]
    verdicts = [v for v, _ in results]
    for _, usage in results:
        usage_total = usage_total + usage

    tally: dict[Specialist | None, int] = {}
    for v in verdicts:
        tally[v.winner] = tally.get(v.winner, 0) + 1
    winner, count = max(tally.items(), key=lambda kv: kv[1])

    if count > n / 2:
        representative = next(v for v in verdicts if v.winner == winner)
        final = JudgeVerdict(
            winner=winner,
            confidence=representative.confidence,
            reasoning=f"{representative.reasoning} ({count}/{n} independent judge samples agreed.)",
        )
    else:
        tally_str = ", ".join(f"{(w.value if w else 'unclear')}: {c}" for w, c in tally.items())
        final = JudgeVerdict(
            winner=None,
            confidence=0.4,
            reasoning=f"No majority among {n} independent judge samples ({tally_str}) -- evidence is genuinely contested.",
        )
    return final, usage_total


def _judge_once(
    incident: Incident, claims: dict[Specialist, Claim], rebuttals: list[Rebuttal]
) -> tuple[JudgeVerdict, UsageStats]:
    transcript_lines: list[str] = []
    for specialist, claim in claims.items():
        transcript_lines.append(f"[{specialist.value}] claim (confidence {claim.confidence:.2f}): {claim.claim}")
        for evidence in claim.evidence:
            transcript_lines.append(f"    evidence: {evidence}")
    for rebuttal in rebuttals:
        transcript_lines.append(f"[{rebuttal.specialist.value} -> {rebuttal.target.value}] rebuttal: {rebuttal.rebuttal}")
    transcript = "\n".join(transcript_lines)
    candidates = [s.value for s in claims]

    def mock() -> dict:
        # This text stands in both when running fully in mock mode (no API key) and as a
        # per-field fallback if the real model omits `reasoning` while still returning a
        # valid winner/confidence. It must not claim to be real analysis in either case --
        # a prior version wrote realistic-sounding justification here, which was misleading
        # when it silently patched a gap in an otherwise-real response.
        no_reasoning = "No detailed reasoning is available for this decision."
        ground_truth = incident.ground_truth_specialist
        if ground_truth in claims:
            return {"winner": ground_truth.value, "confidence": 0.85, "reasoning": no_reasoning}
        ranked = sorted(claims.values(), key=lambda c: c.confidence, reverse=True)
        if len(ranked) >= 2 and ranked[0].confidence - ranked[1].confidence < 0.05:
            return {"winner": None, "confidence": 0.4, "reasoning": no_reasoning}
        return {"winner": ranked[0].specialist.value, "confidence": ranked[0].confidence, "reasoning": no_reasoning}

    user_prompt = (
        f"Incident alert: {incident.alert}\n\n"
        f"Debate transcript:\n{transcript}\n\n"
        f"Candidates: {', '.join(candidates)}\n\n"
        "Respond with JSON matching exactly this shape:\n"
        '{"winner": "<one of the candidate names, or null if genuinely unclear>", '
        '"confidence": <float 0-1>, "reasoning": "<single non-empty string>"}\n\n'
        "The reasoning field is required and must not be empty or null -- explain which "
        "specific evidence from the transcript drove your decision."
    )
    result, usage = qwen_client.complete_json("judge", JUDGE_PERSONA, user_prompt, mock)

    winner: Specialist | None = None
    winner_raw = result.get("winner")
    if winner_raw:
        try:
            candidate = Specialist(str(winner_raw).strip().lower())
            if candidate in claims:
                winner = candidate
        except ValueError:
            winner = None

    verdict = JudgeVerdict(
        winner=winner,
        confidence=float(result.get("confidence", 0.5)),
        reasoning=str(result.get("reasoning", "")),
    )
    return verdict, usage


def run_negotiation(incident: Incident, contested_bids: list[Bid]) -> tuple[Resolution, UsageStats]:
    usage_total = UsageStats()
    contested_specialists = [b.specialist for b in contested_bids]
    agents = {s: SPECIALIST_AGENTS[s] for s in contested_specialists}
    initial_confidence = {b.specialist: b.confidence for b in contested_bids}

    claims: dict[Specialist, Claim] = {}
    last_statement: dict[Specialist, str] = {}
    rounds: list[NegotiationRound] = []

    round1 = NegotiationRound(round_number=1)
    specialists_order = list(agents.keys())
    with ThreadPoolExecutor(max_workers=len(specialists_order)) as executor:
        claim_results = list(
            executor.map(
                lambda s: agents[s].make_claim(incident, prior_confidence=initial_confidence[s]),
                specialists_order,
            )
        )
    for specialist, (claim, usage) in zip(specialists_order, claim_results):
        claims[specialist] = claim
        last_statement[specialist] = f"{claim.claim} Evidence: {'; '.join(claim.evidence)}"
        round1.claims.append(claim)
        usage_total = usage_total + usage
    rounds.append(round1)

    all_rebuttals: list[Rebuttal] = []
    specialists_list = list(agents.keys())
    round_number = 2
    while round_number <= max_rounds() and len(specialists_list) > 1:
        nround = NegotiationRound(round_number=round_number)
        new_statements: dict[Specialist, str] = {}
        targets = {
            specialist: max((s for s in specialists_list if s != specialist), key=lambda s: claims[s].confidence)
            for specialist in specialists_list
        }
        with ThreadPoolExecutor(max_workers=len(specialists_list)) as executor:
            rebuttal_results = list(
                executor.map(
                    lambda s: agents[s].make_rebuttal(incident, targets[s], last_statement[targets[s]]),
                    specialists_list,
                )
            )
        for specialist, (rebuttal, usage) in zip(specialists_list, rebuttal_results):
            nround.rebuttals.append(rebuttal)
            all_rebuttals.append(rebuttal)
            new_statements[specialist] = rebuttal.rebuttal
            usage_total = usage_total + usage
        last_statement.update(new_statements)
        rounds.append(nround)
        round_number += 1

    verdict, usage = _judge_negotiation(incident, claims, all_rebuttals)
    usage_total = usage_total + usage

    if verdict.winner is not None:
        other_perspectives = [claim for specialist, claim in claims.items() if specialist != verdict.winner]
        diag, usage = agents[verdict.winner].diagnose(incident, other_perspectives=other_perspectives)
        usage_total = usage_total + usage
        resolution = Resolution(
            outcome="consensus",
            winning_specialist=verdict.winner,
            root_cause=diag["root_cause"],
            remediation=diag["remediation"],
            confidence=verdict.confidence,
            rounds=rounds,
            judge_reasoning=verdict.reasoning,
        )
        return resolution, usage_total

    resolution = Resolution(
        outcome="escalated",
        rounds=rounds,
        escalation_reason=verdict.reasoning
        or f"Judge could not determine a winner among {', '.join(s.value for s in contested_specialists)}.",
        judge_reasoning=verdict.reasoning,
    )
    return resolution, usage_total
