"""MK2 + MultiPlus emulator built from a captured log (no real device involved).

It can be used over a com0com pair or in-process (open_virtual).

Usage: python mk2_emu.py [COM21] [source_log]
  client program -> COM7 ==(com0com)== COM21 -> this emulator

Replies are built from the source log (default: the first capture = original settings):
  V, R, S, L                 -> like the MK2 (captured frames)
  Winmon 0x05/0x06           -> firmware version
  Winmon 0x35 / 0x31         -> settings info / values
  Winmon 0x36 / 0x30         -> RAM scales / values
  Winmon 0x02                -> 0x81 LED word (controllable)
  Winmon 0x33 + 0x34         -> stored, acknowledged with 0x88
  Winmon 0x03, 0x04, 0x44, 0x3B and the reset frame -> no reply (like the device)

Control: emu_ctrl.json is read every 0.5 s, e.g.
  {"led": "0x8001", "ram": {"4": 2650}, "note": "bit 0"}
Traffic is logged to emu_YYYYmmdd_HHMMSS.log (compatible with mk2_decode).
"""
import datetime
import json
import os
import sys
import threading
import time

import serial

import mk2_decode as d

# The command line is read only in a standalone run (main). When this module is imported by another program
# (the panel, the survey recorder, the tests) the arguments of that program must not be touched.
DEFAULT_SRC = "data/reference_settings_read.log"
PORT = "COM21"
SRC = DEFAULT_SRC
HERE = os.path.dirname(os.path.abspath(__file__))
CTRL = os.path.join(HERE, "emu_ctrl.json")

V_FRAME = bytes.fromhex("07 FF 56 98 3E 11 00 57 66")
S_ACK = bytes.fromhex("02 FF 53 AC")
L_REPLY = bytes.fromhex("08 FF 4C 00 00 00 00 FF 00 AE")
REPLY_DELAY = 0.15     # the device replies in ~150-180 ms
MULTI_GAP = 0.05       # 0x35/0x36: consecutive reply frames 50 ms apart
V_IDLE = 1.0           # spontaneous V frame when there is no traffic

# ---------------------------------------------------------------- state from the source log
settings, values, ram_info, ram, fw = {}, {}, {}, {}, {}
state = {"led": 0x8010, "note": ""}


def load_source(name):
    """(Re)loads the settings, RAM scales/values and firmware from a capture log (path relative to this folder)."""
    global SRC
    st = d.decode(os.path.join(HERE, name))
    settings.clear()
    settings.update({sid: dict(info) for sid, info in st.setting_info.items()})
    values.clear()
    values.update(st.setting_val)
    ram_info.clear()
    ram_info.update({rid: tuple(v) for rid, v in st.ram_info.items()})
    ram.clear()
    ram.update({rid: vals[-1][1] for rid, vals in st.ram_vals.items()})
    fw.clear()
    fw.update(dict(st.version) or {0x82: 0x1BE0, 0x83: 0x001C})
    SRC = name


load_source(DEFAULT_SRC)

logf = None   # emu_*.log is opened only in a standalone run (main); the panel's internal emulator does not log
t0 = time.perf_counter()


def log(direction, data):
    if logf is None:
        return
    logf.write(f"{time.perf_counter() - t0:10.3f}  {direction}  " + " ".join(f"{b:02X}" for b in data) + "\n")


def build(payload):
    body = bytes([len(payload) + 1, 0xFF]) + payload
    return body + bytes([(-sum(body)) & 0xFF])


def triplet(code, val):
    return bytes([code, val & 0xFF, (val >> 8) & 0xFF])


