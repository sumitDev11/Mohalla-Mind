"""Mohalla Pulse: per shop x product demand forecasting (CLAUDE.md Section 7.1).

v1 baseline : 0.5 * mean(last 4 same-weekday) + 0.5 * mean(last 14 days), computed on history
              with festival/weather effects divided out, then multiplied back for the target day.
v2 LightGBM : one global model predicting the ratio  actual / baseline  (so festivals never seen in
              training, like Diwali, still extrapolate through the baseline's multipliers).

Everything except the DB loaders/savers is a pure function over numpy arrays.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data import catalog as cat

BASELINE_VERSION = "baseline_v1"
LGBM_VERSION = "lgbm_v2"
FEST_PRECEDENCE = ["Diwali", "Dussehra", "Navratri", "Raksha Bandhan"]
WEATHERS = ["sunny", "rain", "hot", "cold"]
MIN_HISTORY = 28  # rm28 / same-weekday lags need 28 days of context
MIN_BASE = 0.2  # floor on the baseline when forming ratios
SHRINK_K = 5.0  # pseudo-days of weight on the catalog prior when learning multipliers
TYPE_CODE = {"kirana": 0, "dairy": 1, "general": 2}


# --------------------------------------------------------------------------- data containers
@dataclass
class Panel:
    """Dense sales panel: one row per (shop, product) series, one column per day."""

    shop_ids: np.ndarray  # (S,)
    product_ids: np.ndarray  # (S,)
    prod_idx: np.ndarray  # (S,) index into skus
    size_mult: np.ndarray  # (S,)
    type_code: np.ndarray  # (S,)
    skus: list[str]
    dates: list[date]  # length T
    Q: np.ndarray  # (S, T) units sold

    def cut(self, upto: int) -> "Panel":
        """Panel restricted to the first `upto` days."""
        return Panel(self.shop_ids, self.product_ids, self.prod_idx, self.size_mult, self.type_code,
                     self.skus, self.dates[:upto], self.Q[:, :upto])


@dataclass
class DayInfo:
    """Per-date context (history followed by the forecast horizon)."""

    dates: list[date]
    fest: list[str]  # festival window label ("" if none)
    weather: list[str]
    temp: np.ndarray
    dtf: np.ndarray  # days to next festival day, capped

    @classmethod
    def build(cls, dates: list[date], weather: list[str] | None = None,
              temp: list[float] | None = None) -> "DayInfo":
        n = len(dates)
        return cls(dates, [festival_label(d) for d in dates], weather or ["sunny"] * n,
                   np.asarray(temp if temp is not None else [25.0] * n, dtype=float),
                   np.asarray([days_to_festival(d) for d in dates], dtype=float))

    def __len__(self) -> int:
        return len(self.dates)


def festival_label(d: date) -> str:
    """Festival whose effect window (day + 2 days before) contains d, else ''."""
    for name in FEST_PRECEDENCE:
        if any(0 <= (f - d).days <= 2 for f in cat.FESTIVALS[name]):
            return name
    return ""


def days_to_festival(d: date, cap: int = 14) -> int:
    """Days until the next festival day (0 on the day), capped."""
    gaps = [(f - d).days for fs in cat.FESTIVALS.values() for f in fs if (f - d).days >= 0]
    return min(min(gaps), cap) if gaps else cap


# --------------------------------------------------------------------------- multipliers
@dataclass
class Multipliers:
    """Festival / weather multipliers: learned from history, shrunk toward catalog priors."""

    fest: dict[tuple[str, str], float]
    weather: dict[tuple[str, str], float]

    def fest_mult(self, label: str, sku: str) -> float:
        if not label:
            return 1.0
        return self.fest.get((label, sku), cat.FESTIVAL_MULT[label].get(sku, 1.0))

    def weather_mult(self, weather: str, sku: str) -> float:
        return self.weather.get((weather, sku), cat.weather_mult(weather, sku))

    def matrices(self, skus: list[str], info: DayInfo) -> tuple[np.ndarray, np.ndarray]:
        """(festival (P,D), weather (P,D)) multiplier matrices."""
        F = np.array([[self.fest_mult(l, s) for l in info.fest] for s in skus])
        W = np.array([[self.weather_mult(w, s) for w in info.weather] for s in skus])
        return F, W


def learn_multipliers(panel: Panel, info: DayInfo, k: float = SHRINK_K) -> Multipliers:
    """Estimate multipliers as (event-day mean) / (neutral sunny-day mean) per product, aggregated
    over shops, shrunk toward the catalog value with weight n/(n+k). Unseen events keep the prior."""
    P, (_, T) = len(panel.skus), panel.Q.shape
    A = np.zeros((P, T))
    np.add.at(A, panel.prod_idx, panel.Q)
    fest, wthr = info.fest[:T], info.weather[:T]
    neutral = [i for i in range(T) if not fest[i] and wthr[i] == "sunny"]
    if len(neutral) < 5:
        return Multipliers({}, {})
    base = A[:, neutral].mean(axis=1)

    def shrunk(idx: list[int], prior: np.ndarray) -> np.ndarray:
        ratio = np.where(base > 0, A[:, idx].mean(axis=1) / np.maximum(base, 1e-9), prior)
        ratio = np.clip(ratio, 0.5, 6.0)
        w = len(idx) / (len(idx) + k)
        return w * ratio + (1 - w) * prior

    fest_m: dict[tuple[str, str], float] = {}
    for label in {l for l in fest if l}:
        idx = [i for i in range(T) if fest[i] == label]
        prior = np.array([cat.FESTIVAL_MULT[label].get(s, 1.0) for s in panel.skus])
        for s, v in zip(panel.skus, shrunk(idx, prior)):
            fest_m[(label, s)] = float(v)
    wthr_m: dict[tuple[str, str], float] = {}
    for wn in ("rain", "hot", "cold"):
        idx = [i for i in range(T) if not fest[i] and wthr[i] == wn]
        if not idx:
            continue
        prior = np.array([cat.weather_mult(wn, s) for s in panel.skus])
        for s, v in zip(panel.skus, shrunk(idx, prior)):
            wthr_m[(wn, s)] = float(v)
    return Multipliers(fest_m, wthr_m)


# --------------------------------------------------------------------------- v1 baseline
def baseline_norm(Qn: np.ndarray, j: int) -> np.ndarray:
    """Baseline for column j from normalised history: 0.5*same-weekday(4) + 0.5*last-14 mean."""
    same_dow = np.mean([Qn[:, j - 7 * k] for k in range(1, 5)], axis=0)
    last14 = Qn[:, j - 14:j].mean(axis=1)
    return 0.5 * same_dow + 0.5 * last14


def forecast_baseline(panel: Panel, F: np.ndarray, W: np.ndarray, horizon: int) -> np.ndarray:
    """v1 forecast, shape (S, horizon), for the days after the panel's last date.
    F and W must cover T + horizon columns."""
    S, T = panel.Q.shape
    M = (F * W)[panel.prod_idx]
    Qn = np.zeros((S, T + horizon))
    Qn[:, :T] = panel.Q / M[:, :T]
    out = np.zeros((S, horizon))
    for h in range(horizon):
        j = T + h
        bn = baseline_norm(Qn, j)
        Qn[:, j] = bn
        out[:, h] = bn * M[:, j]
    return out


# --------------------------------------------------------------------------- v2 LightGBM
def _features(panel: Panel, info: DayInfo, F: np.ndarray, W: np.ndarray, Q: np.ndarray,
              j: int, base_pred: np.ndarray) -> pd.DataFrame:
    """Feature frame (one row per series) for target column j; Q holds actuals or recursive preds."""
    S = Q.shape[0]
    d = info.dates[j]
    cols: dict[str, np.ndarray] = {
        "product": panel.prod_idx.astype(int),
        "shop_size": panel.size_mult,
        "shop_type": panel.type_code.astype(int),
        "dow": np.full(S, d.weekday()),
        "is_weekend": np.full(S, int(d.weekday() >= 5)),
        "days_to_festival": np.full(S, info.dtf[j]),
        "weather": np.full(S, WEATHERS.index(info.weather[j])),
        "temp": np.full(S, info.temp[j]),
        "fest_mult": F[panel.prod_idx, j],
        "weather_mult": W[panel.prod_idx, j],
        "lag_1": Q[:, j - 1], "lag_7": Q[:, j - 7], "lag_14": Q[:, j - 14],
        "rolling_mean_7": Q[:, j - 7:j].mean(axis=1),
        "rolling_mean_28": Q[:, j - 28:j].mean(axis=1),
        "base_pred": base_pred,
    }
    for name in FEST_PRECEDENCE:
        cols["fest_" + name.lower().replace(" ", "_")] = np.full(S, int(info.fest[j] == name))
    return pd.DataFrame(cols)


def build_training_set(panel: Panel, info: DayInfo, F: np.ndarray,
                       W: np.ndarray) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Stack features for every target day j >= MIN_HISTORY. Target is actual / max(base, MIN_BASE)."""
    S, T = panel.Q.shape
    M = (F * W)[panel.prod_idx]
    Qn = panel.Q / M[:, :T]
    frames, ys, ws = [], [], []
    for j in range(MIN_HISTORY, T):
        base = baseline_norm(Qn, j) * M[:, j]
        base_c = np.maximum(base, MIN_BASE)
        frames.append(_features(panel, info, F, W, panel.Q, j, base))
        ys.append(panel.Q[:, j] / base_c)
        ws.append(base_c)
    return pd.concat(frames, ignore_index=True), np.concatenate(ys), np.concatenate(ws)


