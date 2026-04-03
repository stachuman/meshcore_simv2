"""SRTM elevation data and terrain profile sampling."""

import math

import srtm


def get_elevation_data(cache_dir: str | None = None) -> srtm.data.GeoElevationData:
    """Initialize SRTM data source. Tiles auto-cached to ~/.cache/srtm/."""
    if cache_dir:
        return srtm.get_data(local_cache_dir=cache_dir)
    return srtm.get_data()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (WGS-84 sphere approximation)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(min(1.0, math.sqrt(a)))


def intermediate_point(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    fraction: float,
) -> tuple[float, float]:
    """Compute intermediate point on great-circle path at given fraction [0,1].

    Uses spherical interpolation formula.
    """
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    lat2_r = math.radians(lat2)
    lon2_r = math.radians(lon2)

    d = 2 * math.asin(math.sqrt(
        math.sin((lat2_r - lat1_r) / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r)
        * math.sin((lon2_r - lon1_r) / 2) ** 2
    ))

    if d < 1e-12:
        return lat1, lon1

    a = math.sin((1 - fraction) * d) / math.sin(d)
    b = math.sin(fraction * d) / math.sin(d)

    x = a * math.cos(lat1_r) * math.cos(lon1_r) + b * math.cos(lat2_r) * math.cos(lon2_r)
    y = a * math.cos(lat1_r) * math.sin(lon1_r) + b * math.cos(lat2_r) * math.sin(lon2_r)
    z = a * math.sin(lat1_r) + b * math.sin(lat2_r)

    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon


def sample_elevation_profile(
    elev_data: srtm.data.GeoElevationData,
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    num_points: int = 150,
) -> list[float]:
    """Sample terrain elevation along a great-circle path.

    Returns list of elevation values in meters, uniformly spaced.
    Handles None returns from SRTM (ocean/missing tiles) by using 0m.
    """
    profile = []
    for i in range(num_points):
        frac = i / max(1, num_points - 1)
        lat, lon = intermediate_point(lat1, lon1, lat2, lon2, frac)
        elev = elev_data.get_elevation(lat, lon)
        if elev is None:
            elev = 0.0  # ocean or missing tile → sea level
        profile.append(float(elev))
    return profile
