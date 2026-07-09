"""The five specialist agents: their personas, bidding, and negotiation behavior.

Each specialist reasons from their *own* domain's tool result (if the incident has
one for them) -- not a shared block of text every specialist reads identically.
This is the actual reason specialization can add value here: a real security
engineer has SIEM access a generalist doesn't routinely check, not just a
different-sounding job title over the same information. In mock mode (no
QWEN_API_KEY), confidence is a deterministic stand-in keyed to the incident's
ground truth, purely for demoability -- real mode draws on no such shortcut.
"""

from __future__ import annotations

import hashlib

from app.models import Bid, Claim, Incident, Rebuttal, Specialist, UsageStats
from app.qwen_client import qwen_client

PERSONAS: dict[Specialist, str] = {
    Specialist.SECURITY: (
        "You are the Security specialist on an incident response team. You look for "
        "authentication/authorization failures, credential leaks, exploits, malicious "
        "traffic patterns, and data exposure."
    ),
    Specialist.PERFORMANCE: (
        "You are the Performance specialist on an incident response team. You look for "
        "CPU/memory pressure, latency regressions, resource exhaustion, and inefficient code paths."
    ),
    Specialist.DATABASE: (
        "You are the Database specialist on an incident response team. You look for "
        "query performance, locking/deadlocks, replication, and connection pool issues."
    ),
    Specialist.NETWORKING: (
        "You are the Networking specialist on an incident response team. You look for "
        "connectivity issues, load balancer health, DNS/CDN problems, and cross-region links."
    ),
    Specialist.FRONTEND: (
        "You are the Frontend specialist on an incident response team. You look for "
        "browser errors, client-side rendering bugs, and JS bundle regressions."
    ),
}

_CLEAN_MARKERS = ("nominal", "normal", "no anomal", "no deploy", "healthy", "no error", "clean")


def _looks_clean(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CLEAN_MARKERS)


def _stable_jitter(seed: str, spread: float) -> float:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    frac = int(digest[:8], 16) / 0xFFFFFFFF  # 0..1
    return (frac * 2 - 1) * spread


