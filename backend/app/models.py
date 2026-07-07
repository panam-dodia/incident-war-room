"""Shared pydantic schemas for the incident war room."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _coerce_text(v):
    """Real LLM output doesn't always match the requested shape exactly -- e.g. a
    model asked for a one-sentence remediation may return a list of steps instead
    of a string. Join lists into a single string rather than failing validation."""
    if isinstance(v, list):
        return " ".join(str(item) for item in v)
    return v


class Specialist(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    DATABASE = "database"
    NETWORKING = "networking"
    FRONTEND = "frontend"


class Incident(BaseModel):
    id: str
    title: str
    description: str
    ground_truth_specialist: Specialist
    ground_truth_root_cause: str
    reference_remediation: str
    cross_cutting: bool = False


class Bid(BaseModel):
    specialist: Specialist
    confidence: float = Field(ge=0, le=1)
    estimated_cost: float = Field(gt=0)
    reasoning: str

    _coerce_reasoning = field_validator("reasoning", mode="before")(_coerce_text)

    @property
    def score(self) -> float:
        return self.confidence / self.estimated_cost


class Allocation(BaseModel):
    bids: list[Bid]
    winner: Specialist
    contested: bool
    contested_specialists: list[Specialist] = Field(default_factory=list)


class Claim(BaseModel):
    specialist: Specialist
    claim: str
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)

    _coerce_claim = field_validator("claim", mode="before")(_coerce_text)


class Rebuttal(BaseModel):
    specialist: Specialist
    target: Specialist
    rebuttal: str

    _coerce_rebuttal = field_validator("rebuttal", mode="before")(_coerce_text)


class JudgeVerdict(BaseModel):
    """A neutral, disinterested decision after the debate -- not a vote by the
    contesting specialists themselves. winner=None means the judge found the
    evidence genuinely too balanced to call, which is the only path to escalation."""

    winner: Optional[Specialist] = None
    confidence: float = Field(ge=0, le=1)
    reasoning: str

    _coerce_reasoning = field_validator("reasoning", mode="before")(_coerce_text)


class SanityCheck(BaseModel):
    """A lightweight, independent second look at an uncontested clear-winner
    diagnosis -- the only specialist involved had no debate or rival to
    challenge it, so this is the one check standing between a confidently
    wrong lone answer and it becoming the final resolution unreviewed."""

    plausible: bool = True
    reasoning: str = ""

    _coerce_reasoning = field_validator("reasoning", mode="before")(_coerce_text)


class NegotiationRound(BaseModel):
    round_number: int
    claims: list[Claim] = Field(default_factory=list)
    rebuttals: list[Rebuttal] = Field(default_factory=list)


class Resolution(BaseModel):
    outcome: Literal["clear_winner", "consensus", "escalated"]
    winning_specialist: Optional[Specialist] = None
    root_cause: Optional[str] = None
    remediation: Optional[str] = None
    confidence: Optional[float] = None
    rounds: list[NegotiationRound] = Field(default_factory=list)
    escalation_reason: Optional[str] = None
    judge_reasoning: Optional[str] = None

    _coerce_root_cause = field_validator("root_cause", mode="before")(_coerce_text)
    _coerce_remediation = field_validator("remediation", mode="before")(_coerce_text)
    _coerce_judge_reasoning = field_validator("judge_reasoning", mode="before")(_coerce_text)


class UsageStats(BaseModel):
    tokens_used: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    calls_made: int = 0

    def __add__(self, other: "UsageStats") -> "UsageStats":
        return UsageStats(
            tokens_used=self.tokens_used + other.tokens_used,
            latency_ms=self.latency_ms + other.latency_ms,
            estimated_cost_usd=self.estimated_cost_usd + other.estimated_cost_usd,
            calls_made=self.calls_made + other.calls_made,
        )


class RunResult(BaseModel):
    incident_id: str
    mode: Literal["multi_agent", "baseline"]
    allocation: Optional[Allocation] = None
    resolution: Resolution
    usage: UsageStats


class EvalResult(BaseModel):
    incident_id: str
    mode: Literal["multi_agent", "baseline"]
    root_cause_correct: bool
    escalated: bool
    judge_score: float = Field(ge=0, le=5)
    usage: UsageStats


class EvalSummary(BaseModel):
    """Selective-prediction metrics, not just naive accuracy -- escalating is not the
    same outcome as being confidently wrong, so they're measured separately. See
    `evaluator.summarize()` for the exact definitions."""

    mode: Literal["multi_agent", "baseline"]
    incidents_scored: int
    accuracy: float
    coverage: float
    precision: float
    confidently_wrong_rate: float
    escalation_rate: float
    utility_score: float
    avg_judge_score: float
    total_tokens: int
    total_latency_ms: float
    total_cost_usd: float
    accuracy_per_1k_tokens: float


class WsEvent(BaseModel):
    """Envelope streamed to the dashboard over the WebSocket."""

    type: str
    run_id: str
    payload: dict
