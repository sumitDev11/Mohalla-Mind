"""Product catalog, price tiers, festival and weather multipliers (CLAUDE.md Sections 6, 7.1)."""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name_en: str
    name_hi: str
    category: str
    unit: str
    mrp: float  # rupees (converted to paise at insert time)
    solo_cost: float  # rupees a single small shop pays (tier 1)
    shelf_life_days: int
    perishable: bool
    base_rate: float  # units/day for a size-M kirana


PRODUCTS: list[ProductSpec] = [
    ProductSpec("atta_5kg", "Atta 5kg", "आटा 5 किग्रा", "staples", "bag", 270, 240, 90, False, 3),
    ProductSpec("rice_5kg", "Rice 5kg", "चावल 5 किग्रा", "staples", "bag", 420, 380, 180, False, 1.5),
    ProductSpec("toor_dal_1kg", "Toor Dal 1kg", "अरहर दाल 1 किग्रा", "staples", "pack", 190, 165, 180, False, 2),
    ProductSpec("chana_dal_1kg", "Chana Dal 1kg", "चना दाल 1 किग्रा", "staples", "pack", 120, 105, 180, False, 2),
    ProductSpec("besan_500g", "Besan 500g", "बेसन 500 ग्राम", "staples", "pack", 70, 60, 120, False, 2),
    ProductSpec("sugar_1kg", "Sugar 1kg", "चीनी 1 किग्रा", "staples", "pack", 50, 44, 365, False, 5),
    ProductSpec("salt_1kg", "Salt 1kg", "नमक 1 किग्रा", "staples", "pack", 28, 24, 365, False, 3),
    ProductSpec("mustard_oil_1l", "Mustard Oil 1L", "सरसों तेल 1 लीटर", "oil", "bottle", 190, 170, 180, False, 3),
    ProductSpec("refined_oil_1l", "Refined Oil 1L", "रिफाइंड तेल 1 लीटर", "oil", "bottle", 160, 142, 180, False, 3),
    ProductSpec("desi_ghee_500ml", "Desi Ghee 500ml", "देसी घी 500 मिली", "oil", "jar", 340, 300, 240, False, 1.5),
    ProductSpec("tea_250g", "Tea 250g", "चाय पत्ती 250 ग्राम", "beverage", "pack", 130, 112, 240, False, 3),
    ProductSpec("milk_500ml", "Milk 500ml", "दूध 500 मिली", "dairy", "pouch", 33, 29, 2, True, 25),
    ProductSpec("curd_400g", "Curd 400g", "दही 400 ग्राम", "dairy", "cup", 40, 34, 5, True, 8),
    ProductSpec("paneer_200g", "Paneer 200g", "पनीर 200 ग्राम", "dairy", "pack", 90, 78, 4, True, 6),
    ProductSpec("bread", "Bread", "ब्रेड", "bakery", "loaf", 45, 38, 4, True, 10),
    ProductSpec("eggs_6", "Eggs (6)", "अंडे (6)", "dairy", "tray", 60, 52, 12, True, 7),
    ProductSpec("glucose_biscuits", "Glucose Biscuits", "ग्लूकोज़ बिस्किट", "snacks", "pack", 10, 8.2, 120, False, 6),
    ProductSpec("namkeen_200g", "Namkeen 200g", "नमकीन 200 ग्राम", "snacks", "pack", 50, 42, 90, False, 4),
    ProductSpec("instant_noodles", "Instant Noodles", "इंस्टेंट नूडल्स", "snacks", "pack", 14, 11.5, 180, False, 6),
    ProductSpec("chips", "Chips", "चिप्स", "snacks", "pack", 20, 16, 90, False, 6),
    ProductSpec("cold_drink_750ml", "Cold Drink 750ml", "कोल्ड ड्रिंक 750 मिली", "beverage", "bottle", 40, 34, 150, False, 5),
    ProductSpec("dry_fruits_250g", "Dry Fruits 250g", "मेवा 250 ग्राम", "festival", "pack", 300, 260, 150, False, 1),
    ProductSpec("sweets_box_500g", "Sweets Box 500g", "मिठाई का डिब्बा 500 ग्राम", "festival", "box", 350, 300, 10, True, 1.2),
    ProductSpec("detergent_1kg", "Detergent 1kg", "डिटर्जेंट 1 किग्रा", "household", "pack", 120, 102, 365, False, 2),
]
PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}

