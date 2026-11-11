"""Compares the final state of the settings (taking writes into account) with the original capture.

Usage: python compare_settings.py <original.log> <new.log>
From the original the values read by the configuration program (0x31) are taken. From the new one
the read values + all writes (0x33/0x34) in chronological order = the final state.
"""
import sys
import mk2_decode as d

orig = d.decode(sys.argv[1])
new = d.decode(sys.argv[2])

final = dict(new.setting_val)  # decode also updates setting_val from the writes
last_write = {sid: t for t, sid, _, _ in new.setting_writes}

diff = 0
for sid in sorted(orig.setting_val):
    a, b = orig.setting_val[sid], final.get(sid)
    if a is None:
        continue
    info = orig.setting_info.get(sid, {})
    scale = d.setting_scale(info["sc"]) if info.get("sc") else None
    off = d.s16(info.get("offset", 0))

    def sv(x):
        return "?" if x is None else (d.fmt(scale * (x + off)) if scale else str(x))
    if a != b:
        diff += 1
        print(f"EROAA  ID {sid:3} {d.SETTING_NAMES.get(sid, sid):28} alkup {sv(a):>8} (raw {a})  nyt {sv(b):>8} (raw {b})"
              f"  last write t={last_write.get(sid, '-')}")
        base = {0: 0, 1: 16}.get(sid)
        if base is not None and b is not None:
            for bit in range(16):
                if (a ^ b) >> bit & 1:
                    print(f"         flag[{base + bit}] {d.FLAG_NAMES.get(base + bit, '?')}: "
                          f"orig {'ON' if a >> bit & 1 else 'off'} now {'ON' if b >> bit & 1 else 'off'}")
print(f"\n{len(orig.setting_val)} asetusta verrattu, {diff} eroaa.")
