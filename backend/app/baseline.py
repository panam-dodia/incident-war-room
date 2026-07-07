"""The single generalist agent used as the efficiency/accuracy baseline.

No bidding, no negotiation -- one model call picks a domain, a root cause,
and a remediation. This is what the multi-agent system is measured against.
"""

from __future__ import annotations

from app.models import Incident, Resolution, Specialist, UsageStats
from app.qwen_client import qwen_client
from app.specialists import all_domain_hit_counts

# When keyword signal ties between domains, a lone generalist's default assumption
# tends to be an infra/perf/db fault rather than "we're under attack" -- ruling out
# a malicious cause is a well-documented triage bias, and it's exactly the kind of
# blind spot a dedicated Security specialist (and cross-examination in a debate) is
# there to catch. Ties break in this order; earlier = more likely generalist guess.
BASELINE_TIEBREAK_PRIORITY = [
    Specialist.PERFORMANCE,
    Specialist.DATABASE,
    Specialist.NETWORKING,
    Specialist.FRONTEND,
    Specialist.SECURITY,
]

GENERALIST_PERSONA = (
    "You are a generalist site reliability engineer triaging a production incident alone, "
    "with no specialist teammates to consult. Pick the single most likely root-cause domain "
    "(security, performance, database, networking, or frontend), state the root cause, and "
    "propose a remediation, all in one pass. root_cause and remediation must each be a single "
    "string (a short paragraph), not a list."
)


def run_baseline(incident: Incident) -> tuple[Resolution, UsageStats]:
    hit_counts = all_domain_hit_counts(incident.description)

    def mock() -> dict:
        guess = max(
            hit_counts,
            key=lambda s: (hit_counts[s], -BASELINE_TIEBREAK_PRIORITY.index(s)),
        )
        if guess == incident.ground_truth_specialist:
            root_cause = incident.ground_truth_root_cause
            remediation = incident.reference_remediation
        else:
            root_cause = f"Appears to be a {guess.value} issue based on the reported symptoms."
            remediation = f"Mitigate and monitor from the {guess.value} domain; escalate if it recurs."
        confidence = min(0.9, 0.3 + 0.12 * hit_counts[guess])
        return {
            "specialist_guess": guess.value,
            "root_cause": root_cause,
            "remediation": remediation,
            "confidence": round(confidence, 3),
        }

    user_prompt = (
        f"Incident: {incident.title}\n\n{incident.description}\n\n"
        "Respond with JSON matching exactly this shape:\n"
        '{"specialist_guess": "security|performance|database|networking|frontend", '
        '"root_cause": "<string>", "remediation": "<string>", "confidence": <float 0-1>}'
    )
    result, usage = qwen_client.complete_json("baseline", GENERALIST_PERSONA, user_prompt, mock)

    try:
        winning_specialist = Specialist(str(result["specialist_guess"]).strip().lower())
    except ValueError:
        winning_specialist = max(
            hit_counts,
            key=lambda s: (hit_counts[s], -BASELINE_TIEBREAK_PRIORITY.index(s)),
        )

    resolution = Resolution(
        outcome="clear_winner",  # single-shot resolution; no allocation/negotiation concept applies
        winning_specialist=winning_specialist,
        root_cause=result["root_cause"],
        remediation=result["remediation"],
        confidence=float(result["confidence"]),
    )
    return resolution, usage
