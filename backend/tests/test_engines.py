"""Engine tests on tiny hand-made datasets (no DB)."""
from datetime import date, timedelta

import numpy as np

from app.engines import pulse

START = date(2026, 10, 1)  # a neutral stretch: no festival window until Navratri on 10 Oct


def make_panel(series: np.ndarray, skus: list[str] | None = None) -> pulse.Panel:
    """Panel from an (S, T) array; one product per series, one shop."""
    S, T = series.shape
    skus = skus or [f"sku{i}" for i in range(S)]
    return pulse.Panel(
        shop_ids=np.ones(S, dtype=int), product_ids=np.arange(S), prod_idx=np.arange(S),
        size_mult=np.ones(S), type_code=np.zeros(S, dtype=int), skus=skus,
        dates=[START + timedelta(days=i) for i in range(T)], Q=series.astype(float))


def info_for(panel: pulse.Panel, extra: int = 0) -> pulse.DayInfo:
    dates = panel.dates + [panel.dates[-1] + timedelta(days=i + 1) for i in range(extra)]
    return pulse.DayInfo.build(dates)


def test_festival_label_window() -> None:
    assert pulse.festival_label(date(2026, 11, 8)) == "Diwali"
    assert pulse.festival_label(date(2026, 11, 6)) == "Diwali"
    assert pulse.festival_label(date(2026, 11, 5)) == ""
    assert pulse.festival_label(date(2026, 10, 20)) == "Dussehra"  # beats Navratri on overlap


def test_baseline_constant_series_predicts_constant() -> None:
    panel = make_panel(np.full((2, 30), 10.0))
    info = pulse.DayInfo.build(panel.dates + [panel.dates[-1] + timedelta(days=1)])
    F, W = pulse.Multipliers({}, {}).matrices(panel.skus, info)
    out = pulse.forecast_baseline(panel, F, W, 1)
    assert np.allclose(out, 10.0)


def test_baseline_same_weekday_weighting() -> None:
    """0.5*mean(last 4 same weekday) + 0.5*mean(last 14)."""
    T = 30
    q = np.ones((1, T)) * 4.0
    for k in range(1, 5):  # the 4 previous same-weekday columns of j=T
        q[0, T - 7 * k] = 20.0
    panel = make_panel(q)
    info = info_for(panel, 1)
    F, W = pulse.Multipliers({}, {}).matrices(panel.skus, info)
    expected = 0.5 * 20.0 + 0.5 * q[0, T - 14:T].mean()
    assert np.isclose(pulse.forecast_baseline(panel, F, W, 1)[0, 0], expected)


def test_baseline_applies_festival_multiplier_and_ignores_festival_inflation() -> None:
    """History inflated on festival days is normalised away; target day gets the multiplier."""
    # Window covering 6-8 Nov 2026 (Diwali): history ends 7 Nov, forecast 8 Nov.
    dates = [date(2026, 11, 7) - timedelta(days=i) for i in range(34, -1, -1)]
    base = 10.0
    info = pulse.DayInfo.build(dates + [date(2026, 11, 8)])
    F, W = pulse.Multipliers({}, {}).matrices(["sweets_box_500g"], info)  # catalog priors
    q = base * (F * W)[:, :len(dates)]  # history already carries every festival uplift
    panel = pulse.Panel(np.ones(1, int), np.zeros(1, int), np.zeros(1, int), np.ones(1), np.zeros(1, int),
                        ["sweets_box_500g"], dates, q)
    out = pulse.forecast_baseline(panel, F, W, 1)[0, 0]
    assert np.isclose(out, base * 5.0)  # Diwali x5 on the clean base, not inflated history x5


def test_learn_multipliers_recovers_event_uplift() -> None:
    """A product doubling on rainy days is learned (shrunk toward the catalog prior of 1.0)."""
    T = 60
    q = np.full((1, T), 10.0)
    weather = ["sunny"] * T
    for i in range(0, T, 3):
        weather[i], q[0, i] = "rain", 20.0
    panel = make_panel(q, ["glucose_biscuits"])  # catalog has no rain effect -> prior 1.0
    info = pulse.DayInfo.build(panel.dates, weather)
    m = pulse.learn_multipliers(panel, info)
    learned = m.weather_mult("rain", "glucose_biscuits")
    assert 1.5 < learned <= 2.0  # moved most of the way from 1.0 to 2.0


def test_learn_multipliers_keeps_prior_for_unseen_event() -> None:
    panel = make_panel(np.full((1, 8), 5.0), ["sweets_box_500g"])  # 1-8 Oct: no festival seen
    m = pulse.learn_multipliers(panel, info_for(panel))
    assert m.fest_mult("Diwali", "sweets_box_500g") == 5.0


def test_wape() -> None:
    assert np.isclose(pulse.wape(np.array([10.0, 10.0]), np.array([12.0, 8.0])), 0.2)


def test_lgbm_pipeline_runs_and_forecast_is_sane() -> None:
    rng = np.random.default_rng(0)
    q = rng.poisson(8, size=(6, 70)).astype(float)
    panel = make_panel(q)
    info = info_for(panel, 3)
    F, W = pulse.Multipliers({}, {}).matrices(panel.skus, info)
    X, y, w = pulse.build_training_set(panel, info, F, W)
    model = pulse.train_lgbm(X, y, w, n_estimators=30)
    out = pulse.forecast_lgbm(model, panel, info, F, W, 3)
    assert out.shape == (6, 3)
    assert np.all(np.isfinite(out)) and np.all(out >= 0)
    assert 3 < out.mean() < 20  # near the true mean of 8


def test_evaluate_reports_both_scores() -> None:
    rng = np.random.default_rng(1)
    panel = make_panel(rng.poisson(10, size=(8, 70)).astype(float))
    res = pulse.evaluate(panel, info_for(panel), holdout=14, n_estimators=30)
    assert res["points"] == 8 * 14
    assert 0 < res["wape_baseline"] < 1 and 0 < res["wape_lgbm"] < 1
