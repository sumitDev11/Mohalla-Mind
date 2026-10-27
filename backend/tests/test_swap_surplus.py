"""Stock swap and surplus-deal tests on tiny hand-made datasets."""
from datetime import date, timedelta

from app.engines import restock as rs
from app.engines import stock_swap as ss
from app.engines import surplus as su

TODAY = date(2026, 11, 7)
LAT, LNG = 28.5677, 77.2433
DEG_PER_100M = 0.0009  # ~100 m of latitude


def at(metres: int) -> tuple[float, float]:
    """A point `metres` north of the origin."""
    return LAT + DEG_PER_100M * metres / 100, LNG


def shortage(shop: int, qty: int, metres: int = 0, product: int = 1) -> ss.Shortage:
    return ss.Shortage(shop, product, qty, *at(metres))


def surplus(shop: int, qty: int, metres: int, product: int = 1, cost: int = 7800) -> ss.Surplus:
    return ss.Surplus(shop, product, qty, *at(metres), cost)


# ------------------------------------------------------------------ surplus / shortage formulas
def test_surplus_after_demand_and_safety() -> None:
    # 100 units, 3 days to expiry, 10/day, safety 5 -> 100 - 30 - 5 = 65
    assert ss.surplus_qty(100, [10.0] * 7, 5.0, days_to_expiry=3) == 65


def test_surplus_horizon_capped_at_seven_days() -> None:
    assert ss.surplus_qty(100, [10.0] * 7, 0.0, days_to_expiry=90) == 30  # 100 - 7*10


def test_no_surplus_when_stock_is_tight() -> None:
    assert ss.surplus_qty(20, [10.0] * 7, 5.0, days_to_expiry=3) == 0


def test_shortage_covers_tomorrow_plus_safety() -> None:
    assert ss.shortage_qty(3, [10.0, 10.0], 4.0) == 11  # 10 + 4 - 3
    assert ss.shortage_qty(30, [10.0], 4.0) == 0


# ------------------------------------------------------------------ matching
def test_nearest_donor_within_range_is_chosen() -> None:
    plans = ss.match_swaps([shortage(1, 10)], [surplus(2, 50, 450), surplus(3, 50, 300), surplus(4, 50, 800)])
    assert len(plans) == 1
    assert plans[0].from_shop_id == 3 and plans[0].to_shop_id == 1
    assert 295 <= plans[0].distance_m <= 305


def test_donor_beyond_one_km_is_ignored() -> None:
    assert ss.match_swaps([shortage(1, 10)], [surplus(2, 50, 1200)]) == []


def test_transfer_price_is_cost_plus_handling() -> None:
    (plan,) = ss.match_swaps([shortage(1, 10)], [surplus(2, 50, 300, cost=7800)])
    assert plan.transfer_price_paise == 7800 + ss.HANDLING_PAISE
    (plan,) = ss.match_swaps([shortage(1, 10)], [surplus(2, 50, 300, cost=7800)], handling_paise=0)
    assert plan.transfer_price_paise == 7800


def test_largest_shortage_is_served_first_from_limited_surplus() -> None:
    plans = ss.match_swaps([shortage(1, 5, 0), shortage(2, 20, 100)], [surplus(9, 22, 50)])
    assert plans[0].to_shop_id == 2 and plans[0].qty == 20
    assert all(p.to_shop_id != 1 or p.qty >= ss.MIN_SWAP_QTY for p in plans)
    assert sum(p.qty for p in plans) <= 22  # never more than the donor has


def test_shortage_can_be_filled_by_several_donors() -> None:
    plans = ss.match_swaps([shortage(1, 30)], [surplus(2, 12, 200), surplus(3, 25, 400)])
    assert [(p.from_shop_id, p.qty) for p in plans] == [(2, 12), (3, 18)]


def test_swaps_only_match_the_same_product() -> None:
    assert ss.match_swaps([shortage(1, 10, product=1)], [surplus(2, 50, 100, product=2)]) == []


def test_tiny_leftovers_are_not_swapped() -> None:
    plans = ss.match_swaps([shortage(1, 10)], [surplus(2, 1, 100)])
    assert plans == []


def test_swap_flows() -> None:
    created = [ss.CreatedSwap(1, ss.SwapPlan(2, 1, 5, 12, 300, 7800), 1, 2)]
    out, inc = ss.swap_flows(created)
    assert out == {(2, 5): 12} and inc == {(1, 5): 12}


# ------------------------------------------------------------------ deals
def test_discount_ladder_by_days_to_expiry() -> None:
    kw = dict(excess=20, mrp_paise=10000, cost_paise=5000)
    assert su.plan_deal(days_to_expiry=3, **kw).discount_pct == 10
    assert su.plan_deal(days_to_expiry=2, **kw).discount_pct == 20
    assert su.plan_deal(days_to_expiry=1, **kw).discount_pct == 30


def test_biscuit_deal_is_ten_percent_three_days_out() -> None:
    plan = su.plan_deal(22, 3, mrp_paise=1000, cost_paise=820)
    assert plan.discount_pct == 10 and plan.deal_price_paise == 900 and plan.qty == 22


def test_price_never_below_cost_before_the_final_day() -> None:
    plan = su.plan_deal(20, 2, mrp_paise=1000, cost_paise=820)  # 20% would be 800 < cost
    assert plan.deal_price_paise == 820 and plan.discount_pct == 18


def test_final_day_may_sell_below_cost() -> None:
    plan = su.plan_deal(20, 1, mrp_paise=1000, cost_paise=820)
    assert plan.deal_price_paise == 700 and plan.discount_pct == 30


def test_no_deal_outside_window_or_for_tiny_excess() -> None:
    assert su.plan_deal(20, 4, 1000, 820) is None
    assert su.plan_deal(20, 0, 1000, 820) is None
    assert su.plan_deal(su.MIN_DEAL_QTY - 1, 2, 1000, 820) is None


def test_no_deal_when_cost_leaves_no_room_to_discount() -> None:
    assert su.plan_deal(20, 3, mrp_paise=1000, cost_paise=1000) is None


def test_swapped_units_come_off_the_excess_first() -> None:
    assert su.net_excess_after_swaps([22], 0) == [22]
    assert su.net_excess_after_swaps([22], 10) == [12]
    assert su.net_excess_after_swaps([6, 20], 10) == [0, 16]  # soonest expiry absorbs the swap first


def test_excess_is_stock_that_will_not_sell_before_expiry() -> None:
    batches = [(40, TODAY + timedelta(days=3))]
    (usable,) = rs.usable_by_batch(batches, TODAY, [6.0] * 7)  # sells 18 in 3 days
    assert 40 - usable == 22


def test_usable_by_batch_is_aligned_with_input_order() -> None:
    batches = [(30, TODAY + timedelta(days=5)), (20, TODAY + timedelta(days=1))]
    assert rs.usable_by_batch(batches, TODAY, [10.0] * 7) == [30, 10]