def winmon_replies(cmd, args):
    """Returns a list of reply triplets (each in its own frame) or []."""
    sid = int.from_bytes(args[:2], "little") if args else 0
    if cmd == 0x05:
        return [triplet(0x82, fw.get(0x82, 0))]
    if cmd == 0x06:
        return [triplet(0x83, fw.get(0x83, 0))]
    if cmd == 0x35:
        info = settings.get(sid, {})
        if not info.get("sc"):
            return [triplet(0x89, 0)]
        return [triplet(0x89, info["sc"]), triplet(0x8A, info.get("offset", 0)), triplet(0x8B, info.get("default", 0)),
                triplet(0x8C, info.get("min", 0)), triplet(0x8D, info.get("max", 0))]
    if cmd == 0x31:
        if settings.get(sid, {}).get("sc") and values.get(sid) is not None:
            return [triplet(0x86, values[sid])]
        return [triplet(0x91, 0)]
    if cmd == 0x36:
        sc, off = ram_info.get(sid, (0, 0))
        return [triplet(0x8E, sc)] if not sc else [triplet(0x8E, sc), triplet(0x8F, off)]
    if cmd == 0x30:
        rid = args[0] if args else 0
        if rid in ram:
            return [triplet(0x85, ram[rid])]
        return [triplet(0x90, 0)]
    if cmd == 0x02:
        return [triplet(0x81, state["led"])]
    if cmd == 0x33:
        state["write_target"] = sid
        return []
    if cmd == 0x34:
        tgt = state.pop("write_target", None)
        if tgt is not None:
            old = values.get(tgt, 0)
            values[tgt] = int.from_bytes(args[:2], "little")
            print(f"   WRITE setting {tgt} = {values[tgt]} (was {old})", flush=True)
            return [triplet(0x88, old)]
        return []
    return []  # 0x03, 0x04, 0x44, 0x3B, unknown: no reply (like the device)


