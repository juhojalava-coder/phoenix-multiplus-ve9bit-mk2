"""Calibration of the knob (Winmon 0x04) from a log: knob value -> real IMains.

Usage: python calibrate_knob.py <log> [<log> ...]   -> prints a table, writes knob_calibration.json

Method:
  - every period during which the knob value stays the same >= MIN_HOLD_S
  - the first SETTLE_S seconds are skipped (the charger settling)
  - the median of the IMains samples = the point; also the medians of Idc and UMains
  - periods in which the charger is not running (IMains < 0.3 A) or Idc saturates at the IBatBulk limit are rejected
  - several periods with the same knob value are combined (samples together)
"""
import json
import statistics
import sys
import time

import mk2_decode as d

MIN_HOLD_S = 8.0
SETTLE_S = 3.0
MIN_SAMPLES = 2
IBATBULK_A = 52.0   # Charge current setting (original); saturated periods are rejected
SAT_MARGIN = 0.95


def segments(path):
    log = d.read_log(path)
    tx = d.frames([(t, b) for t, dr, b in log if dr == "TX"])
    rx = d.frames([(t, b) for t, dr, b in log if dr == "RX"])
    ev = sorted([(t, "TX", f) for t, f in tx] + [(t, "RX", f) for t, f in rx], key=lambda e: e[0])

    knob_changes = []          # (t, value) when the value changes
    samples = {1: [], 5: [], 0: []}  # ram id -> [(t, value)]
    pend, last = {}, None
    for t, dr, f in ev:
        if f[1] != 0xFF or f[2] not in (0x58, 0x59, 0x5A):
            continue
        p = f[3:-1]
        if dr == "TX":
            pend[f[2]] = p
            if len(p) >= 2 and p[0] == 0x04 and p[1] != last:
                knob_changes.append((t, p[1]))
                last = p[1]
            continue
        q = pend.get(f[2], b"")
        for i in range(0, len(p) - 2, 3):
            rc, v = p[i], p[i + 1] | p[i + 2] << 8
            if rc == 0x85 and q[:1] == b"\x30" and len(q) > 1 and q[1] in samples:
                x = d.s16(v) if q[1] == 5 else v
                samples[q[1]].append((t, x / 100))
    t_end = ev[-1][0] if ev else 0
    out = []
    for i, (t0, val) in enumerate(knob_changes):
        t1 = knob_changes[i + 1][0] if i + 1 < len(knob_changes) else t_end
        if t1 - t0 < MIN_HOLD_S:
            continue

        def win(rid):
            return [x for t, x in samples[rid] if t0 + SETTLE_S <= t <= t1]
        out.append({"knob": val, "t0": t0, "t1": t1, "imains": win(1), "idc": win(5), "umains": win(0)})
    return out


def calibrate(paths):
    by_knob = {}
    for path in paths:
        for s in segments(path):
            if len(s["imains"]) < MIN_SAMPLES:
                continue
            im = statistics.median(s["imains"])
            idc = statistics.median(s["idc"]) if s["idc"] else None
            if im < 0.3:
                continue  # the charger was not running (e.g. switch off / no mains)
            if idc is not None and idc >= SAT_MARGIN * IBATBULK_A:
                continue  # IBatBulk limits, not the knob
            agg = by_knob.setdefault(s["knob"], {"imains": [], "idc": [], "umains": [], "dur": 0.0})
            agg["imains"] += s["imains"]
            agg["idc"] += s["idc"]
            agg["umains"] += s["umains"]
            agg["dur"] += s["t1"] - s["t0"]
    points = []
    for knob in sorted(by_knob):
        a = by_knob[knob]
        points.append({
            "knob": knob,
            "imains": round(statistics.median(a["imains"]), 3),
            "idc": round(statistics.median(a["idc"]), 2) if a["idc"] else None,
            "umains": round(statistics.median(a["umains"]), 1) if a["umains"] else None,
            "n": len(a["imains"]),
            "hold_s": round(a["dur"], 1),
        })
    return points


if __name__ == "__main__":
    paths = sys.argv[1:]
    pts = calibrate(paths)
    print(f"{'knob':>6} {'IMains':>8} {'ratio':>6} {'Idc':>7} {'UMains':>7} {'n':>4} {'time s':>8}")
    for p in pts:
        print(f"{p['knob'] / 10:6.1f} {p['imains']:8.2f} {p['imains'] / (p['knob'] / 10):6.3f} "
              f"{p['idc'] if p['idc'] is not None else '-':>7} {p['umains'] if p['umains'] is not None else '-':>7} "
              f"{p['n']:4} {p['hold_s']:8.1f}")
    with open("knob_calibration.json", "w", encoding="utf-8") as f:
        json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "sources": paths,
                   "method": f"median IMains, hold>={MIN_HOLD_S}s, settle {SETTLE_S}s",
                   "points": pts}, f, indent=1)
    print(f"\n{len(pts)} points -> knob_calibration.json")
