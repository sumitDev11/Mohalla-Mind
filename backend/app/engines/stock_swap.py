"""Stock Swap: match one shop's surplus to a neighbour's shortage (CLAUDE.md Section 7.4).

    surplus  = unexpired stock - forecast(next min(7, days_to_expiry)) - safety_stock   (if > 0)
    shortage = forecast(next 1 day) + safety_stock - on_hand                            (if > 0)

Shortages are served largest first, each from the nearest surplus shop within 1000 m.
transfer_price = donor cost price (+ a small handling fee), so neighbours make no profit on each
other. A swap produces two proposals (swap_give, swap_take) and executes only when both say haan.
Money is integer paise.
"""
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.engines.restock import Key, StockInputs, expected_demand, load_inputs, safety_stock
from app.geo import haversine_m

MAX_DISTANCE_M = 1000.0
MIN_SWAP_QTY = 2
HANDLING_PAISE = 200  # Rs2 per unit, paid to the donor for the effort
SURPLUS_HORIZON_DAYS = 7


# --------------------------------------------------------------------------- pure functions
def surplus_qty(unexpired: int, daily_forecast: Sequence[float], safety: float, days_to_expiry: int) -> int:
    """Units a shop can give away: stock beyond what it will sell before expiry plus a safety buffer."""
    horizon = min(SURPLUS_HORIZON_DAYS, max(days_to_expiry, 0))
    return max(0, math.floor(round(unexpired - expected_demand(daily_forecast, horizon) - safety, 6)))


def shortage_qty(on_hand: int, daily_forecast: Sequence[float], safety: float) -> int:
    """Units a shop is short of for tomorrow, including the safety buffer."""
    tomorrow = daily_forecast[0] if daily_forecast else 0.0
    return max(0, math.ceil(round(tomorrow + safety - on_hand, 6)))


@dataclass(frozen=True)
class Shortage:
    shop_id: int
    product_id: int
    qty: int
    lat: float
    lng: float


@dataclass(frozen=True)
class Surplus:
    shop_id: int
    product_id: int
    qty: int
    lat: float
    lng: float
    cost_price_paise: int


@dataclass(frozen=True)
class SwapPlan:
    from_shop_id: int
    to_shop_id: int
    product_id: int
    qty: int
    distance_m: int
    transfer_price_paise: int  # per unit


def match_swaps(shortages: Sequence[Shortage], surpluses: Sequence[Surplus],
                max_distance_m: float = MAX_DISTANCE_M, min_qty: int = MIN_SWAP_QTY,
                handling_paise: int = HANDLING_PAISE) -> list[SwapPlan]:
    """Greedy matching: biggest shortage first, nearest donor first, within max_distance_m."""
    remaining = {(s.shop_id, s.product_id): s.qty for s in surpluses}
    plans: list[SwapPlan] = []
    for need in sorted(shortages, key=lambda s: (-s.qty, s.shop_id, s.product_id)):
        left = need.qty
        donors = sorted(
            ((haversine_m(need.lat, need.lng, d.lat, d.lng), d) for d in surpluses
             if d.product_id == need.product_id and d.shop_id != need.shop_id),
            key=lambda t: (t[0], t[1].shop_id))
        for dist, donor in donors:
            if left <= 0 or dist > max_distance_m:
                break
            q = min(left, remaining[(donor.shop_id, donor.product_id)])
            if q < min_qty:
                continue
            remaining[(donor.shop_id, donor.product_id)] -= q
            left -= q
            plans.append(SwapPlan(donor.shop_id, need.shop_id, need.product_id, q, round(dist),
                                  donor.cost_price_paise + handling_paise))
    return plans


# --------------------------------------------------------------------------- DB glue
@dataclass(frozen=True)
class CreatedSwap:
    swap_id: int
    plan: SwapPlan
    give_proposal_id: int
    take_proposal_id: int


