"""Restock: how many units should each shop reorder? (CLAUDE.md Section 7.2)

    target       = sum(forecast over lead_time + 1 days) + safety_stock
    safety_stock = 1.65 * std(daily sales, last 28 days) * sqrt(lead_time)
    reorder_qty  = max(0, ceil(target - usable_on_hand))

"usable" excludes stock that would expire before it can plausibly be sold.
This module also holds the shared stock helpers (FEFO usability, input loading) that the
swap and surplus engines reuse.
"""
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

LEAD_TIME_DAYS = 2
SERVICE_Z = 1.65  # ~95% service level
HISTORY_DAYS = 28
FORECAST_DAYS = 7


# --------------------------------------------------------------------------- pure functions
def safety_stock(daily_sales: Sequence[float], lead_time_days: int = LEAD_TIME_DAYS) -> float:
    """1.65 * sample std of daily sales * sqrt(lead time). Zero with fewer than 2 observations."""
    if len(daily_sales) < 2:
        return 0.0
    return SERVICE_Z * float(np.std(daily_sales, ddof=1)) * math.sqrt(lead_time_days)


def expected_demand(daily_forecast: Sequence[float], days: int) -> float:
    """Expected units sold over the next `days` days (the last forecast day is repeated if short)."""
    if days <= 0 or not daily_forecast:
        return 0.0
    padded = list(daily_forecast) + [daily_forecast[-1]] * max(0, days - len(daily_forecast))
    return float(sum(padded[:days]))


def usable_by_batch(batches: Sequence[tuple[int, date]], today: date,
                    daily_forecast: Sequence[float]) -> list[int]:
    """Units of each batch that can be sold before it expires, selling first-expiry-first-out.

    `batches` is [(qty, expiry_date)]; a batch is sellable through its expiry date, starting
    tomorrow. A batch's usable part is capped by the demand expected up to its expiry minus what
    earlier-expiring batches already absorb. The result is aligned with the input order.
    """
    result = [0] * len(batches)
    consumed = 0.0
    for i in sorted(range(len(batches)), key=lambda k: batches[k][1]):
        qty, expiry = batches[i]
        sellable_days = (expiry - today).days
        if sellable_days <= 0 or qty <= 0:
            continue
        room = max(0.0, expected_demand(daily_forecast, sellable_days) - consumed)
        take = min(qty, int(math.floor(room + 1e-9)))
        if take < qty and room - take > 1e-9:  # a fractional unit of demand still sells one more
            take += 1
        result[i] = take
        consumed += take
    return result


def usable_units(batches: Sequence[tuple[int, date]], today: date, daily_forecast: Sequence[float]) -> int:
    """Total units sellable before expiry (see usable_by_batch)."""
    return sum(usable_by_batch(batches, today, daily_forecast))


def reorder_qty(daily_forecast: Sequence[float], daily_sales: Sequence[float], usable_on_hand: int,
                lead_time_days: int = LEAD_TIME_DAYS) -> int:
    """Units to reorder so usable stock covers lead time + 1 day of forecast demand plus safety stock."""
    target = expected_demand(daily_forecast, lead_time_days + 1) + safety_stock(daily_sales, lead_time_days)
    return max(0, math.ceil(round(target - usable_on_hand, 6)))


# --------------------------------------------------------------------------- DB glue
Key = tuple[int, int]  # (shop_id, product_id)


@dataclass
class StockInputs:
    """Everything the stock engines need for one night, keyed by (shop_id, product_id)."""

    forecast: dict[Key, list[float]]  # next FORECAST_DAYS days
    sales: dict[Key, np.ndarray]  # last HISTORY_DAYS days, oldest first
    batches: dict[Key, list]  # InventoryBatch rows with qty > 0


def load_inputs(db: Session, today: date) -> StockInputs:
    """Load forecasts, 28-day sales history and live batches. Requires Pulse forecasts for today+1.."""
    from app.engines.pulse import load_forecasts
    from app.models import InventoryBatch, Sale

    per_day = [load_forecasts(db, today + timedelta(days=i + 1)) for i in range(FORECAST_DAYS)]
    if not per_day[0]:
        raise ValueError(f"No forecasts for {today + timedelta(days=1)}; run Pulse first")
    forecast = {key: [day.get(key, 0.0) for day in per_day] for key in per_day[0]}

    since = today - timedelta(days=HISTORY_DAYS - 1)
    sales: dict[Key, np.ndarray] = {}
    for s in db.execute(select(Sale.shop_id, Sale.product_id, Sale.date, Sale.qty)
                        .where(Sale.date >= since, Sale.date <= today)):
        sales.setdefault((s.shop_id, s.product_id), np.zeros(HISTORY_DAYS))[(s.date - since).days] = s.qty

    batches: dict[Key, list] = {}
    for b in db.scalars(select(InventoryBatch).where(InventoryBatch.qty > 0)):
        batches.setdefault((b.shop_id, b.product_id), []).append(b)
    return StockInputs(forecast, sales, batches)


@dataclass(frozen=True)
class RestockNeed:
    shop_id: int
    product_id: int
    qty: int  # reorder quantity (> 0)
    on_hand: int
    usable: int
    target: float


def compute_restock_needs(db: Session, today: date, adjustments: dict[Key, int] | None = None,
                          inputs: StockInputs | None = None) -> list[RestockNeed]:
    """Reorder needs (qty > 0) for every shop x product on the night of `today`.

    `adjustments` adds incoming units per (shop_id, product_id), e.g. stock arriving from a swap,
    so swapped units reduce restock needs (and negative values remove outgoing units).
    """
    adjustments = adjustments or {}
    inp = inputs or load_inputs(db, today)
    needs: list[RestockNeed] = []
    for key, fc in inp.forecast.items():
        stock = [(b.qty, b.expiry_date) for b in inp.batches.get(key, [])]
        extra = adjustments.get(key, 0)
        on_hand = sum(q for q, _ in stock) + extra
        usable = max(0, usable_units(stock, today, fc) + extra)
        hist = inp.sales.get(key, np.zeros(HISTORY_DAYS))
        qty = reorder_qty(fc, hist, usable)
        if qty > 0:
            target = expected_demand(fc, LEAD_TIME_DAYS + 1) + safety_stock(hist)
            needs.append(RestockNeed(key[0], key[1], qty, on_hand, usable, round(target, 2)))
    return needs
