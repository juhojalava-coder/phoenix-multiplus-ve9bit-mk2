"""Timeline: switch commands (0x03), LED changes (0x81) and UOut/ripple changes."""
import sys
import mk2_decode as d

SW = {1: "charger only", 3: "on", 4: "off"}
log = d.read_log(sys.argv[1])
tx = d.frames([(t, b) for t, dr, b in log if dr == "TX"])
rx = d.frames([(t, b) for t, dr, b in log if dr == "RX"])
ev = sorted([(t, "TX", f) for t, f in tx] + [(t, "RX", f) for t, f in rx], key=lambda e: e[0])

pending, last_led, last_u, last_sw = {}, None, None, None
for t, dr, f in ev:
    if f[1] != 0xFF or f[2] not in (0x57, 0x58, 0x59, 0x5A):
        continue
    p = f[3:-1]
    if dr == "TX":
        pending[f[2]] = p
        if p[:1] == b"\x03":
            tag = "repeat" if p[1] == last_sw else "CHANGE"
            if tag == "CHANGE":
                print(f"{t:8.2f}  >>> 0x03 {p[1]} = {SW.get(p[1])}   [{f.hex(' ')}]")
            last_sw = p[1]
        continue
    req = pending.get(f[2], b"")
    for i in range(0, len(p) - 2, 3):
        rc, val = p[i], p[i + 1] | p[i + 2] << 8
        if rc == 0x81 and val != last_led:
            print(f"{t:8.2f}      LED 0x{val:04X} lo={p[i + 1]:08b}  inverter={'ON' if p[i + 1] >> 4 & 1 else 'off'}")
            last_led = val
        if rc == 0x85 and req[:2] == b"\x30\x02":
            u = val / 100
            if last_u is None or abs(u - last_u) > 5:
                print(f"{t:8.2f}      UOut {u:.1f} V")
                last_u = u
