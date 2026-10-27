"""Evaluate Pulse (baseline vs LightGBM) on the last 14 days; optionally save forecasts.

Run: python -m app.engines.evaluate_pulse [--save] [--holdout 14]
"""
import argparse
from datetime import timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.engines import pulse
from app.models import Forecast, Product, Sale


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", type=int, default=14)
    ap.add_argument("--save", action="store_true", help="train on all history and save 7-day forecasts")
    a = ap.parse_args()

    with SessionLocal() as db:
        panel = pulse.load_panel(db)
        info = pulse.load_day_info(db, panel.dates)
        res = pulse.evaluate(panel, info, a.holdout)
        print(f"=== Mohalla Pulse evaluation: {res['holdout_from']} .. {res['holdout_to']} "
              f"({res['points']} shop-product-days) ===")
        print(f"WAPE baseline_v1 : {res['wape_baseline']:.1%}")
        print(f"WAPE lgbm_v2     : {res['wape_lgbm']:.1%}")
        if "festival_window_wape_baseline" in res:
            print(f"  in pre-Diwali window  baseline {res['festival_window_wape_baseline']:.1%}"
                  f" | lgbm {res['festival_window_wape_lgbm']:.1%}")
        if a.save:
            out = pulse.run_pulse(db)
            print(f"\nSaved {out['rows']} forecast rows for {out['dates'][0]} .. {out['dates'][-1]}")
            tomorrow = panel.dates[-1] + timedelta(days=1)
            print(f"\nDiwali uplift (all shops, {tomorrow} forecast vs last-7-day actual average):")
            for sku in ("sweets_box_500g", "dry_fruits_250g", "paneer_200g", "desi_ghee_500ml", "sugar_1kg"):
                pid = db.scalar(select(Product.id).where(Product.sku == sku))
                pred = db.scalar(select(func.sum(Forecast.qty_pred)).where(
                    Forecast.product_id == pid, Forecast.date == tomorrow,
                    Forecast.model_version == pulse.LGBM_VERSION))
                base = db.scalar(select(func.sum(Forecast.qty_pred)).where(
                    Forecast.product_id == pid, Forecast.date == tomorrow,
                    Forecast.model_version == pulse.BASELINE_VERSION))
                recent = db.scalar(select(func.sum(Sale.qty)).where(
                    Sale.product_id == pid, Sale.date > panel.dates[-1] - timedelta(days=7))) / 7
                print(f"  {sku:18s} recent {recent:7.1f}/day  lgbm {pred:7.1f} ({pred / recent:.1f}x)"
                      f"  baseline {base:7.1f} ({base / recent:.1f}x)")


if __name__ == "__main__":
    main()
