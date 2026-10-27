"""Synthetic mohalla generator.

Run: python -m app.data.generate --shops 30 --days 90 --seed 42 --reset
"""
import argparse
import math
import time
from datetime import date, datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.data import catalog as cat
from app.db import Base, SessionLocal, engine
from app.geo import haversine_m
from app.models import (
    CalendarDay, ConsentLog, InventoryBatch, PriceTier, Product, Sale, Shop, SimState,
)

KIRANA_NAMES = [
    "Sunita Kirana Store", "Sharma Kirana", "Verma Kirana Store", "Agarwal Provision Store",
    "Singh General Kirana", "Mehra Kirana", "Jain Kirana Store", "Kapoor Provision Store",
    "Bansal Kirana", "Malhotra Kirana", "Arora Kirana Store", "Chopra Provision Store",
    "Bhatia Kirana", "Saxena Kirana Store", "Tiwari Kirana", "Pandey Kirana Store",
    "Yadav Kirana", "Gupta Kirana", "Mishra Kirana Store", "Goel Provision Store",
    "Anand Kirana", "Rastogi Kirana Store", "Joshi Kirana", "Khanna Kirana Store",
]
DAIRY_NAMES = ["Gupta Dairy", "Sharma Dairy", "Krishna Dairy", "Shiv Dairy", "Ganga Dairy"]
GENERAL_NAMES = ["Sunrise General Store", "Balaji General Store", "Ganesh General Store",
                 "New Delhi General Store", "Laxmi General Store"]
OWNERS = [
    "Sunita Ji", "Ramesh Ji", "Anil Ji", "Meena Ji", "Suresh Ji", "Kavita Ji", "Rajesh Ji",
    "Pooja Ji", "Mahesh Ji", "Neeta Ji", "Vinod Ji", "Rekha Ji", "Dinesh Ji", "Seema Ji",
    "Ashok Ji", "Geeta Ji", "Manoj Ji", "Usha Ji", "Sanjay Ji", "Anita Ji", "Pawan Ji",
    "Renu Ji", "Deepak Ji", "Savita Ji", "Naresh Ji", "Kiran Ji", "Mukesh Ji", "Shobha Ji",
    "Vijay Ji", "Lata Ji",
]
MAX_RADIUS_M = 900.0


def _offset(lat: float, lng: float, dist_m: float, angle: float) -> tuple[float, float]:
    """Move (lat, lng) by dist_m metres at angle radians."""
    dlat = dist_m * math.sin(angle) / 111_320.0
    dlng = dist_m * math.cos(angle) / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lng + dlng


def _make_shops(n: int, rng: np.random.Generator) -> list[Shop]:
    """Create n shops; shop 1 is Sunita (M kirana), shops 2-3 sit within 500 m of her."""
    n_dairy = round(n * 4 / 30)
    n_general = round(n * 4 / 30)
    types = ["kirana"] * (n - 1 - n_dairy - n_general) + ["dairy"] * n_dairy + ["general"] * n_general
    rng.shuffle(types)
    types = ["kirana"] + types
    n_s, n_l = round(n * 0.3), round(n * 0.2)
    sizes = ["S"] * n_s + ["L"] * n_l + ["M"] * (n - n_s - n_l)
    sizes.remove("M")
    rng.shuffle(sizes)
    sizes = ["M"] + sizes

    pools = {"kirana": list(KIRANA_NAMES), "dairy": list(DAIRY_NAMES), "general": list(GENERAL_NAMES)}
    clat, clng = settings.mohalla_center_lat, settings.mohalla_center_lng
    shops: list[Shop] = []
    for i in range(n):
        t = types[i]
        pool = pools[t]
        name = pool.pop(0) if pool else f"{t.title()} Shop {i + 1}"
        if i == 0:
            lat, lng = _offset(clat, clng, 40.0, 0.6)
        elif i in (1, 2):  # planted paneer neighbours, 250-400 m from Sunita
            lat, lng = _offset(shops[0].lat, shops[0].lng, float(rng.uniform(250, 400)), float(rng.uniform(0, 2 * math.pi)))
        else:
            r = MAX_RADIUS_M * math.sqrt(float(rng.uniform(0.02, 1.0)))
            lat, lng = _offset(clat, clng, r, float(rng.uniform(0, 2 * math.pi)))
        shops.append(Shop(
            id=i + 1, name=name, owner_name=OWNERS[i % len(OWNERS)], shop_type=t, size=sizes[i],
            lat=round(lat, 6), lng=round(lng, 6), language="hi-IN", soundbox_id=f"SB{100000 + i + 1}",
        ))
    return shops