class SpecialistAgent:
    def __init__(self, specialist: Specialist):
        self.specialist = specialist
        self.persona = PERSONAS[specialist]

    def _own_tool_result(self, incident: Incident) -> str | None:
        tool = incident.tools.get(self.specialist)
        return tool.result if tool else None

    def bid(self, incident: Incident) -> tuple[Bid, UsageStats]:
        tool_result = self._own_tool_result(incident)
        cost = round(max(0.6, 1.0 + _stable_jitter(f"{incident.id}:{self.specialist}:cost", 0.12)), 2)

        if tool_result is None:
            # No monitoring channel exists for this domain on this incident -- there's
            # nothing to reason about, so skip the call entirely (real cost saving,
            # and realistic: you can't investigate a system that isn't implicated).
            bid = Bid(
                specialist=self.specialist,
                confidence=0.05,
                estimated_cost=cost,
                reasoning="No monitoring channel in my domain is relevant to this incident.",
            )
            return bid, UsageStats()

        def mock() -> dict:
            if self.specialist == incident.ground_truth_specialist:
                confidence = 0.9 + _stable_jitter(f"{incident.id}:{self.specialist}:bid", 0.05)
            elif _looks_clean(tool_result):
                confidence = 0.12 + _stable_jitter(f"{incident.id}:{self.specialist}:bid", 0.05)
            else:
                confidence = 0.35 + _stable_jitter(f"{incident.id}:{self.specialist}:bid", 0.1)
            confidence = min(0.97, max(0.03, confidence))
            return {
                "confidence": round(confidence, 3),
                "estimated_cost": cost,
                "reasoning": f"Checked my domain's monitoring: {tool_result}",
            }

        system_prompt = (
            self.persona
            + " You've already checked your own domain's monitoring as part of your normal "
            "workflow (shown below). Return your confidence (0-1) that this incident's root "
            "cause is in your domain, an estimated_cost (relative effort units, 0.5-2.0) to "
            "investigate further, and a one-sentence reasoning referencing what you found."
        )
        user_prompt = (
            f"Incident alert: {incident.alert}\n\nYour domain's monitoring data: {tool_result}\n\n"
            "Respond with JSON matching exactly this shape:\n"
            '{"confidence": <float 0-1>, "estimated_cost": <float>, "reasoning": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("bid", system_prompt, user_prompt, mock)
        bid = Bid(
            specialist=self.specialist,
            confidence=float(result["confidence"]),
            estimated_cost=float(result["estimated_cost"]),
            reasoning=str(result["reasoning"]),
            tool_checked=self.specialist.value,
        )
        return bid, usage

    def make_claim(self, incident: Incident, prior_confidence: float) -> tuple[Claim, UsageStats]:
        tool_result = self._own_tool_result(incident) or "No specific monitoring data available in my domain."

        def mock() -> dict:
            return {
                "claim": f"This is fundamentally a {self.specialist.value} issue.",
                "evidence": [tool_result],
                "confidence": round(prior_confidence, 3),
            }

        system_prompt = (
            self.persona
            + " State your claim about the root cause with 2-3 concrete pieces of evidence "
            "drawn from your domain's monitoring data below."
        )
        user_prompt = (
            f"Incident alert: {incident.alert}\n\nYour domain's monitoring data: {tool_result}\n\n"
            "Respond with JSON matching exactly this shape:\n"
            '{"claim": "<single string>", "evidence": ["<string>", "..."], "confidence": <float 0-1>}'
        )
        result, usage = qwen_client.complete_json("negotiate", system_prompt, user_prompt, mock)
        claim = Claim(
            specialist=self.specialist,
            claim=str(result["claim"]),
            evidence=list(result["evidence"]),
            confidence=float(result["confidence"]),
        )
        return claim, usage

    def make_rebuttal(
        self, incident: Incident, target_specialist: Specialist, target_statement: str
    ) -> tuple[Rebuttal, UsageStats]:
        """Rebut whatever the rival specialist most recently said -- their opening
        claim in round 2, or their own prior rebuttal in later rounds, so a
        multi-round debate is a real back-and-forth instead of a repeated reply
        to the same opening statement."""
        tool_result = self._own_tool_result(incident) or "No specific monitoring data available in my domain."

        def mock() -> dict:
            return {
                "rebuttal": (
                    f"Your evidence describes a downstream symptom, not the root cause -- my "
                    f"domain's data ({tool_result}) better explains why this started."
                )
            }

        system_prompt = (
            self.persona
            + " Rebut the other specialist's most recent statement using your own domain's "
            "monitoring data. Stay grounded in what you actually observed -- do not invent a "
            "deeper hypothetical root cause your data doesn't support."
        )
        user_prompt = (
            f"Incident alert: {incident.alert}\n\nYour domain's monitoring data: {tool_result}\n\n"
            f"Other specialist ({target_specialist.value}) says: {target_statement}\n\n"
            'Respond with JSON matching exactly this shape: {"rebuttal": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("negotiate", system_prompt, user_prompt, mock)
        rebuttal = Rebuttal(specialist=self.specialist, target=target_specialist, rebuttal=str(result["rebuttal"]))
        return rebuttal, usage

    def diagnose(
        self, incident: Incident, other_perspectives: list[Claim] | None = None
    ) -> tuple[dict, UsageStats]:
        """Produce the final root cause + remediation. When this specialist won a
        contested negotiation, other_perspectives carries the losing specialists'
        claims -- without this, a winning specialist's final answer only ever saw
        its own domain's data and had no way to incorporate a genuinely separate,
        valid concern a rival raised during the debate, even though the debate just
        happened. This is what lets the deliberation actually inform the output,
        instead of being discarded once a winner is picked."""
        tool_result = self._own_tool_result(incident) or "No specific monitoring data available in my domain."

        def mock() -> dict:
            if self.specialist == incident.ground_truth_specialist:
                root_cause = incident.ground_truth_root_cause
                remediation = incident.reference_remediation
            else:
                root_cause = f"Likely a {self.specialist.value}-side issue given: {tool_result}"
                remediation = (
                    f"Investigate and remediate from the {self.specialist.value} domain "
                    "(mitigate symptoms, monitor for recurrence)."
                )
            return {"root_cause": root_cause, "remediation": remediation}

        system_prompt = (
            self.persona
            + " Give the final root cause and a concrete remediation for this incident. "
            "root_cause and remediation must each be a single string (a short paragraph), not a list."
        )
        other_text = ""
        if other_perspectives:
            system_prompt += (
                " Other specialists raised separate perspectives during a debate about this "
                "incident (listed below), each grounded in their own domain's monitoring data. "
                "If any of them identify a genuinely distinct, valid concern that your own "
                "remediation doesn't already cover, fold addressing it in too. Do not repeat a "
                "point you've already made, and do not include a claim that was actually wrong "
                "-- only incorporate concerns that add real, separate value."
            )
            other_text = "\n\nOther specialists' perspectives from the debate:\n" + "\n".join(
                f"- [{c.specialist.value}] {c.claim} (evidence: {'; '.join(c.evidence)})"
                for c in other_perspectives
            )

        user_prompt = (
            f"Incident alert: {incident.alert}\n\nYour domain's monitoring data: {tool_result}{other_text}\n\n"
            "Respond with JSON matching exactly this shape:\n"
            '{"root_cause": "<single string>", "remediation": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("negotiate", system_prompt, user_prompt, mock)
        return result, usage


SPECIALIST_AGENTS: dict[Specialist, SpecialistAgent] = {s: SpecialistAgent(s) for s in Specialist}
