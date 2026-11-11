"""Follows the log of mk2_proxy.py in real time and prints changes of the RAM values.

Usage: python mk2_watch.py capture_XXXX.log
First reads the log from the start (RAM scales 0x36), then follows new lines.
Prints a line when a value changes by more than the threshold, and a summary every 10 s.
"""
import sys, time
import mk2_decode as d

PATH = sys.argv[1]
THRESH = {0: 2.0, 1: 0.2, 2: 2.0, 3: 0.2, 4: 0.1, 5: 0.5, 6: 0.05, 7: 0.02, 8: 0.02, 9: 0.2}
SHORT = {0: "UMains", 1: "IMains", 2: "UOut", 3: "IInv", 4: "Udc", 5: "Idc",
         6: "Ripple", 7: "PerOut", 8: "PerIn", 9: "IOut"}

ram_info = {}
pending = {}
last = {}      # id -> last printed value
cur = {}       # id -> latest value
bufs = {"TX": bytearray(), "RX": bytearray()}
live = False


def conv(rid, raw):
    sc, off = ram_info.get(rid, (None, 0))
    if sc is None or d.ram_scale(sc) is None:
        return float(raw)
    scale, signed = d.ram_scale(sc)
    x = d.s16(raw) if signed else raw
    return scale * (x + d.s16(off))


def show(rid, v):
    if rid in (7, 8):
        hz = d.period_to_hz(v)
        return f"{hz:.2f}Hz" if hz else "-"
    return f"{v:.2f}"


SWITCH = {1: "charger only", 2: "inverter only", 3: "on", 4: "off"}
QUIET_WINMON = {0x30, 0x31, 0x35, 0x36, 0x05, 0x06, 0x02, 0x03}  # routine; 0x02/0x03 are handled separately
# 0x81 LED word (lo | hi<<8), verified 30.10.2026: 0 overload, 1 low battery, 2 temperature (emulator),
# 4 inverter, 5 mains, 6 bulk, 7 absorption, 8 float. Bit 15 always 1. Bits 3, 9-14 do not show in the display of the official software.
LEDS = ["overload", "lowbat", "temp", "?b3", "inverter", "mains", "bulk", "absorption",
        "float", "?b9", "?b10", "?b11", "?b12", "?b13", "?b14", "(b15)"]
last_sw = None   # latest Winmon 0x03 value (switch)
last_led = None  # latest 0x81 reply
last_s = {}  # direction -> last printed S frame


def describe_s(p):
    """'S' command (PDF chapter 5). p = data without the 'S' byte."""
    if len(p) < 5:
        return p.hex(" ")
    sw, b1, b2, _one, flags = p[:5]
    txt = f"switch={sw}({SWITCH.get(sw, '?')})"
    if flags & 0x80:
        lim = b1 | b2 << 8
        txt += f" limit={'ignore' if lim >= 0x8000 else f'{lim / 10:.1f}A'}"
    else:
        txt += f" pot={b1} scale={b2}A"
    fl = [n for b, n in ((0, "auto-send"), (1, "LED-append"), (4, "NO-SEND"), (6, "fwd-panel"), (7, "amps*10")) if flags >> b & 1]
    txt += f" flags=0x{flags:02X}[{','.join(fl)}]"
    if len(p) > 5:
        txt += f" ext={p[5:].hex(' ')}"
    return txt


