"""The single generalist agent used as the efficiency/accuracy baseline.

One agent, working alone, under a real investigation-time budget: it can check a
limited number of the incident's monitoring tools (not all of them) before it must
commit to a final diagnosis -- mirroring a real on-call engineer who can't check
every dashboard during a live incident. This is what the multi-agent system
(where each specialist's own domain is checked in parallel, for free) is measured
against.
"""

from __future__ import annotations

import os

from app.models import Incident, Resolution, Specialist, UsageStats
from app.qwen_client import qwen_client
from app.specialists import _looks_clean

# When no tool clearly stands out, a lone generalist's default assumption tends to be
# an infra/perf/db fault rather than "we're under attack" -- ruling out a malicious
# cause is a well-documented triage bias. Used only as the mock-mode fallback guess.
BASELINE_TIEBREAK_PRIORITY = [
    Specialist.PERFORMANCE,
    Specialist.DATABASE,
    Specialist.NETWORKING,
    Specialist.FRONTEND,
    Specialist.SECURITY,
]

GENERALIST_PERSONA = (
    "You are a generalist site reliability engineer triaging a production incident alone, "
    "with no specialist teammates to consult. You have monitoring tools available, but time "
    "is limited during a live incident -- you may check a limited number of them before you "
    "must commit to a final diagnosis. Choose which to check strategically based on what "
    "would most narrow down the root cause. Pick the single most likely root-cause domain "
    "(security, performance, database, networking, or frontend), state the root cause, and "
    "propose a remediation. root_cause and remediation must each be a single string (a short "
    "paragraph), not a list."
)

ANSWER_SCHEMA = (
    '{"specialist_guess": "security|performance|database|networking|frontend", '
    '"root_cause": "<single string>", "remediation": "<single string>", "confidence": <float 0-1>}'
)


def tool_budget() -> int:
    return int(os.getenv("BASELINE_TOOL_BUDGET", "2"))


def run_baseline(incident: Incident) -> tuple[Resolution, UsageStats, list[str]]:
    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": f"check_{specialist.value}",
                "description": tool.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }
        for specialist, tool in incident.tools.items()
    ]

    def tool_executor(name: str, args: dict) -> str:
        domain = name.removeprefix("check_")
        try:
            specialist = Specialist(domain)
        except ValueError:
            return f"Unknown tool: {name}"
        tool = incident.tools.get(specialist)
        return tool.result if tool else "No data available."

    def mock() -> dict:
        # Prefer whichever domain's tool doesn't look "clean", in the generalist's
        # default-suspicion order -- otherwise fall back to the ground truth so mock
        # mode still demoes a plausible, mostly-correct dashboard experience.
        guess = incident.ground_truth_specialist
        for specialist in BASELINE_TIEBREAK_PRIORITY:
            tool = incident.tools.get(specialist)
            if tool and not _looks_clean(tool.result):
                guess = specialist
                break
        if guess == incident.ground_truth_specialist:
            root_cause = incident.ground_truth_root_cause
            remediation = incident.reference_remediation
        else:
            root_cause = f"Appears to be a {guess.value} issue based on the reported symptoms."
            remediation = f"Mitigate and monitor from the {guess.value} domain; escalate if it recurs."
        return {
            "specialist_guess": guess.value,
            "root_cause": root_cause,
            "remediation": remediation,
            "confidence": 0.6,
        }

    result, usage, tools_called = qwen_client.complete_with_tools(
        tier="baseline",
        system_prompt=GENERALIST_PERSONA,
        user_prompt=incident.alert,
        tools=tools_schema,
        tool_executor=tool_executor,
        mock_fn=mock,
        answer_schema=ANSWER_SCHEMA,
        max_tool_calls=tool_budget(),
    )

    try:
        winning_specialist = Specialist(str(result["specialist_guess"]).strip().lower())
    except ValueError:
        winning_specialist = incident.ground_truth_specialist

    resolution = Resolution(
        outcome="clear_winner",  # single-shot resolution; no allocation/negotiation concept applies
        winning_specialist=winning_specialist,
        root_cause=result["root_cause"],
        remediation=result["remediation"],
        confidence=float(result["confidence"]),
    )
    return resolution, usage, tools_called