def train_lgbm(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, n_estimators: int = 300):
    """Fit the global LightGBM ratio model (deterministic: seed 42, single thread)."""
    from lightgbm import LGBMRegressor

    model = LGBMRegressor(objective="regression", n_estimators=n_estimators, learning_rate=0.05,
                          num_leaves=31, min_child_samples=20, subsample=0.8, subsample_freq=1,
                          colsample_bytree=0.9, random_state=42, n_jobs=1, verbose=-1)
    model.fit(X, y, sample_weight=w, categorical_feature=["product"])
    return model


def _ratio(model, X: pd.DataFrame) -> np.ndarray:
    return np.clip(model.predict(X), 0.0, 8.0)


def forecast_lgbm(model, panel: Panel, info: DayInfo, F: np.ndarray, W: np.ndarray,
                  horizon: int) -> np.ndarray:
    """Recursive multi-day LightGBM forecast, shape (S, horizon)."""
    S, T = panel.Q.shape
    M = (F * W)[panel.prod_idx]
    Q = np.zeros((S, T + horizon))
    Q[:, :T] = panel.Q
    Qn = Q / M
    out = np.zeros((S, horizon))
    for h in range(horizon):
        j = T + h
        base = baseline_norm(Qn, j) * M[:, j]
        pred = _ratio(model, _features(panel, info, F, W, Q, j, base)) * np.maximum(base, MIN_BASE)
        Q[:, j], Qn[:, j], out[:, h] = pred, pred / M[:, j], pred
    return out