def _make_calendar(start: date, end: date, rng: np.random.Generator) -> list[CalendarDay]:
    """Daily festival / weather / temperature rows from start to end inclusive."""
    base_temp = {8: 33.0, 9: 32.0, 10: 28.0, 11: 22.0}
    rain_p = {8: 0.35, 9: 0.25, 10: 0.05, 11: 0.03}
    rows = []
    d = start
    while d <= end:
        temp = base_temp.get(d.month, 25.0) + float(rng.normal(0, 2.5))
        rain = rng.random() < rain_p.get(d.month, 0.05)
        if rain:
            temp -= 5
        weather = "rain" if rain else "hot" if temp > 35 else "cold" if temp < 15 else "sunny"
        rows.append(CalendarDay(date=d, festival=cat.festival_on(d), weather=weather, temp_c=round(temp, 1)))
        d += timedelta(days=1)
    return rows


def _make_sales(shops: list[Shop], products: list[Product], days: list[date],
                cal: dict[date, CalendarDay], rng: np.random.Generator) -> list[dict]:
    """Demand model: base x size x dow x festival x weather x lognormal noise."""
    rows: list[dict] = []
    for s in shops:
        for p in products:
            rate = cat.daily_rate(p.sku, s.size, s.shop_type)
            for d in days:
                dow = 1.15 if d.weekday() >= 5 else 1.0
                mult = dow * cat.festival_mult(d, p.sku) * cat.weather_mult(cal[d].weather, p.sku)
                qty = int(round(rate * mult * float(rng.lognormal(0, 0.25))))
                if qty <= 0:
                    continue
                rows.append(dict(
                    shop_id=s.id, product_id=p.id, date=d, qty=qty, revenue_paise=qty * p.mrp_paise,
                    upi_txn_count=max(1, int(round(qty / 1.8))),
                ))
    return rows


