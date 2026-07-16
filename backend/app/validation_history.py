"""Snapshots of real Qwen Cloud full-batch eval runs, captured after the
tool-budget rebuild, kept here so the dashboard can show the accuracy/cost gap
was checked repeatedly -- not a single lucky run. These are frozen numbers
from actual runs (see README's "Real results" table), not live-computed --
`/api/eval/run` still runs a fresh live comparison against whatever incidents
and code are currently in the repo.
"""

from __future__ import annotations

RUNS: list[dict] = [
    {
        "label": "Run 1",
        "multi_agent": {"accuracy": 1.0, "mechanism_accuracy": 1.0, "total_tokens": 83748, "total_cost_usd": 0.10316},
        "baseline": {"accuracy": 0.875, "mechanism_accuracy": 0.75, "total_tokens": 22247, "total_cost_usd": 0.02225},
    },
    {
        "label": "Run 2",
        "multi_agent": {"accuracy": 0.875, "mechanism_accuracy": 0.875, "total_tokens": 93245, "total_cost_usd": 0.13922},
        "baseline": {"accuracy": 0.875, "mechanism_accuracy": 0.75, "total_tokens": 22825, "total_cost_usd": 0.02283},
    },
    {
        "label": "Run 3",
        "multi_agent": {"accuracy": 1.0, "mechanism_accuracy": 1.0, "total_tokens": 91219, "total_cost_usd": 0.12308},
        "baseline": {"accuracy": 0.875, "mechanism_accuracy": 0.75, "total_tokens": 22130, "total_cost_usd": 0.02213},
    },
]


def _spread(values: list[float]) -> dict:
    return {
        "min": round(min(values), 4),
        "mean": round(sum(values) / len(values), 4),
        "max": round(max(values), 4),
    }


def historical_summary() -> dict:
    """Aggregate min/mean/max accuracy and mechanism_accuracy across the recorded
    runs, per mode, plus the resulting cost/token premium range -- the numbers
    that back the README's "Real results" table, exposed to the dashboard too."""
    modes = ("multi_agent", "baseline")
    per_mode = {
        mode: {
            "accuracy": _spread([r[mode]["accuracy"] for r in RUNS]),
            "mechanism_accuracy": _spread([r[mode]["mechanism_accuracy"] for r in RUNS]),
        }
        for mode in modes
    }
    token_premium = _spread([r["multi_agent"]["total_tokens"] / r["baseline"]["total_tokens"] for r in RUNS])
    cost_premium = _spread([r["multi_agent"]["total_cost_usd"] / r["baseline"]["total_cost_usd"] for r in RUNS])
    return {
        "num_runs": len(RUNS),
        "runs": RUNS,
        "per_mode": per_mode,
        "token_premium": token_premium,
        "cost_premium": cost_premium,
    }