# --------------------------------------------------------------------------- evaluation
def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    """Weighted absolute percentage error: sum|a-p| / sum(a)."""
    total = float(np.sum(actual))
    return float(np.sum(np.abs(actual - pred)) / total) if total > 0 else float("nan")


def evaluate(panel: Panel, info: DayInfo, holdout: int = 14, n_estimators: int = 300) -> dict:
    """One-step-ahead WAPE on the last `holdout` days for baseline vs LightGBM.
    Multipliers and the model only ever see the days before the holdout."""
    S, T = panel.Q.shape
    cut = T - holdout
    mult = learn_multipliers(panel.cut(cut), info)
    F, W = mult.matrices(panel.skus, info)
    M = (F * W)[panel.prod_idx]
    X, y, w = build_training_set(panel.cut(cut), info, F, W)
    model = train_lgbm(X, y, w, n_estimators)

    Qn = panel.Q / M[:, :T]
    act, p_base, p_lgb, in_fest = [], [], [], []
    for j in range(cut, T):
        base = baseline_norm(Qn, j) * M[:, j]
        pred = _ratio(model, _features(panel, info, F, W, panel.Q, j, base)) * np.maximum(base, MIN_BASE)
        act.append(panel.Q[:, j]); p_base.append(base); p_lgb.append(pred)
        in_fest.append(np.full(S, bool(info.fest[j])))
    act, p_base, p_lgb, in_fest = (np.concatenate(a) for a in (act, p_base, p_lgb, in_fest))
    res = {
        "holdout_days": holdout, "holdout_from": panel.dates[cut].isoformat(),
        "holdout_to": panel.dates[-1].isoformat(), "points": int(act.size),
        "wape_baseline": wape(act, p_base), "wape_lgbm": wape(act, p_lgb),
    }
    if in_fest.any():
        res["festival_window_wape_baseline"] = wape(act[in_fest], p_base[in_fest])
        res["festival_window_wape_lgbm"] = wape(act[in_fest], p_lgb[in_fest])
    return res