def _make_inventory(shops: list[Shop], products: list[Product], today: date,
                    rng: np.random.Generator) -> list[InventoryBatch]:
    """1-3 batches per shop x product, plus the planted demo scenarios for Sunita Ji."""
    sunita = shops[0]
    atta_low = {sunita.id} | {int(x) for x in rng.choice([s.id for s in shops[1:]], size=min(15, len(shops) - 1), replace=False)}
    paneer_donors = {shops[1].id, shops[2].id}
    batches: list[InventoryBatch] = []

    def add(s: Shop, p: Product, qty: int, received: date, expiry: date) -> None:
        if qty > 0:
            batches.append(InventoryBatch(shop_id=s.id, product_id=p.id, qty=qty,
                                          cost_price_paise=p.solo_cost_paise,
                                          expiry_date=expiry, received_date=received))

    for s in shops:
        for p in products:
            rate = cat.daily_rate(p.sku, s.size, s.shop_type)
            shelf = p.shelf_life_days
            # --- planted scenarios -------------------------------------------------
            if s.id == sunita.id and p.sku == "glucose_biscuits":
                add(s, p, 40, today - timedelta(days=shelf - 3), today + timedelta(days=3))
                continue
            if s.id == sunita.id and p.sku == "paneer_200g":
                add(s, p, 3, today - timedelta(days=1), today + timedelta(days=3))
                continue
            if s.id in paneer_donors and p.sku == "paneer_200g":
                add(s, p, int(round(rate * 12)), today - timedelta(days=1), today + timedelta(days=3))
                continue
            # --- normal stock ------------------------------------------------------
            if p.sku == "atta_5kg" and s.id in atta_low:
                total = max(1, int(round(rate * float(rng.uniform(0.2, 0.6)))))
            else:
                total = max(2, int(round(rate * float(rng.uniform(2.0, 4.5)))))
            n_b = int(rng.integers(1, 4)) if total >= 3 else 1
            cuts = np.sort(rng.integers(0, total + 1, size=n_b - 1)) if n_b > 1 else np.array([], dtype=int)
            parts = np.diff(np.concatenate([[0], cuts, [total]])).tolist()
            for q in parts:
                received = today - timedelta(days=int(rng.integers(0, max(1, min(shelf - 1, 10)) + 1)))
                expiry = received + timedelta(days=shelf)
                if expiry <= today:
                    expiry = today + timedelta(days=int(rng.integers(1, max(2, shelf // 3) + 1)))
                add(s, p, int(q), received, expiry)
    return batches


def generate(n_shops: int = 30, n_days: int = 90, seed: int = 42, reset: bool = True) -> dict:
    """Populate the database and return a summary dict."""
    t0 = time.time()
    rng = np.random.default_rng(seed)
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    today = settings.sim_start_date
    start = today - timedelta(days=n_days - 1)
    end = today + timedelta(days=14)

    with SessionLocal() as db:
        shops = _make_shops(n_shops, rng)
        db.add_all(shops)

        products = [Product(
            sku=p.sku, name_en=p.name_en, name_hi=p.name_hi, category=p.category, unit=p.unit,
            mrp_paise=cat.to_paise(p.mrp), solo_cost_paise=cat.to_paise(p.solo_cost),
            shelf_life_days=p.shelf_life_days, perishable=p.perishable,
        ) for p in cat.PRODUCTS]
        db.add_all(products)
        db.flush()
        for p, spec in zip(products, cat.PRODUCTS):
            for q, price in cat.tier_prices_paise(spec.solo_cost):
                db.add(PriceTier(product_id=p.id, min_qty=q, unit_price_paise=price))

        cal_rows = _make_calendar(start, end, rng)
        db.add_all(cal_rows)
        cal = {c.date: c for c in cal_rows}

        history = [start + timedelta(days=i) for i in range(n_days)]
        sale_rows = _make_sales(shops, products, history, cal, rng)
        db.bulk_insert_mappings(Sale, sale_rows)

        batches = _make_inventory(shops, products, today, rng)
        db.add_all(batches)

        now = datetime.utcnow()
        for s in shops:
            for scope in ("share_aggregates", "join_pools"):
                db.add(ConsentLog(shop_id=s.id, scope=scope, granted_at=now))
        db.add(SimState(id=1, current_date=today, night_ran_for=None))
        db.commit()

        summary = _summarize(db, today)
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


def _summarize(db: Session, today: date) -> dict:
    """Count rows and verify the three planted scenarios."""
    count = lambda m: db.scalar(select(func.count()).select_from(m))  # noqa: E731
    shops = {s.id: s for s in db.scalars(select(Shop))}
    prod = {p.sku: p for p in db.scalars(select(Product))}
    sunita = shops[1]

    biscuit = db.scalar(select(func.sum(InventoryBatch.qty)).where(
        InventoryBatch.shop_id == 1, InventoryBatch.product_id == prod["glucose_biscuits"].id,
        InventoryBatch.expiry_date == today + timedelta(days=3)))

    def on_hand(shop_id: int, sku: str) -> int:
        return db.scalar(select(func.coalesce(func.sum(InventoryBatch.qty), 0)).where(
            InventoryBatch.shop_id == shop_id, InventoryBatch.product_id == prod[sku].id)) or 0

    sun_paneer = on_hand(1, "paneer_200g")
    donors = [s for s in shops.values() if s.id != 1
              and haversine_m(sunita.lat, sunita.lng, s.lat, s.lng) <= 500
              and on_hand(s.id, "paneer_200g") >= 40]
    atta_need = sum(1 for s in shops.values()
                    if on_hand(s.id, "atta_5kg") < 1.5 * cat.daily_rate("atta_5kg", s.size, s.shop_type))
    return {
        "shops": count(Shop), "products": count(Product), "sales_rows": count(Sale),
        "days": db.scalar(select(func.count(func.distinct(Sale.date)))),
        "batches": count(InventoryBatch),
        "scenario_biscuits_40_expiring_3d": biscuit == 40,
        "scenario_paneer_low_with_donors": f"sunita_paneer={sun_paneer}, donors<=500m={len(donors)}",
        "scenario_paneer_ok": sun_paneer <= 6 and len(donors) >= 2,
        "scenario_atta_shops_needing_restock": atta_need,
        "scenario_atta_ok": atta_need >= 12,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the synthetic Mohalla dataset")
    ap.add_argument("--shops", type=int, default=30)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    summary = generate(a.shops, a.days, a.seed, a.reset)
    print("=== Mohalla Mind data generated ===")
    for k, v in summary.items():
        print(f"{k:42s} {v}")


if __name__ == "__main__":
    main()
