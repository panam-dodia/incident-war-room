from app.validation_history import RUNS, historical_summary


def test_historical_summary_shape():
    summary = historical_summary()
    assert summary["num_runs"] == len(RUNS)
    for mode in ("multi_agent", "baseline"):
        for metric in ("accuracy", "mechanism_accuracy"):
            spread = summary["per_mode"][mode][metric]
            assert spread["min"] <= spread["mean"] <= spread["max"]
    assert summary["token_premium"]["min"] > 1.0
    assert summary["cost_premium"]["min"] > 1.0


def test_baseline_mechanism_accuracy_is_steady_across_runs():
    """Regression guard: the README/dashboard claim depends on baseline's
    mechanism accuracy being flat (0.75) across every recorded run -- if that
    ever changes, the recorded RUNS data is stale and needs refreshing."""
    summary = historical_summary()
    spread = summary["per_mode"]["baseline"]["mechanism_accuracy"]
    assert spread["min"] == spread["max"] == 0.75
