"""Saath Kharido: pooled buying (CLAUDE.md Section 7.3).

1. Take reorder needs from consenting shops, group by product, total them.
2. Look up the distributor tier price for the total.
3. Pool when >= 3 shops AND saving >= 3% versus the solo (tier 1) price.
4. One 'pool' proposal per member, carrying that member's own saving in rupees.
5. At the deadline only approved members are executed; the tier is recomputed on the approved total.

All money is integer paise.
"""
import argparse
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

MIN_POOL_SHOPS = 3
MIN_SAVING_PCT = 0.03
DEADLINE_HOUR = 20  # 20:00 sim time, on the day after the night cycle

Tiers = Sequence[tuple[int, int]]  # [(min_qty, unit_price_paise)]


@dataclass(frozen=True)
class MemberPlan:
    shop_id: int
    qty: int
    savings_paise: int


@dataclass(frozen=True)
class PoolPlan:
    product_id: int
    total_qty: int
    tier_price_paise: int
    solo_price_paise: int
    members: tuple[MemberPlan, ...]

    @property
    def savings_paise(self) -> int:
        return sum(m.savings_paise for m in self.members)

    @property
    def saving_pct(self) -> float:
        return 1 - self.tier_price_paise / self.solo_price_paise


# --------------------------------------------------------------------------- pure functions
def solo_price(tiers: Tiers) -> int:
    """Price a single small shop pays: the lowest-quantity tier."""
    return min(tiers, key=lambda t: t[0])[1]


def tier_price(tiers: Tiers, qty: int) -> int:
    """Unit price for an order of `qty`: the tier with the highest min_qty not above qty."""
    eligible = [t for t in tiers if t[0] <= qty]
    return max(eligible, key=lambda t: t[0])[1] if eligible else solo_price(tiers)


def plan_pool(product_id: int, qtys: dict[int, int], tiers: Tiers,
              min_shops: int = MIN_POOL_SHOPS, min_saving_pct: float = MIN_SAVING_PCT) -> PoolPlan | None:
    """Pool plan for one product from {shop_id: qty}, or None if it does not qualify."""
    qtys = {s: q for s, q in qtys.items() if q > 0}
    if len(qtys) < min_shops:
        return None
    total = sum(qtys.values())
    solo, pooled = solo_price(tiers), tier_price(tiers, total)
    if solo <= 0 or (solo - pooled) / solo < min_saving_pct:
        return None
    members = tuple(MemberPlan(s, q, q * (solo - pooled)) for s, q in sorted(qtys.items()))
    return PoolPlan(product_id, total, pooled, solo, members)


def plan_pools(needs: Sequence[tuple[int, int, int]], tiers_by_product: dict[int, Tiers],
               **kw) -> list[PoolPlan]:
    """Plans for every product that qualifies. `needs` is [(shop_id, product_id, qty)]."""
    by_product: dict[int, dict[int, int]] = defaultdict(dict)
    for shop_id, product_id, qty in needs:
        by_product[product_id][shop_id] = by_product[product_id].get(shop_id, 0) + qty
    plans = (plan_pool(p, q, tiers_by_product[p], **kw) for p, q in sorted(by_product.items()))
    return [p for p in plans if p is not None]


def reprice_pool(plan: PoolPlan, approved_shop_ids: Sequence[int], tiers: Tiers, **kw) -> PoolPlan | None:
    """Recompute a pool on the approved members only (price may drop a tier, or the pool may fail)."""
    keep = set(approved_shop_ids)
    return plan_pool(plan.product_id, {m.shop_id: m.qty for m in plan.members if m.shop_id in keep}, tiers, **kw)


# --------------------------------------------------------------------------- DB glue
@dataclass(frozen=True)
class CreatedPool:
    pool_id: int
    plan: PoolPlan
    proposal_ids: dict[int, int]  # shop_id -> proposal_id


def pool_deadline(today: date) -> datetime:
    """20:00 on the day after the night cycle, so merchants can answer in the morning."""
    return datetime.combine(today + timedelta(days=1), time(DEADLINE_HOUR, 0))