# distributor tiers: (min_qty, discount vs solo). atta: 240 / 226 / 221.
TIER_BREAKS: list[tuple[int, float]] = [(1, 0.0), (50, 0.06), (150, 0.08)]

SIZE_MULT = {"S": 0.6, "M": 1.0, "L": 1.6}
DAIRY_SKUS = {"milk_500ml", "curd_400g", "paneer_200g", "eggs_6", "bread"}
GENERAL_SKUS = {"instant_noodles", "chips", "cold_drink_750ml", "glucose_biscuits", "namkeen_200g", "detergent_1kg"}

# Festival dates (real dates near the 2026 demo window). Effect applies on the day and 2 days before.
FESTIVALS: dict[str, list[date]] = {
    "Raksha Bandhan": [date(2026, 8, 28)],
    "Navratri": [date(2026, 10, 11) + timedelta(days=i) for i in range(9)],
    "Dussehra": [date(2026, 10, 20)],
    "Diwali": [date(2026, 11, 8)],
}
_NAVRATRI_LIKE = {"paneer_200g": 1.5, "sweets_box_500g": 2.0, "dry_fruits_250g": 1.3}
FESTIVAL_MULT: dict[str, dict[str, float]] = {
    "Diwali": {"dry_fruits_250g": 4.0, "sweets_box_500g": 5.0, "desi_ghee_500ml": 2.5,
               "paneer_200g": 1.8, "besan_500g": 2.0, "sugar_1kg": 1.6},
    "Dussehra": _NAVRATRI_LIKE,
    "Navratri": _NAVRATRI_LIKE,
    "Raksha Bandhan": {"sweets_box_500g": 3.0, "dry_fruits_250g": 2.0},
}
WEATHER_MULT: dict[str, dict[str, float]] = {
    "rain": {"instant_noodles": 1.5, "besan_500g": 1.4, "tea_250g": 1.3},
    "hot": {"cold_drink_750ml": 2.0, "curd_400g": 1.4},
    "cold": {"tea_250g": 1.3, "desi_ghee_500ml": 1.2},
    "sunny": {},
}


def to_paise(rupees: float) -> int:
    """Convert rupees to integer paise."""
    return int(round(rupees * 100))


def tier_prices_paise(solo_cost: float) -> list[tuple[int, int]]:
    """Return [(min_qty, unit_price_paise)] for a product's solo cost."""
    return [(q, to_paise(solo_cost * (1 - disc))) for q, disc in TIER_BREAKS]


def festival_on(d: date) -> str | None:
    """Name of the festival falling exactly on d, if any (Dussehra wins over Navratri)."""
    hit = [n for n, ds in FESTIVALS.items() if d in ds]
    if not hit:
        return None
    return "Dussehra" if "Dussehra" in hit else hit[0]


def festival_mult(d: date, sku: str) -> float:
    """Catalog festival multiplier for sku on d (festival day and 2 days before; max over festivals)."""
    best = 1.0
    for name, dates in FESTIVALS.items():
        if any(0 <= (f - d).days <= 2 for f in dates):
            best = max(best, FESTIVAL_MULT[name].get(sku, 1.0))
    return best


def weather_mult(weather: str, sku: str) -> float:
    """Catalog weather multiplier for sku."""
    return WEATHER_MULT.get(weather, {}).get(sku, 1.0)


def type_mult(shop_type: str, sku: str) -> float:
    """Shop-type affinity: dairy shops sell more dairy; general stores more packaged goods."""
    if shop_type == "dairy":
        return 2.0 if sku in DAIRY_SKUS else (1.2 if sku == "desi_ghee_500ml" else 0.5)
    if shop_type == "general":
        return 1.3 if sku in GENERAL_SKUS else 0.7
    return 1.0


def daily_rate(sku: str, size: str, shop_type: str) -> float:
    """Average units/day for a shop (before festival/weather/dow/noise)."""
    return PRODUCT_BY_SKU[sku].base_rate * SIZE_MULT[size] * type_mult(shop_type, sku)
