"""Surplus-to-Shopper: near-expiry discount deals (CLAUDE.md Section 7.5).

For each batch expiring within 3 days:  excess = qty - expected sales before expiry.
Discount: 3 days -> 10%, 2 days -> 20%, 1 day -> 30%. Never below cost except on the final day.
Swaps take priority: units already promised to a neighbour are removed from the excess first.
Money is integer paise.
"""
import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.engines.restock import Key, StockInputs, load_inputs, usable_by_batch

DISCOUNT_BY_DAYS = {3: 10, 2: 20, 1: 30}
MIN_DEAL_QTY = 5
MAX_DEAL_DAYS = 3


# --------------------------------------------------------------------------- pure functions
@dataclass(frozen=True)
class DealPlan:
    qty: int
    discount_pct: int  # effective discount after the cost floor
    deal_price_paise: int
    days_to_expiry: int


def net_excess_after_swaps(excess_by_expiry: Sequence[int], swapped_out: int) -> list[int]:
    """Remove units already promised to swaps from batch excesses, nearest expiry first.
    Input and output are ordered by expiry date (soonest first)."""
    left = swapped_out
    out = []
    for e in excess_by_expiry:
        cut = min(e, left)
        left -= cut
        out.append(e - cut)
    return out


def plan_deal(excess: int, days_to_expiry: int, mrp_paise: int, cost_paise: int,
              min_qty: int = MIN_DEAL_QTY) -> DealPlan | None:
    """Deal for a batch's excess units, or None if it does not qualify."""
    if not 1 <= days_to_expiry <= MAX_DEAL_DAYS or excess < min_qty:
        return None
    price = round(mrp_paise * (100 - DISCOUNT_BY_DAYS[days_to_expiry]) / 100)
    if days_to_expiry > 1:  # only the final day may go below cost
        price = max(price, cost_paise)
    if price >= mrp_paise:
        return None  # no room to discount
    return DealPlan(excess, round((mrp_paise - price) * 100 / mrp_paise), price, days_to_expiry)


# --------------------------------------------------------------------------- DB glue
@dataclass(frozen=True)
class CreatedDeal:
    deal_id: int
    proposal_id: int
    shop_id: int
    product_id: int
    plan: DealPlan


def create_deals(db: Session, today: date, swapped_out: dict[Key, int] | None = None,
                 inputs: StockInputs | None = None) -> list[CreatedDeal]:
    """Create a proposed Deal plus a 'deal' proposal for every near-expiry batch with excess stock.
    Re-running for the same date replaces undecided deals."""
    from app.models import Deal, Product, Proposal

    swapped_out = swapped_out or {}
    inp = inputs or load_inputs(db, today)
    products = {p.id: p for p in db.scalars(select(Product))}
    db.execute(delete(Proposal).where(Proposal.sim_date == today, Proposal.type == "deal",
                                      Proposal.status == "proposed"))
    db.execute(delete(Deal).where(Deal.sim_date == today, Deal.status == "proposed"))

    created: list[CreatedDeal] = []
    for key, fc in inp.forecast.items():
        live = sorted((b for b in inp.batches.get(key, []) if b.expiry_date > today),
                      key=lambda b: (b.expiry_date, b.id))
        if not live or (live[0].expiry_date - today).days > MAX_DEAL_DAYS:
            continue
        usable = usable_by_batch([(b.qty, b.expiry_date) for b in live], today, fc)
        excess = net_excess_after_swaps([b.qty - u for b, u in zip(live, usable)], swapped_out.get(key, 0))
        prod = products[key[1]]
        for batch, ex in zip(live, excess):
            plan = plan_deal(ex, (batch.expiry_date - today).days, prod.mrp_paise, batch.cost_price_paise)
            if plan is None:
                continue
            deal = Deal(shop_id=key[0], product_id=key[1], batch_id=batch.id, discount_pct=plan.discount_pct,
                        deal_price_paise=plan.deal_price_paise, qty=plan.qty, qty_redeemed=0,
                        expires_at=batch.expiry_date, status="proposed", sim_date=today)
            db.add(deal)
            db.flush()
            deal.qr_payload = f"mohallamind://deal/{deal.id}"
            prop = Proposal(
                shop_id=key[0], type="deal", status="proposed", sim_date=today,
                savings_paise=plan.qty * plan.deal_price_paise,  # revenue rescued from waste
                payload={"deal_id": deal.id, "product_id": prod.id, "sku": prod.sku, "name_en": prod.name_en,
                         "name_hi": prod.name_hi, "qty": plan.qty, "discount_pct": plan.discount_pct,
                         "mrp_paise": prod.mrp_paise, "deal_price_paise": plan.deal_price_paise,
                         "expires_at": batch.expiry_date.isoformat(), "days_to_expiry": plan.days_to_expiry})
            db.add(prop)
            db.flush()
            created.append(CreatedDeal(deal.id, prop.id, key[0], key[1], plan))
    db.commit()
    return created


def main() -> None:
    """Run swaps then deals on the demo data and print the shop's results."""
    from app.db import SessionLocal
    from app.engines.stock_swap import create_swaps, swap_flows
    from app.models import Product, SimState

    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", type=int, default=1)
    a = ap.parse_args()
    with SessionLocal() as db:
        today = db.get(SimState, 1).current_date
        inp = load_inputs(db, today)
        swaps = create_swaps(db, today, inp)
        out, _ = swap_flows(swaps)
        deals = create_deals(db, today, out, inp)
        names = {p.id: p.name_en for p in db.scalars(select(Product))}
        print(f"Sim date {today}: {len(swaps)} swaps, {len(deals)} deals")
        for c in swaps:
            p = c.plan
            tag = " <-- shop %d" % a.shop if a.shop in (p.from_shop_id, p.to_shop_id) else ""
            print(f"  swap {names[p.product_id]:12s} {p.qty:3d} units  {p.from_shop_id:2d} -> {p.to_shop_id:2d}  "
                  f"{p.distance_m} m  Rs{p.transfer_price_paise / 100:.0f}/unit{tag}")
        for c in deals:
            if c.shop_id == a.shop:
                d = c.plan
                print(f"  deal shop {a.shop}: {names[c.product_id]} {d.qty} units, {d.discount_pct}% off "
                      f"-> Rs{d.deal_price_paise / 100:.2f}, expires in {d.days_to_expiry} days")


if __name__ == "__main__":
    main()
