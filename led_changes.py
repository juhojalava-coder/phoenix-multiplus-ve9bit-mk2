"""Lists all changes of the LED state (Winmon 0x02 -> 0x81) from a log + Udc/Idc/IMains at that moment.

Usage: python led_changes.py <log>
Known bits (low byte): 4 inverter, 5 mains on, 6 bulk. The others are marked ?bN.
Purpose: to identify the absorption/float/alarm bits when the charge stages change.
"""
import sys
import mk2_decode as d

KNOWN = {0: "overload", 1: "low battery", 2: "temperature",  # emulator + Digital Multi Control view
         4: "inverter", 5: "mains", 6: "bulk", 7: "absorption", 8: "float"}  # 16-bit word lo|hi<<8
ALWAYS_ON = {15}
RAM = {4: "Udc", 5: "Idc", 1: "IMains", 0: "UMains"}
SIGNED = {5}

log = d.read_log(sys.argv[1])
tx = d.frames([(t, b) for t, dr, b in log if dr == "TX"])
rx = d.frames([(t, b) for t, dr, b in log if dr == "RX"])
ev = sorted([(t, "TX", f) for t, f in tx] + [(t, "RX", f) for t, f in rx], key=lambda e: e[0])

pend, cur, last_led, n = {}, {}, None, 0
for t, dr, f in ev:
    if f[1] != 0xFF or f[2] not in (0x57, 0x58, 0x59, 0x5A):
        continue
    p = f[3:-1]
    if dr == "TX":
        pend[f[2]] = p
        continue
    q = pend.get(f[2], b"")
    for i in range(0, len(p) - 2, 3):
        rc, v = p[i], p[i + 1] | p[i + 2] << 8
        if rc == 0x85 and q[:1] == b"\x30" and len(q) > 1 and q[1] in RAM:
            x = d.s16(v) if q[1] in SIGNED else v
            cur[RAM[q[1]]] = x / 100
        elif rc == 0x81 and v != last_led:
            lo, hi = p[i + 1], p[i + 2]
            names = [KNOWN.get(b, f"?b{b}") for b in range(16) if v >> b & 1 and b not in ALWAYS_ON]
            meas = "  ".join(f"{k}={cur[k]:.2f}" for k in ("Udc", "Idc", "IMains", "UMains") if k in cur)
            print(f"{t:9.1f}  lo={lo:08b} hi={hi:08b}  [{', '.join(names) or '-'}]   {meas}")
            last_led = v
            n += 1
print(f"\n{n} LED-muutosta.")