def compute_positions(db: Session, today: date,
                      inputs: StockInputs | None = None) -> tuple[list[Shortage], list[Surplus]]:
    """Shortages and surpluses for every shop x product, from unexpired stock."""
    from app.models import Shop

    inp = inputs or load_inputs(db, today)
    shops = {s.id: s for s in db.scalars(select(Shop))}
    shortages: list[Shortage] = []
    surpluses: list[Surplus] = []
    for key, fc in inp.forecast.items():
        shop = shops[key[0]]
        live = [b for b in inp.batches.get(key, []) if b.expiry_date > today]
        on_hand = sum(b.qty for b in live)
        hist = inp.sales.get(key)
        safety = safety_stock(hist) if hist is not None else 0.0
        short = shortage_qty(on_hand, fc, safety)
        if short >= MIN_SWAP_QTY:
            shortages.append(Shortage(key[0], key[1], short, shop.lat, shop.lng))
        if live:
            dte = max((b.expiry_date - today).days for b in live)
            extra = surplus_qty(on_hand, fc, safety, dte)
            if extra >= MIN_SWAP_QTY:
                cost = round(sum(b.qty * b.cost_price_paise for b in live) / on_hand)
                surpluses.append(Surplus(key[0], key[1], extra, shop.lat, shop.lng, cost))
    return shortages, surpluses


def create_swaps(db: Session, today: date, inputs: StockInputs | None = None) -> list[CreatedSwap]:
    """Match swaps and create a Swap row plus swap_give / swap_take proposals for each.

    Proposals name only the distance, never the neighbour. Re-running for the same date replaces
    undecided swaps and proposals.
    """
    from app.models import Product, Proposal, Swap

    db.execute(delete(Proposal).where(Proposal.sim_date == today, Proposal.type.in_(["swap_give", "swap_take"]),
                                      Proposal.status == "proposed"))
    db.execute(delete(Swap).where(Swap.sim_date == today, Swap.status == "proposed"))

    shortages, surpluses = compute_positions(db, today, inputs)
    products = {p.id: p for p in db.scalars(select(Product))}
    created: list[CreatedSwap] = []
    for plan in match_swaps(shortages, surpluses):
        prod = products[plan.product_id]
        swap = Swap(from_shop_id=plan.from_shop_id, to_shop_id=plan.to_shop_id, product_id=plan.product_id,
                    qty=plan.qty, distance_m=plan.distance_m, transfer_price_paise=plan.transfer_price_paise,
                    status="proposed", sim_date=today)
        db.add(swap)
        db.flush()
        base = {"swap_id": swap.id, "product_id": prod.id, "sku": prod.sku, "name_en": prod.name_en,
                "name_hi": prod.name_hi, "qty": plan.qty, "distance_m": plan.distance_m,
                "transfer_price_paise": plan.transfer_price_paise}
        give = Proposal(shop_id=plan.from_shop_id, type="swap_give", status="proposed", sim_date=today,
                        savings_paise=plan.qty * plan.transfer_price_paise, payload={**base, "role": "give"})
        take = Proposal(shop_id=plan.to_shop_id, type="swap_take", status="proposed", sim_date=today,
                        savings_paise=plan.qty * max(0, prod.mrp_paise - plan.transfer_price_paise),
                        payload={**base, "role": "take", "mrp_paise": prod.mrp_paise})
        db.add_all([give, take])
        db.flush()
        created.append(CreatedSwap(swap.id, plan, give.id, take.id))
    db.commit()
    return created


def swap_flows(created: Sequence[CreatedSwap]) -> tuple[dict[Key, int], dict[Key, int]]:
    """(outgoing, incoming) units per (shop_id, product_id) across planned swaps."""
    out: dict[Key, int] = defaultdict(int)
    inc: dict[Key, int] = defaultdict(int)
    for c in created:
        out[(c.plan.from_shop_id, c.plan.product_id)] += c.plan.qty
        inc[(c.plan.to_shop_id, c.plan.product_id)] += c.plan.qty
    return dict(out), dict(inc)
