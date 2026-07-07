"""Scores multi-agent vs baseline runs on root-cause accuracy, remediation
quality (LLM-judge rubric), and cost/latency -- the measurable efficiency
comparison the hackathon track asks for."""

from __future__ import annotations

from app.incidents import INCIDENTS
from app.models import EvalResult, EvalSummary, Incident, Resolution, RunResult
from app.orchestrator import run_baseline_run, run_multi_agent


def _word_overlap(produced: str, reference: str) -> float:
    strip = lambda w: w.strip(".,;:()")
    produced_words = {strip(w) for w in produced.lower().split()}
    reference_words = {strip(w) for w in reference.lower().split()}
    if not reference_words:
        return 0.0
    return min(1.0, len(produced_words & reference_words) / len(reference_words))


def judge_score(incident: Incident, resolution: Resolution) -> float:
    """LLM-judge rubric stand-in (mock mode): remediation word-overlap with the
    reference remediation, scaled to a 1-5 rubric score. Swapped for a real
    Qwen `judge` tier call once QWEN_API_KEY is set (see qwen_client.py)."""
    if resolution.outcome == "escalated" or not resolution.remediation:
        return 1.0
    overlap = _word_overlap(resolution.remediation, incident.reference_remediation)
    return round(min(5.0, 1.0 + overlap * 4.0), 2)


def score_run(incident: Incident, run_result: RunResult) -> EvalResult:
    escalated = run_result.resolution.outcome == "escalated"
    correct = (not escalated) and run_result.resolution.winning_specialist == incident.ground_truth_specialist
    return EvalResult(
        incident_id=incident.id,
        mode=run_result.mode,
        root_cause_correct=correct,
        escalated=escalated,
        judge_score=judge_score(incident, run_result.resolution),
        usage=run_result.usage,
    )


def summarize(results: list[EvalResult], mode: str) -> EvalSummary:
    """Selective-prediction metrics (see Chow's reject-option classifier / selective
    classification literature): escalating is treated as a distinct outcome from being
    confidently wrong, not folded into a single "accuracy" number that would otherwise
    make honest uncertainty look identical to a bad guess.

    - coverage: fraction of incidents the system committed to an answer on at all.
      Baseline has no abstain option, so its coverage is always 1.0.
    - precision: of the incidents it committed to, how many were correct
      (a.k.a. "selective accuracy" -- quality conditional on being confident).
    - confidently_wrong_rate: committed to an answer AND got it wrong -- the outcome
      that actually carries real-world risk (someone could act on a wrong diagnosis).
    - utility_score: mean of +1 (correct) / 0 (escalated) / -1 (confidently wrong) per
      incident -- a single number that doesn't reward guessing and doesn't punish
      honest uncertainty as if it were a mistake. Symmetric weighting by design: this
      is a methodology choice, not tuned to favor either system.
    - accuracy: kept for continuity/comparison with earlier runs, but should not be
      read alone -- it scores escalation and confidently-wrong identically.
    """
    subset = [r for r in results if r.mode == mode]
    n = len(subset) or 1

    correct_count = sum(1 for r in subset if r.root_cause_correct)
    escalated_count = sum(1 for r in subset if r.escalated)
    auto_resolved = [r for r in subset if not r.escalated]
    confidently_wrong_count = sum(1 for r in auto_resolved if not r.root_cause_correct)

    accuracy = correct_count / n
    coverage = len(auto_resolved) / n
    precision = (correct_count / len(auto_resolved)) if auto_resolved else 0.0
    confidently_wrong_rate = confidently_wrong_count / n
    escalation_rate = escalated_count / n
    utility_score = sum(1.0 if r.root_cause_correct else (0.0 if r.escalated else -1.0) for r in subset) / n

    avg_judge = sum(r.judge_score for r in subset) / n
    total_tokens = sum(r.usage.tokens_used for r in subset)
    total_latency = sum(r.usage.latency_ms for r in subset)
    total_cost = sum(r.usage.estimated_cost_usd for r in subset)
    accuracy_per_1k = (accuracy * n) / (total_tokens / 1000) if total_tokens else 0.0

    return EvalSummary(
        mode=mode,
        incidents_scored=len(subset),
        accuracy=round(accuracy, 3),
        coverage=round(coverage, 3),
        precision=round(precision, 3),
        confidently_wrong_rate=round(confidently_wrong_rate, 3),
        escalation_rate=round(escalation_rate, 3),
        utility_score=round(utility_score, 3),
        avg_judge_score=round(avg_judge, 3),
        total_tokens=total_tokens,
        total_latency_ms=round(total_latency, 1),
        total_cost_usd=round(total_cost, 5),
        accuracy_per_1k_tokens=round(accuracy_per_1k, 4),
    )


def run_full_eval(incidents: list[Incident] | None = None) -> dict:
    incidents = incidents if incidents is not None else INCIDENTS
    results: list[EvalResult] = []
    per_incident: list[dict] = []

    for incident in incidents:
        ma_run = run_multi_agent(incident)
        bl_run = run_baseline_run(incident)
        ma_eval = score_run(incident, ma_run)
        bl_eval = score_run(incident, bl_run)
        results.extend([ma_eval, bl_eval])
        per_incident.append(
            {
                "incident_id": incident.id,
                "title": incident.title,
                "cross_cutting": incident.cross_cutting,
                "ground_truth_specialist": incident.ground_truth_specialist.value,
                "multi_agent": {
                    "correct": ma_eval.root_cause_correct,
                    "judge_score": ma_eval.judge_score,
                    "outcome": ma_run.resolution.outcome,
                    "tokens": ma_run.usage.tokens_used,
                },
                "baseline": {
                    "correct": bl_eval.root_cause_correct,
                    "judge_score": bl_eval.judge_score,
                    "outcome": bl_run.resolution.outcome,
                    "tokens": bl_run.usage.tokens_used,
                },
            }
        )

    summary = {
        "multi_agent": summarize(results, "multi_agent").model_dump(),
        "baseline": summarize(results, "baseline").model_dump(),
    }
    return {"per_incident": per_incident, "summary": summary}