def load_ctrl(last_mtime):
    try:
        m = os.path.getmtime(CTRL)
    except OSError:
        return last_mtime
    if m == last_mtime:
        return m
    try:
        with open(CTRL, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        print("ctrl error:", e, flush=True)
        return m
    if "led" in c:
        state["led"] = int(c["led"], 0) if isinstance(c["led"], str) else int(c["led"])
    for k, v in c.get("ram", {}).items():
        ram[int(k)] = int(v)
    state["note"] = c.get("note", "")
    print(f"== CONTROL: LED word 0x{state['led']:04X}  ({state['note']})", flush=True)
    return m


# ---------------------------------------------------------------- control from inside the process (panel)
# The internal emulator (open_virtual) shares this module-level state, so the panel can change
# the measurements and the LED word directly. Values are in display units (V, A); conversion to a raw value
# uses the device RAM scale (the same formula as mk2_link.MK2.conv, inverted).
def ram_value(rid):
    """Current value of a RAM variable in display units or None (no scale / no value)."""
    if rid not in ram or rid not in ram_info:
        return None
    sc, off = ram_info[rid]
    rs = d.ram_scale(sc)
    if rs is None:
        return None
    scale, signed = rs
    x = d.s16(ram[rid]) if signed else ram[rid]
    return scale * (x + d.s16(off))


def set_ram_value(rid, value):
    """Sets a RAM variable (V/A). Signed ones (Idc, IOut): negative = discharge / feed-in.
    Returns the raw value that was set. Frequencies (7, 8) are not supported (period time)."""
    if rid in (7, 8):
        raise ValueError("frequency is not adjustable")
    sc, off = ram_info[rid]
    rs = d.ram_scale(sc)
    if rs is None:
        raise ValueError(f"RAM {rid}: no scale")
    scale, signed = rs
    x = int(round(value / scale - d.s16(off)))
    if signed:
        x = max(-0x8000, min(0x7FFF, x)) & 0xFFFF
    else:
        x = max(0, min(0xFFFF, x))
    ram[rid] = x
    return x


def led_word():
    return state["led"]


def set_led_bit(bit, on):
    """Sets/clears a bit of the LED word (the LEDs of the DMC view, mk2_panel.LED_MAP)."""
    if on:
        state["led"] |= 1 << bit
    else:
        state["led"] &= ~(1 << bit) & 0xFFFF
    return state["led"]


class _Pipe:
    """One-way byte pipe (virtual port)."""

    def __init__(self):
        self.buf = bytearray()
        self.cv = threading.Condition()


class VirtualSerial:
    """pyserial-compatible (read/write/in_waiting/close/dtr) in-process port.
    open_virtual() creates a pair: one end for the link, the other for the emulator (no com0com)."""

    def __init__(self, rx, tx, timeout=0.05, closed=None):
        self._rx, self._tx = rx, tx
        self.timeout = timeout
        self.dtr = True
        self._closed = closed if closed is not None else threading.Event()

    @property
    def closed(self):
        return self._closed.is_set()

    @property
    def in_waiting(self):
        return len(self._rx.buf)

    def read(self, size=1):
        if self.closed:
            raise serial.SerialException("virtual port closed")
        with self._rx.cv:
            if not self._rx.buf:
                self._rx.cv.wait(self.timeout)
            out = bytes(self._rx.buf[:size])
            del self._rx.buf[:size]
        return out

    def write(self, data):
        if self.closed:
            raise serial.SerialException("virtual port closed")
        with self._tx.cv:
            self._tx.buf += data
            self._tx.cv.notify_all()
        return len(data)

    def close(self):
        self._closed.set()


def open_virtual():
    """Starts the emulator in a background thread and returns the link-side VirtualSerial port.
    Closing the port also stops the emulator."""
    a, b = _Pipe(), _Pipe()
    closed = threading.Event()
    link_side = VirtualSerial(rx=a, tx=b, timeout=0.05, closed=closed)
    emu_side = VirtualSerial(rx=b, tx=a, timeout=0.02, closed=closed)
    threading.Thread(target=serve, args=(emu_side,), kwargs={"quiet": True}, daemon=True,
                     name="mk2-emulator").start()
    return link_side


def main():
    global logf, PORT
    if len(sys.argv) > 1:
        PORT = sys.argv[1]
    if len(sys.argv) > 2:
        load_source(sys.argv[2])
    logf = open(os.path.join(HERE, f"emu_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"), "a", buffering=1,
                encoding="utf-8")
    ser = serial.Serial(PORT, 2400, bytesize=8, parity="N", stopbits=1, timeout=0.02)
    print(f"Emulator {PORT}, source {SRC}: {len(settings)} setting infos, {len(values)} values, "
          f"{len(ram_info)} RAM infos, log {os.path.basename(logf.name)}", flush=True)
    serve(ser)


def serve(ser, quiet=False):
    """Emulator loop: a real serial port (main) or VirtualSerial (open_virtual).
    Ends when the port is closed. quiet: no write/Winmon output (the panel's internal emulator)."""
    global print
    if quiet:
        def print(*_a, **_k):  # noqa: A001 - only this module's output
            pass
    try:
        _serve(ser)
    except serial.SerialException:
        return


def _serve(ser):
    buf = bytearray()
    last_rx = time.time()
    last_v = 0.0
    ctrl_m = load_ctrl(None)
    t_ctrl = 0.0
    while True:
        try:
            chunk = ser.read(ser.in_waiting or 1)
        except serial.SerialException:
            return                      # port closed (VirtualSerial.close or unplugged)
        now = time.time()
        if chunk:
            log("TX", chunk)  # TX = from the client program (same direction as in the proxy logs)
            buf += chunk
            last_rx = now
        while buf:
            if buf[:len(d.RESET_FRAME)] == d.RESET_FRAME:
                print("   RESET MULTI frame", flush=True)
                del buf[:len(d.RESET_FRAME)]
                continue
            if (buf[0] & 0x7F) > d.MAX_LEN:
                del buf[:1]
                continue
            n = (buf[0] & 0x7F) + 2
            if len(buf) < n:
                break
            fr = bytes(buf[:n])
            if sum(fr) & 0xFF != 0 or fr[1] != 0xFF:
                del buf[:1]
                continue
            del buf[:n]
            cmd = fr[2]
            out = []
            if cmd == 0x56:      # V
                out = [V_FRAME]
            elif cmd == 0x52:    # R: MK2 reset -> V
                time.sleep(0.4)
                out = [V_FRAME]
            elif cmd == 0x53:    # S
                out = [S_ACK]
            elif cmd == 0x4C:    # L
                out = [L_REPLY]
            elif cmd in (0x57, 0x58, 0x59, 0x5A):
                p = fr[3:-1]
                reps = winmon_replies(p[0], p[1:]) if p else []
                out = [build(bytes([cmd]) + r) for r in reps]
                if p and p[0] not in (0x30, 0x31, 0x35, 0x36, 0x02, 0x05, 0x06):
                    print(f"   Winmon 0x{p[0]:02X} {p[1:].hex(' ')}", flush=True)
            time.sleep(REPLY_DELAY)
            for i, o in enumerate(out):
                if i:
                    time.sleep(MULTI_GAP)
                ser.write(o)
                log("RX", o)
        if now - last_rx > V_IDLE and now - last_v > V_IDLE:
            ser.write(V_FRAME)
            log("RX", V_FRAME)
            last_v = now
        if now - t_ctrl > 0.5:
            ctrl_m = load_ctrl(ctrl_m)
            t_ctrl = now


if __name__ == "__main__":
    main()
