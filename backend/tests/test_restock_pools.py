"""Restock and Saath Kharido tests on tiny hand-made datasets."""
from datetime import date, timedelta

import numpy as np

from app.engines import restock as rs
from app.engines import saath_kharido as sk

TODAY = date(2026, 11, 7)
ATTA_TIERS = [(1, 24000), (50, 22600), (150, 22100)]  # paise: Rs240 / 226 / 221


# ------------------------------------------------------------------ restock
def test_safety_stock_zero_for_constant_sales() -> None:
    assert rs.safety_stock([5.0] * 28) == 0.0


def test_safety_stock_formula() -> None:
    sales = [0.0, 10.0] * 14
    expected = 1.65 * np.std(sales, ddof=1) * np.sqrt(2)
    assert np.isclose(rs.safety_stock(sales), expected)


def test_reorder_qty_covers_lead_time_plus_one_day() -> None:
    # 3 days x 10 = 30 target, no variance; 12 usable -> reorder 18
    assert rs.reorder_qty([10, 10, 10, 10], [10.0] * 28, usable_on_hand=12) == 18


def test_reorder_qty_never_negative() -> None:
    assert rs.reorder_qty([1, 1, 1], [1.0] * 28, usable_on_hand=500) == 0


def test_usable_excludes_stock_that_expires_unsold() -> None:
    # 50 units expiring in 1 day, demand 10/day: only ~10 can sell
    assert rs.usable_units([(50, TODAY + timedelta(days=1))], TODAY, [10.0] * 7) == 10


def test_usable_fefo_across_batches() -> None:
    batches = [(30, TODAY + timedelta(days=5)), (20, TODAY + timedelta(days=1))]
    # soon batch absorbs 10; later batch can sell 5 days*10 - 10 = 40 >= 30
    assert rs.usable_units(batches, TODAY, [10.0] * 7) == 40


def test_usable_ignores_already_expired() -> None:
    assert rs.usable_units([(9, TODAY)], TODAY, [10.0] * 7) == 0


def test_long_life_stock_is_fully_usable() -> None:
    assert rs.usable_units([(25, TODAY + timedelta(days=90))], TODAY, [10.0] * 7) == 25


# ------------------------------------------------------------------ pooling
def test_tier_price_boundaries() -> None:
    assert sk.tier_price(ATTA_TIERS, 49) == 24000
    assert sk.tier_price(ATTA_TIERS, 50) == 22600
    assert sk.tier_price(ATTA_TIERS, 150) == 22100


def test_twelve_shops_need_atta_form_one_pool() -> None:
    needs = [(shop, 1, 10) for shop in range(1, 13)]  # 12 shops x 10 bags = 120 -> tier 2
    (plan,) = sk.plan_pools(needs, {1: ATTA_TIERS})
    assert len(plan.members) == 12 and plan.total_qty == 120
    assert plan.tier_price_paise == 22600 and plan.solo_price_paise == 24000
    assert all(m.savings_paise == 10 * 1400 for m in plan.members)  # Rs140 each
    assert plan.savings_paise == 12 * 14000
    assert round(plan.saving_pct, 3) == 0.058


def test_pool_needs_at_least_three_shops() -> None:
    assert sk.plan_pools([(1, 1, 80), (2, 1, 80)], {1: ATTA_TIERS}) == []


def test_pool_needs_a_tier_discount() -> None:
    needs = [(s, 1, 10) for s in range(1, 5)]  # 40 bags: still tier 1, no saving
    assert sk.plan_pools(needs, {1: ATTA_TIERS}) == []


def test_pool_needs_three_percent_saving() -> None:
    tiers = [(1, 10000), (50, 9800)]  # only 2% off
    assert sk.plan_pools([(s, 1, 20) for s in range(1, 6)], {1: tiers}) == []


def test_bigger_pool_unlocks_higher_tier() -> None:
    (plan,) = sk.plan_pools([(s, 1, 14) for s in range(1, 13)], {1: ATTA_TIERS})  # 168 bags
    assert plan.tier_price_paise == 22100


def test_products_are_pooled_independently() -> None:
    needs = [(s, 1, 10) for s in range(1, 13)] + [(1, 2, 5), (2, 2, 5)]
    plans = sk.plan_pools(needs, {1: ATTA_TIERS, 2: ATTA_TIERS})
    assert [p.product_id for p in plans] == [1]


def test_reprice_drops_a_tier_when_members_decline() -> None:
    (plan,) = sk.plan_pools([(s, 1, 14) for s in range(1, 13)], {1: ATTA_TIERS})  # 168 -> tier 3
    smaller = sk.reprice_pool(plan, list(range(1, 11)), ATTA_TIERS)  # 10 approve: 140 -> tier 2
    assert smaller is not None and smaller.tier_price_paise == 22600
    assert all(m.savings_paise == 14 * 1400 for m in smaller.members)


def test_reprice_cancels_pool_if_too_few_approve() -> None:
    (plan,) = sk.plan_pools([(s, 1, 14) for s in range(1, 13)], {1: ATTA_TIERS})
    assert sk.reprice_pool(plan, [1, 2], ATTA_TIERS) is None  # only 2 shops
    assert sk.reprice_pool(plan, [1, 2, 3], ATTA_TIERS) is None  # 42 bags: below tier 2


def test_money_is_integer_paise() -> None:
    (plan,) = sk.plan_pools([(s, 1, 10) for s in range(1, 13)], {1: ATTA_TIERS})
    assert all(isinstance(m.savings_paise, int) for m in plan.members)
    assert isinstance(plan.tier_price_paise, int)
