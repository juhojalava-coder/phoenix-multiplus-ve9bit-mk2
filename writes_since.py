"""Lists the settings writes (0x33+0x34) and resets from a log after time T, quickly (no full decode).

Usage: python writes_since.py <log> [T]
Prints every write: time, setting ID, name, raw value, and whether a 0x35/0x31 read for the same ID preceded it.
"""
import sys
import mk2_decode as d

path = sys.argv[1]
t_min = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

lines = []
with open(path, encoding="utf-8") as f:
    for line in f:
        p = line.split()
        if len(p) >= 3 and p[1] in ("TX", "RX"):
            try:
                t = float(p[0])
            except ValueError:
                continue
            if t >= t_min - 5:
                lines.append((t, p[1], bytes(int(h, 16) for h in p[2:])))
tx = d.frames([(t, b) for t, dr, b in lines if dr == "TX"])

target, reads, n = None, {}, 0
first_t = None
for t, fr in tx:
    if t < t_min:
        continue
    if fr == d.RESET_FRAME:
        print(f"{t:10.3f}  RESET MULTI")
        continue
    if fr[1] != 0xFF or fr[2] not in (0x58, 0x59, 0x5A) or len(fr) < 5:
        continue
    cmd, args = fr[3], fr[4:-1]
    sid = int.from_bytes(args[:2], "little") if args else None
    if cmd in (0x35, 0x31):
        reads.setdefault(sid, set()).add(cmd)
    elif cmd == 0x33:
        target = sid
    elif cmd == 0x34 and target is not None:
        raw = int.from_bytes(args[:2], "little")
        r = reads.get(target, set())
        pre = ("info+read" if r == {0x35, 0x31} else "read" if 0x31 in r else "info" if 0x35 in r else "-")
        first_t = first_t or t
        print(f"{t:10.3f}  ID {target:3} {d.SETTING_NAMES.get(target, '?'):28} raw={raw:5}   before: {pre}")
        n += 1
        target = None
        reads.pop(target, None)
print(f"\n{n} writes" + (f", first t={first_t:.1f}" if first_t else ""))
