"""Geo helpers: haversine distance and radius search (no PostGIS needed for 30 shops)."""
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two lat/lng points."""
    p1, p2 = radians(lat1), radians(lat2)
    dphi = p2 - p1
    dlmb = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def shops_within(shops: list, lat: float, lng: float, radius_m: float = 1000.0) -> list[tuple[object, float]]:
    """Return (shop, distance_m) pairs within radius_m, nearest first. Shops need .lat/.lng."""
    out = [(s, haversine_m(lat, lng, s.lat, s.lng)) for s in shops]
    return sorted([(s, d) for s, d in out if d <= radius_m], key=lambda t: t[1])
