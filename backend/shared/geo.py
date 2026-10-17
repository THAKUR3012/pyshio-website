"""Distances between map points (patients' homes, clinics, pharmacies)."""
import math


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float):
    """Cheap pre-filter for SQL; exact distance is checked afterwards."""
    dlat = math.degrees(radius_km / EARTH_RADIUS_KM)
    dlng = math.degrees(radius_km / (EARTH_RADIUS_KM * max(math.cos(math.radians(lat)), 0.01)))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def within(rows, lat: float, lng: float, radius_km: float, lat_key="lat", lng_key="lng"):
    """[(distance_km, row)] for rows inside the radius, nearest first."""
    found = []
    for r in rows:
        if r[lat_key] is None or r[lng_key] is None:
            continue
        d = haversine_km(lat, lng, r[lat_key], r[lng_key])
        if d <= radius_km:
            found.append((round(d, 2), r))
    return sorted(found, key=lambda x: x[0])


def check_point(lat, lng) -> None:
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Please pick a valid location on the map")