def on_frame(t, direction, fr):
    if fr[1] != 0xFF:
        if live:
            print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  {direction} VE.Bus-kehys: {fr.hex(' ')}", flush=True)
        return
    cmd = fr[2]
    if cmd not in (0x58, 0x59, 0x5A):
        # MK2-level command (S, L, R, A, F...). V frames are routine.
        if live and cmd != 0x56:
            p = fr[3:-1]
            name = chr(cmd) if 32 <= cmd < 127 else f"0x{cmd:02X}"
            desc = describe_s(p) if cmd == 0x53 and direction == "TX" else p.hex(" ")
            # S and its acknowledgement repeat; print only when the content changes (per direction)
            if cmd == 0x53:
                if last_s.get(direction) == fr:
                    return
                last_s[direction] = fr
            print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  {direction} MK2 '{name}' {desc}   [{fr.hex(' ')}]", flush=True)
        return
    global last_sw, last_led
    L, p = fr[2], fr[3:-1]
    if direction == "TX":
        pending[L] = p
        if p and p[0] == 0x03 and len(p) > 1:  # switch state (undocumented, observed)
            if live and p[1] != last_sw:
                print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  >>> SWITCH Winmon 0x03 {p[1]} = {SWITCH.get(p[1], '?')}"
                      f"{'' if last_sw is None else f'  (was {SWITCH.get(last_sw, last_sw)})'}", flush=True)
            last_sw = p[1]
        elif live and p and p[0] not in QUIET_WINMON:
            print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  TX Winmon 0x{p[0]:02X} {p[1:].hex(' ')}   [{fr.hex(' ')}]", flush=True)
        return
    # 0x81 = LED/state reply (also arrives spontaneously); decoding as a hypothesis: low byte = LED on
    for i in range(0, len(p) - 2, 3):
        if p[i] == 0x81:
            val = p[i + 1] | p[i + 2] << 8
            if val != last_led:
                if live:
                    on = [n for b, n in enumerate(LEDS) if val >> b & 1 and b != 15]
                    print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  LED 0x81 = 0x{val:04X}  lo={p[i + 1]:08b} hi={p[i + 2]:08b}"
                          f"  on:{','.join(on) or '-'}", flush=True)
                last_led = val
    req = pending.get(L)
    if not req:
        return
    for i in range(0, len(p) - 2, 3):
        rc, val = p[i], p[i + 1] | p[i + 2] << 8
        if req[0] == 0x36:
            rid = int.from_bytes(req[1:3], "little")  # the ID is 16 bits
            sc, off = ram_info.get(rid, (0, 0))
            if rc == 0x8E:
                ram_info[rid] = (val, off)
            elif rc == 0x8F:
                ram_info[rid] = (sc, val)
        elif req[0] == 0x30 and rc == 0x85:
            rid = req[1]
            v = conv(rid, val)
            cur[rid] = v
            if live:
                prev = last.get(rid)
                if prev is None or abs(v - prev) >= THRESH.get(rid, 0.5):
                    tag = "" if prev is None else f"  (was {show(rid, prev)})"
                    print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  {SHORT.get(rid, rid):7} raw={val:5}  -> {show(rid, v)}{tag}",
                          flush=True)
                    last[rid] = v
            else:
                last[rid] = v


def feed(line):
    p = line.split()
    if len(p) < 3 or p[1] not in ("TX", "RX"):
        return
    t, dr = float(p[0]), p[1]
    buf = bufs[dr]
    buf.extend(int(h, 16) for h in p[2:])
    while buf:
        if buf[:len(d.RESET_FRAME)] == d.RESET_FRAME:
            print(f"{time.strftime('%H:%M:%S')} t={t:8.1f}  {dr} *** RESET MULTI *** {d.RESET_FRAME.hex(' ')}", flush=True)
            del buf[:len(d.RESET_FRAME)]
            continue
        if (buf[0] & 0x7F) > d.MAX_LEN:  # garbage byte, not a length
            del buf[:1]
            continue
        n = (buf[0] & 0x7F) + 2
        if len(buf) < n:
            break
        fr = bytes(buf[:n])
        if sum(fr) & 0xFF == 0:
            on_frame(t, dr, fr)
            del buf[:n]
        else:
            del buf[:1]


with open(PATH, encoding="utf-8") as f:
    for line in f:
        feed(line)
    live = True
    print("Lahtotaso:", "  ".join(f"{SHORT.get(k, k)}={show(k, v)}" for k, v in sorted(cur.items())), flush=True)
    print("Watching for changes... (Ctrl+C stops)", flush=True)
    t_sum = time.time()
    while True:
        line = f.readline()
        if line:
            feed(line)
            continue
        time.sleep(0.1)
        if time.time() - t_sum > 10:
            t_sum = time.time()
            print(time.strftime('%H:%M:%S'), "state:", "  ".join(f"{SHORT.get(k, k)}={show(k, v)}" for k, v in sorted(cur.items())), flush=True)