def create_pools(db: Session, needs: Sequence, today: date) -> list[CreatedPool]:
    """Create pools, members and one proposal per member from RestockNeeds.

    Only shops with a `join_pools` consent take part. Re-running for the same date replaces
    still-open pools and their undecided proposals.
    """
    from app.models import ConsentLog, Pool, PoolMember, PriceTier, Product, Proposal

    old = list(db.scalars(select(Pool.id).where(Pool.sim_date == today, Pool.status == "open")))
    if old:
        db.execute(delete(PoolMember).where(PoolMember.pool_id.in_(old)))
        db.execute(delete(Pool).where(Pool.id.in_(old)))
    db.execute(delete(Proposal).where(Proposal.sim_date == today, Proposal.type == "pool",
                                      Proposal.status == "proposed"))

    consenting = set(db.scalars(select(ConsentLog.shop_id).where(ConsentLog.scope == "join_pools")))
    tiers: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for t in db.scalars(select(PriceTier)):
        tiers[t.product_id].append((t.min_qty, t.unit_price_paise))
    products = {p.id: p for p in db.scalars(select(Product))}

    plans = plan_pools([(n.shop_id, n.product_id, n.qty) for n in needs if n.shop_id in consenting], tiers)
    created: list[CreatedPool] = []
    for plan in plans:
        prod = products[plan.product_id]
        pool = Pool(product_id=plan.product_id, total_qty=plan.total_qty, tier_price_paise=plan.tier_price_paise,
                    solo_price_paise=plan.solo_price_paise, status="open", deadline=pool_deadline(today),
                    sim_date=today)
        db.add(pool)
        db.flush()
        ids: dict[int, int] = {}
        for m in plan.members:
            prop = Proposal(
                shop_id=m.shop_id, type="pool", savings_paise=m.savings_paise, status="proposed", sim_date=today,
                payload={  # other members are only ever a count: no names, no quantities
                    "pool_id": pool.id, "product_id": prod.id, "sku": prod.sku, "name_en": prod.name_en,
                    "name_hi": prod.name_hi, "qty": m.qty, "solo_price_paise": plan.solo_price_paise,
                    "tier_price_paise": plan.tier_price_paise, "n_shops": len(plan.members),
                    "total_qty": plan.total_qty, "deadline": pool.deadline.isoformat(),
                })
            db.add(prop)
            db.flush()
            db.add(PoolMember(pool_id=pool.id, shop_id=m.shop_id, qty=m.qty, proposal_id=prop.id))
            ids[m.shop_id] = prop.id
        created.append(CreatedPool(pool.id, plan, ids))
    db.commit()
    return created


def main() -> None:
    """Run restock + pooling on the demo data and print the result (needs Pulse forecasts saved)."""
    from app.db import SessionLocal
    from app.engines.restock import compute_restock_needs
    from app.models import Product, SimState

    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", type=int, default=1, help="shop whose savings to print")
    a = ap.parse_args()
    with SessionLocal() as db:
        today = db.get(SimState, 1).current_date
        needs = compute_restock_needs(db, today)
        pools = create_pools(db, needs, today)
        names = {p.id: p.name_en for p in db.scalars(select(Product))}
        print(f"Sim date {today}: {len(needs)} reorder lines -> {len(pools)} pools")
        for c in sorted(pools, key=lambda c: -c.plan.savings_paise)[:8]:
            p = c.plan
            print(f"  {names[p.product_id]:18s} {len(p.members):2d} shops, {p.total_qty:4d} units, "
                  f"Rs{p.solo_price_paise / 100:.0f} -> Rs{p.tier_price_paise / 100:.0f} "
                  f"({p.saving_pct:.0%}), total saving Rs{p.savings_paise / 100:,.0f}")
        atta = next((c for c in pools if names[c.plan.product_id] == "Atta 5kg"), None)
        if atta:
            mine = next((m for m in atta.plan.members if m.shop_id == a.shop), None)
            print(f"\nAtta pool: {len(atta.plan.members)} shops, {atta.plan.total_qty} bags.")
            if mine:
                print(f"Shop {a.shop} orders {mine.qty} bags and saves Rs{mine.savings_paise / 100:,.0f}")


if __name__ == "__main__":
    main()
