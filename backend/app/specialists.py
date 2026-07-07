"""The five specialist agents: their personas, bidding, and negotiation behavior.

In mock mode (no QWEN_API_KEY), each specialist's bid confidence is derived
from keyword overlap between the incident text and that specialist's domain
vocabulary -- a deterministic stand-in for "how well does this incident match
my expertise" until real Qwen Cloud reasoning is wired in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.models import Bid, Claim, Incident, Rebuttal, Specialist, UsageStats
from app.qwen_client import qwen_client


@dataclass(frozen=True)
class SpecialistProfile:
    name: str
    persona: str
    keywords: tuple[str, ...]


PROFILES: dict[Specialist, SpecialistProfile] = {
    Specialist.SECURITY: SpecialistProfile(
        name="Security",
        persona=(
            "You are the Security specialist on an incident response team. You look for "
            "authentication/authorization failures, credential leaks, exploits, malicious "
            "traffic patterns, and data exposure."
        ),
        keywords=("unauthoriz", "credential", "token", "exploit", "inject", "attack",
                   "breach", "malicious", "anonymous", "public", "pii", "vulnerab",
                   "waf", "stuffing", "phishing"),
    ),
    Specialist.PERFORMANCE: SpecialistProfile(
        name="Performance",
        persona=(
            "You are the Performance specialist on an incident response team. You look for "
            "CPU/memory pressure, latency regressions, resource exhaustion, and inefficient code paths."
        ),
        keywords=("latency", "cpu", "memory", "slow", "throughput", "timeout",
                   "oom", "backlog", "queue", "p99"),
    ),
    Specialist.DATABASE: SpecialistProfile(
        name="Database",
        persona=(
            "You are the Database specialist on an incident response team. You look for "
            "query performance, locking/deadlocks, replication, and connection pool issues."
        ),
        keywords=("deadlock", "lock", "quer", "index", "connection pool", "replica",
                   "transaction", "tabl", "databas"),
    ),
    Specialist.NETWORKING: SpecialistProfile(
        name="Networking",
        persona=(
            "You are the Networking specialist on an incident response team. You look for "
            "connectivity issues, load balancer health, DNS/CDN problems, and cross-region links."
        ),
        keywords=("packet loss", "load balanc", "dns", "cdn", "vpc", "peering",
                   "network", "connection reset", "edge node", "rout", "firewall", "flapping"),
    ),
    Specialist.FRONTEND: SpecialistProfile(
        name="Frontend",
        persona=(
            "You are the Frontend specialist on an incident response team. You look for "
            "browser errors, client-side rendering bugs, and JS bundle regressions."
        ),
        keywords=("browser", "javascript", "bundle", "render", "safari", "chrome",
                   "dashboard", "typeerror", "client-side", "console"),
    ),
}


def keyword_hits(specialist: Specialist, text: str) -> list[str]:
    lowered = text.lower()
    return [kw for kw in PROFILES[specialist].keywords if kw in lowered]


def all_domain_hit_counts(text: str) -> dict[Specialist, int]:
    return {s: len(keyword_hits(s, text)) for s in Specialist}


def _stable_jitter(seed: str, spread: float) -> float:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    frac = int(digest[:8], 16) / 0xFFFFFFFF  # 0..1
    return (frac * 2 - 1) * spread


class SpecialistAgent:
    def __init__(self, specialist: Specialist):
        self.specialist = specialist
        self.profile = PROFILES[specialist]

    def bid(self, incident: Incident) -> tuple[Bid, UsageStats]:
        hits = keyword_hits(self.specialist, incident.description)
        confidence = 0.15 + 0.14 * len(hits) + _stable_jitter(f"{incident.id}:{self.specialist}:bid", 0.04)
        confidence = min(0.95, max(0.05, confidence))
        cost = 1.0 + _stable_jitter(f"{incident.id}:{self.specialist}:cost", 0.12)
        cost = round(max(0.6, cost), 2)

        def mock() -> dict:
            if hits:
                reasoning = (
                    f"Description mentions {', '.join(hits[:3])}, which points toward a "
                    f"{self.profile.name.lower()} issue."
                )
            else:
                reasoning = f"No strong {self.profile.name.lower()} signals, but flagging for completeness."
            return {"confidence": round(confidence, 3), "estimated_cost": cost, "reasoning": reasoning}

        system_prompt = (
            self.profile.persona
            + " Given an incident, return your confidence (0-1) that this incident is in your "
            "domain, an estimated_cost (relative effort units, roughly 0.5-2.0) to investigate "
            "it, and a one-sentence reasoning."
        )
        user_prompt = (
            f"Incident: {incident.title}\n\n{incident.description}\n\n"
            "Respond with JSON matching exactly this shape:\n"
            '{"confidence": <float 0-1>, "estimated_cost": <float>, "reasoning": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("bid", system_prompt, user_prompt, mock)
        bid = Bid(
            specialist=self.specialist,
            confidence=float(result["confidence"]),
            estimated_cost=float(result["estimated_cost"]),
            reasoning=str(result["reasoning"]),
        )
        return bid, usage

    def make_claim(self, incident: Incident, prior_confidence: float) -> tuple[Claim, UsageStats]:
        hits = keyword_hits(self.specialist, incident.description)

        def mock() -> dict:
            evidence = [f"Observed signal: '{h}'" for h in hits[:3]] or [
                "General domain fit based on the overall symptom description."
            ]
            return {
                "claim": f"This is fundamentally a {self.profile.name.lower()} issue.",
                "evidence": evidence,
                "confidence": round(prior_confidence, 3),
            }

        system_prompt = (
            self.profile.persona
            + " State your claim about the root cause with 2-3 concrete pieces of evidence "
            "quoted or paraphrased from the incident text."
        )
        user_prompt = (
            f"Incident: {incident.title}\n\n{incident.description}\n\n"
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

        def mock() -> dict:
            return {
                "rebuttal": (
                    f"Your evidence describes a downstream symptom, not the root cause -- the "
                    f"{self.profile.name.lower()} signals in this incident better explain why it started."
                )
            }

        system_prompt = (
            self.profile.persona
            + " Rebut the other specialist's most recent statement: explain why their evidence "
            "is a symptom rather than the root cause. Stay grounded in the concrete evidence in "
            "the incident text -- do not invent a deeper hypothetical root cause the text doesn't support."
        )
        user_prompt = (
            f"Incident: {incident.title}\n\n{incident.description}\n\n"
            f"Other specialist ({target_specialist.value}) says: {target_statement}\n\n"
            'Respond with JSON matching exactly this shape: {"rebuttal": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("negotiate", system_prompt, user_prompt, mock)
        rebuttal = Rebuttal(specialist=self.specialist, target=target_specialist, rebuttal=str(result["rebuttal"]))
        return rebuttal, usage

    def diagnose(self, incident: Incident) -> tuple[dict, UsageStats]:
        hits = keyword_hits(self.specialist, incident.description)

        def mock() -> dict:
            if self.specialist == incident.ground_truth_specialist:
                root_cause = incident.ground_truth_root_cause
                remediation = incident.reference_remediation
            else:
                root_cause = (
                    f"Likely a {self.profile.name.lower()}-side issue given signals: "
                    f"{', '.join(hits) if hits else 'general symptom pattern'}."
                )
                remediation = (
                    f"Investigate and remediate from the {self.profile.name.lower()} domain "
                    "(mitigate symptoms, monitor for recurrence)."
                )
            return {"root_cause": root_cause, "remediation": remediation}

        system_prompt = (
            self.profile.persona
            + " Give the final root cause and a concrete remediation for this incident. "
            "root_cause and remediation must each be a single string (a short paragraph), not a list."
        )
        user_prompt = (
            f"Incident: {incident.title}\n\n{incident.description}\n\n"
            "Respond with JSON matching exactly this shape:\n"
            '{"root_cause": "<single string>", "remediation": "<single string>"}'
        )
        result, usage = qwen_client.complete_json("negotiate", system_prompt, user_prompt, mock)
        return result, usage


SPECIALIST_AGENTS: dict[Specialist, SpecialistAgent] = {s: SpecialistAgent(s) for s in Specialist}
