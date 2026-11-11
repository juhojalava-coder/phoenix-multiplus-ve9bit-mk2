"""Timeline around a switch turn: switch/knob commands, LED state and measurements.

Usage: python switch_timeline.py <log> [state=1] [n=last]
  state: 1=charger only, 3=on, 4=off. Selects the last time the switch changed to this state.
"""
import sys
import mk2_decode as d

path = sys.argv[1]
state = int(sys.argv[2]) if len(sys.argv) > 2 else 1
NAMES = {0: "UMains", 1: "IMains", 2: "UOut", 4: "Udc", 5: "Idc", 8: "PerIn", 9: "IOut"}
SIGNED = {5, 9}

log = d.read_log(path)
tx = d.frames([(t, b) for t, dr, b in log if dr == "TX"])
rx = d.frames([(t, b) for t, dr, b in log if dr == "RX"])
ev = sorted([(t, "TX", f) for t, f in tx] + [(t, "RX", f) for t, f in rx], key=lambda e: e[0])

# switch state changes (repeats removed)
changes, last = [], None
for t, dr, f in ev:
    if dr == "TX" and f[1] == 0xFF and f[2] in (0x58, 0x59, 0x5A) and len(f) > 5 and f[3] == 0x03:
        if f[4] != last:
            changes.append((t, f[4]))
            last = f[4]
hits = [t for t, s in changes if s == state]
if not hits:
    sys.exit(f"the switch did not change to state {state}")
T0 = hits[-1]
print(f"switch -> {state} at t={T0:.2f}  (all changes: {[(round(t, 1), s) for t, s in changes[-6:]]})")

pend = {}
for t, dr, f in ev:
    if t < T0 - 4 or t > T0 + 15:
        continue
    if f == d.RESET_FRAME:
        print(f"{t - T0:+7.2f}  RESET MULTI")
        continue
    if f[1] != 0xFF or f[2] not in (0x58, 0x59, 0x5A):
        continue
    p = f[3:-1]
    if dr == "TX":
        pend[f[2]] = p
        if p[:1] in (b"\x03", b"\x04"):
            print(f"{t - T0:+7.2f}  TX {'switch' if p[0] == 3 else 'knob  '} {p[1:].hex(' ')}")
        continue
    q = pend.get(f[2], b"")
    for i in range(0, len(p) - 2, 3):
        rc, v = p[i], p[i + 1] | p[i + 2] << 8
        if rc == 0x81:
            print(f"{t - T0:+7.2f}  LED 0x{v:04X}  lo={p[i + 1]:08b}")
        elif rc == 0x85 and q[:1] == b"\x30" and len(q) > 1 and q[1] in NAMES:
            rid = q[1]
            x = d.s16(v) if rid in SIGNED else v
            print(f"{t - T0:+7.2f}  {NAMES[rid]:6} raw={v:5}  {x / 100:8.2f}")
