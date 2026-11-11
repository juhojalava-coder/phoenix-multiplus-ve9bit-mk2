"""MK2 link quality: comparison between COM ports (e.g. motherboard COM1 vs. USB-RS232 adapter).

READS ONLY: opens the connection like the panel (V, R, S, scales) and polls RAM values and the LED word with the allowed
Winmon 0x30 / 0x02 commands. Does NOT send the knob, the switch or settings, so the state of the device does not change.

Usage:  python link_quality.py PORT [seconds=60]      (close the panel/bridge first)

Measures:
  - response rate (a query without a response = a dropout or a wrongly read frame)
  - response time (ms): mean, median, 95 % and maximum
  - dropped bytes (wrong checksum -> electrical interference or synchronisation)
  - plausibility: Udc and Idc stay in the expected range (a wrong value = a bit error)
  - DTR state (the MK2.2 gets its supply voltage from DTR)
The result is printed and written to the file link_quality_<PORT>_<time>.txt.
"""
import datetime
import sys
import time

import mk2_link as m

RAM = (9, 2, 1, 0, 8, 5, 7, 4, 6)          # same as the panel
UDC_RANGE = (18.0, 32.0)                    # 24 V system
IDC_RANGE = (-150.0, 80.0)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM1"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    lines = []

    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    t_open = time.time()
    link = m.MK2(port)
    say(f"port {port}, DTR={link.ser.dtr}, opened {time.time() - t_open:.2f} s")
    t0 = time.time()
    try:
        link.connect()
    except Exception as e:  # noqa: BLE001
        say(f"CONNECTION FAILED: {e}")
        say(f"received frames {link.rx_frames}, dropped bytes {link.rx_dropped}")
        link.close()
        return 1
    say(f"connection {time.time() - t0:.2f} s: MK2 {link.mk2_version} '{link.mk2_mode}', device fw "
        f"{(link.fw.get(0x82, 0) | link.fw.get(0x83, 0) << 16) or '?'}")
    sent = ok = 0
    lat = []
    bad_values = []
    t_end = time.time() + secs
    cycles = 0
    while time.time() < t_end:
        cycles += 1
        for rid in RAM:
            sent += 1
            t = time.perf_counter()
            res = [v for rc, v in link.winmon(0x30, bytes([rid])) if rc == 0x85]
            if res:
                ok += 1
                lat.append((time.perf_counter() - t) * 1000)
                val = link.conv(rid, res[0])
                if rid == 4 and val is not None and not UDC_RANGE[0] <= val <= UDC_RANGE[1]:
                    bad_values.append(("Udc", val))
                if rid == 5 and val is not None and not IDC_RANGE[0] <= val <= IDC_RANGE[1]:
                    bad_values.append(("Idc", val))
        sent += 1
        t = time.perf_counter()
        if [v for rc, v in link.winmon(0x02) if rc == 0x81]:
            ok += 1
            lat.append((time.perf_counter() - t) * 1000)
    dur = secs
    link.close()
    say("")
    say(f"kesto {dur:.0f} s, kierroksia {cycles} ({dur / cycles:.2f} s/kierros)")
    say(f"queries {sent}, replies {ok} = {100 * ok / sent:.2f} %, missing {sent - ok}")
    if lat:
        q = sorted(lat)   # no statistics module: it is missing from the Python of Venus OS
        say(f"vastausaika ms: ka {sum(q) / len(q):.0f}, mediaani {q[len(q) // 2]:.0f}, "
            f"95 % {q[int(0.95 * (len(q) - 1))]:.0f}, max {max(lat):.0f}")
    say(f"received frames {link.rx_frames}, dropped bytes (checksum) {link.rx_dropped}")
    say(f"implausible values {len(bad_values)}" + (f": {bad_values[:5]}" if bad_values else ""))
    name = f"link_quality_{port.replace('/', '_')}_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
    with open(name, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nsaved {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
