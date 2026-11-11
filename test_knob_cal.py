"""Tests for the knob calibration (knob_cal.py)."""
import knob_cal as kc

PTS = [(10, 1.52), (28, 4.87), (31, 5.63), (35, 6.39), (36, 6.70), (41, 7.46),
       (45, 8.22), (50, 9.13), (55, 10.20), (61, 11.26), (66, 12.18)]


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_points_exact():
    c = kc.KnobCal(PTS)
    for k, a in PTS:
        v, ext = c.to_amps(k)
        assert approx(v, a) and not ext, (k, v, a, ext)


def test_interpolation_between_points():
    c = kc.KnobCal(PTS)
    v, ext = c.to_amps(30)  # 2.8 -> 4.87, 3.1 -> 5.63: 2/3 of the way
    assert approx(v, 4.87 + (5.63 - 4.87) * 2 / 3) and not ext


def test_extrapolation_flagged():
    c = kc.KnobCal(PTS)
    v, ext = c.to_amps(5)       # below 1.0: from the origin to the 1.0 point
    assert ext and approx(v, 1.52 * 5 / 10)
    v, ext = c.to_amps(100)     # above 6.6: the last ratio
    assert ext and approx(v, 12.18 * 100 / 66)


def test_inverse():
    c = kc.KnobCal(PTS)
    k, a = c.to_knob(6.70)
    assert k == 36 and approx(a, 6.70)
    k, a = c.to_knob(9.0)       # nearest
    assert abs(a - 9.0) < 0.2, (k, a)


def test_monotonic_and_fallback():
    assert kc.KnobCal(PTS).monotonic()
    f = kc.KnobCal([])
    v, ext = f.to_amps(36)
    assert ext and approx(v, 3.6 * kc.FALLBACK_RATIO)


def test_file_from_log():
    c = kc.KnobCal.load()
    assert c.points, "knob_calibration.json is missing - run calibrate_knob.py"
    assert c.monotonic(), c.points
    v, _ = c.to_amps(36)
    assert abs(v - 6.70) < 0.05, v


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: knob calibration")
