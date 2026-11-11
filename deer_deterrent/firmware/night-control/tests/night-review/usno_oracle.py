"""Independent approximate solar oracle for review tests; not firmware code.

This uses USNO's low-order solar coordinates, sidereal time and altitude
equations, then brackets actual horizon crossings by bisection. It intentionally
does not reuse the implementation's NOAA equation-of-time event solver.

Primary equations:
  https://aa.usno.navy.mil/faq/sun_approx
  https://aa.usno.navy.mil/faq/GAST
  https://aa.usno.navy.mil/faq/alt_az
Horizon definition:
  https://aa.usno.navy.mil/faq/RST_defs

This is an approximate calculated cross-check, not measured sunrise or proof
of sub-minute physical accuracy. No third-party implementation is copied.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

DAY = 86400
RAD = math.pi / 180
HORIZON = -0.8333


def solar_altitude(epoch: float, latitude: float, longitude: float) -> float:
    jd = epoch / DAY + 2440587.5
    d_tt = jd - 2451545.0  # UT1 ~ UTC, TT ~ UT1 for this approximate check.
    jd_zero = math.floor(jd - 0.5) + 0.5
    d_zero = jd_zero - 2451545.0
    hours = (jd - jd_zero) * 24.0
    century = d_tt / 36525.0

    anomaly = ((357.529 + 0.98560028 * d_tt) % 360.0) * RAD
    mean_longitude = (280.459 + 0.98564736 * d_tt) % 360.0
    apparent_longitude = (mean_longitude + 1.915 * math.sin(anomaly)
                          + 0.020 * math.sin(2.0 * anomaly)) * RAD
    obliquity = (23.439 - 0.00000036 * d_tt) * RAD
    right_ascension = math.atan2(math.cos(obliquity) * math.sin(apparent_longitude),
                                math.cos(apparent_longitude)) / RAD
    declination = math.asin(math.sin(obliquity) * math.sin(apparent_longitude))
    gmst = (6.697375 + 0.065709824279 * d_zero + 1.0027379 * hours
            + 0.0000258 * century * century) % 24.0
    omega = (125.04 - 0.052954 * d_tt) * RAD
    mean_sun = (280.47 + 0.98565 * d_tt) * RAD
    eqeq = (-0.000319 * math.sin(omega) - 0.000024 * math.sin(2 * mean_sun)) \
        * math.cos((23.4393 - 0.0000004 * d_tt) * RAD)
    hour_angle = ((gmst + eqeq) * 15.0 + longitude - right_ascension) * RAD
    lat = latitude * RAD
    sine = (math.cos(hour_angle) * math.cos(declination) * math.cos(lat)
            + math.sin(declination) * math.sin(lat))
    return math.asin(max(-1.0, min(1.0, sine))) / RAD


def crossings(start: float, end: float, latitude: float, longitude: float):
    """Return (rising, epoch) crossings in the requested actual UTC interval."""
    result = []
    left = start
    left_value = solar_altitude(left, latitude, longitude) - HORIZON
    while left < end:
        right = min(left + 600.0, end)
        right_value = solar_altitude(right, latitude, longitude) - HORIZON
        if (left_value < 0) != (right_value < 0):
            rising = left_value < 0
            lo, hi = left, right
            for _ in range(32):
                middle = (lo + hi) / 2.0
                middle_value = solar_altitude(middle, latitude, longitude) - HORIZON
                if (middle_value < 0) == rising:
                    lo = middle
                else:
                    hi = middle
            result.append((rising, (lo + hi) / 2.0))
        left, left_value = right, right_value
    return result


def validate_published_vectors():
    vectors = json.loads((Path(__file__).parent / 'solar_reference_vectors.json').read_text())
    results = []
    for v in vectors:
        midnight = v['nominal_midnight_utc']
        events = crossings(midnight - DAY, midnight + 2 * DAY,
                           v['latitude'], v['longitude'])
        for kind, key in [(True, 'expected_sunrise_unix'),
                          (False, 'expected_sunset_unix')]:
            actual = min((t for rising, t in events if rising == kind),
                         key=lambda t: abs(t - v[key]))
            error = actual - v[key]
            results.append({'vector': v['name'], 'event': 'rise' if kind else 'set',
                            'error_seconds': round(error, 3)})
            assert abs(error) <= v['tolerance_seconds'], (v['name'], key, error)
    return results


if __name__ == '__main__':
    print(json.dumps({'published_vector_checks': validate_published_vectors()}, indent=2))