# --------------------------------------------------------------------------- DB glue
def load_panel(db: Session) -> Panel:
    """Load all sales into a dense Panel (missing days -> 0 units)."""
    from app.models import Product, Sale, Shop

    shops = list(db.scalars(select(Shop).order_by(Shop.id)))
    products = list(db.scalars(select(Product).order_by(Product.id)))
    rows = db.execute(select(Sale.shop_id, Sale.product_id, Sale.date, Sale.qty)).all()
    df = pd.DataFrame(rows, columns=["shop_id", "product_id", "date", "qty"])
    d0, d1 = df["date"].min(), df["date"].max()
    dates = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    spos = {s.id: i for i, s in enumerate(shops)}
    ppos = {p.id: i for i, p in enumerate(products)}
    P = len(products)
    Q = np.zeros((len(shops) * P, len(dates)))
    sidx = df["shop_id"].map(spos).to_numpy() * P + df["product_id"].map(ppos).to_numpy()
    tidx = np.array([(d - d0).days for d in df["date"]])
    Q[sidx, tidx] = df["qty"].to_numpy()
    return Panel(
        shop_ids=np.repeat([s.id for s in shops], P),
        product_ids=np.tile([p.id for p in products], len(shops)),
        prod_idx=np.tile(np.arange(P), len(shops)),
        size_mult=np.repeat([cat.SIZE_MULT[s.size] for s in shops], P),
        type_code=np.repeat([TYPE_CODE[s.shop_type] for s in shops], P),
        skus=[p.sku for p in products], dates=dates, Q=Q)


def load_day_info(db: Session, dates: list[date]) -> DayInfo:
    """DayInfo for `dates` using the calendar table (sunny/25C where a date is missing)."""
    from app.models import CalendarDay

    cal = {c.date: c for c in db.scalars(select(CalendarDay).where(CalendarDay.date.in_(dates)))}
    weather = [cal[d].weather if d in cal else "sunny" for d in dates]
    temp = [cal[d].temp_c if d in cal else 25.0 for d in dates]
    return DayInfo.build(dates, weather, temp)


def run_pulse(db: Session, horizon: int = 7, n_estimators: int = 300) -> dict:
    """Train on all history, forecast the next `horizon` days with both models and save them.
    Returns {"dates": [...], "versions": [...], "rows": n}."""
    from app.models import Forecast

    panel = load_panel(db)
    last = panel.dates[-1]
    future = [last + timedelta(days=i + 1) for i in range(horizon)]
    info = load_day_info(db, panel.dates + future)
    mult = learn_multipliers(panel, info)
    F, W = mult.matrices(panel.skus, info)

    preds = {BASELINE_VERSION: forecast_baseline(panel, F, W, horizon)}
    X, y, w = build_training_set(panel, info, F, W)
    model = train_lgbm(X, y, w, n_estimators)
    preds[LGBM_VERSION] = forecast_lgbm(model, panel, info, F, W, horizon)

    db.query(Forecast).filter(Forecast.date.in_(future), Forecast.model_version.in_(list(preds))).delete(
        synchronize_session=False)
    objs = []
    for version, arr in preds.items():
        for s in range(arr.shape[0]):
            for h in range(horizon):
                q = round(float(arr[s, h]), 2)
                objs.append(Forecast(shop_id=int(panel.shop_ids[s]), product_id=int(panel.product_ids[s]),
                                     date=future[h], qty_pred=q, qty_low=round(q * 0.8, 2),
                                     qty_high=round(q * 1.2, 2), model_version=version))
    db.add_all(objs)
    db.commit()
    return {"dates": [d.isoformat() for d in future], "versions": list(preds), "rows": len(objs)}


def load_forecasts(db: Session, d: date) -> dict[tuple[int, int], float]:
    """{(shop_id, product_id): qty_pred} for date d, preferring LightGBM over the baseline."""
    from app.models import Forecast

    out: dict[tuple[int, int], float] = {}
    for version in (BASELINE_VERSION, LGBM_VERSION):  # later (preferred) version overwrites
        for f in db.scalars(select(Forecast).where(Forecast.date == d, Forecast.model_version == version)):
            out[(f.shop_id, f.product_id)] = f.qty_pred
    return out
