"""Read-only tool: opens the MK2 connection like the panel, reads the RAM values, does NOT send the knob,
the switch or settings. For testing the memory of the knob.

Usage:  python read_only.py [COM1] [seconds]

Prints about once a second: Udc, Idc, IMains, IOut, UOut, LED. The knob value cannot be read from the
device, so the charge level is inferred from Idc (charging = Idc positive).
"""
import sys
import time

import mk2_link as m

port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 40

link = m.MK2(port)
link.connect()   # V, R, S, scales - same as the panel. NO set_knob/set_switch/poll.
print(f"connection OK, MK2 {link.mk2_version} '{link.mk2_mode}', fw {link.fw}", flush=True)

# Does ID 6 (IMainsLimit) read the knob value? The knob 0x04 and ID 6 control the same AC limit.
r6 = [v for rc, v in link.winmon(0x31, bytes([6, 0])) if rc == 0x86]
if r6:
    print(f"ID 6 IMainsLimit raw={r6[0]} = {r6[0] * 0.2:.1f} A", flush=True)
print(f"{'t':>5}  {'Udc':>6} {'Idc':>6} {'IMains':>7} {'IOut':>6} {'UOut':>6}  LED", flush=True)

t0 = time.time()
while time.time() - t0 < secs:
    vals = {}
    for rid in (4, 5, 1, 9, 2):
        for rc, raw in link.winmon(0x30, bytes([rid])):
            if rc == 0x85:
                vals[rid] = link.conv(rid, raw)
    led = ""
    for rc, v in link.winmon(0x02):
        if rc == 0x81:
            led = f"0x{v:04X}"
    print(f"{time.time() - t0:5.1f}  {vals.get(4, 0):6.2f} {vals.get(5, 0):6.1f} "
          f"{vals.get(1, 0):7.2f} {vals.get(9, 0):6.1f} {vals.get(2, 0):6.0f}  {led}", flush=True)
    time.sleep(1.0)

link.close()
print("done", flush=True)
