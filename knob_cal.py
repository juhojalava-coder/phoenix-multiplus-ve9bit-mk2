"""Current limit knob calibration: knob raw value (0x04, 0.1 A on the panel scale) <-> actual IMains.

The points are read from knob_calibration.json (calibrate_knob.py creates it from a log).
The conversion is piecewise linear between the measured points:
  - below the first point: a line from the origin to the first point (EXTRAPOLATED)
  - above the last point: the ratio of the last point (A / knob A) (EXTRAPOLATED)
If the file does not exist, the linear ratio FALLBACK_RATIO is used (also extrapolated).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "knob_calibration.json")
FALLBACK_RATIO = 1.85  # measured average for knob >= 5.0 (30.10.2026)


class KnobCal:
    def __init__(self, points=None, source="fallback"):
        # points: [(knob_raw, imains_A)], ascending
        self.points = sorted(points or [])
        self.source = source

    @classmethod
    def load(cls, path=DEFAULT_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            pts = [(p["knob"], p["imains"]) for p in data["points"]]
            return cls(pts, source=f"{os.path.basename(path)} ({len(pts)} points, {data.get('created', '?')})")
        except (OSError, KeyError, ValueError):
            return cls([], source=f"no calibration, linear x{FALLBACK_RATIO}")

    def to_amps(self, knob_raw):
        """Knob raw -> (actual A, extrapolated?)"""
        p = self.points
        if not p:
            return knob_raw / 10 * FALLBACK_RATIO, True
        if knob_raw <= p[0][0]:
            k0, a0 = p[0]
            return a0 * knob_raw / k0 if k0 else 0.0, knob_raw != k0
        if knob_raw >= p[-1][0]:
            k1, a1 = p[-1]
            return a1 * knob_raw / k1, knob_raw != k1
        for (k0, a0), (k1, a1) in zip(p, p[1:]):
            if k0 <= knob_raw <= k1:
                return a0 + (a1 - a0) * (knob_raw - k0) / (k1 - k0), False
        raise AssertionError("unreachable")

    def to_knob(self, amps):
        """Desired actual A -> nearest knob raw value (integer) and its actual A."""
        best = min(range(0, 256), key=lambda k: abs(self.to_amps(k)[0] - amps))
        return best, self.to_amps(best)[0]

    def monotonic(self):
        a = [x for _, x in self.points]
        return all(y > x for x, y in zip(a, a[1:]))
