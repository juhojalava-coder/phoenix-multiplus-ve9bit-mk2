"""MK2 driver: direct connection to the MK2 (without the official configuration software).

Replays a sequence captured from the official configuration software. Sends ONLY commands that
were observed in that traffic (ALLOWED lists). Writing settings (0x33+0x34) is possible
ONLY through the write_settings() method (validation + read-modify-write of the flags),
and resetting the device only through the reset() method.

Safety gates (docs/phoenixconfig.md "Monitor only and control"):
  - monitor only by default: knob and switch commands are sent only after enable_control() / control=True
  - writing, reset and control only on verified firmware (WRITE_ALLOWED_FW); otherwise read-only
  - a write chain stops at the first failed write (WriteAborted) and marks settings_dirty

  connect:  'V' -> 'R' (MK2 reset) -> 'S' with the NO-SEND flag
  scales:   Winmon 0x36 GetRAMVarInfo, RAM IDs 0..9
  query:    Winmon 0x30 ReadRAMVar, 0x02 LED state (reply 0x81)
  switch:   Winmon 0x03 <1|3|4>  (undocumented; 1=charger only 3=on 4=off)

Traffic is logged in the mk2_proxy.py format (capture_direct_*.log).
"""
import datetime
import os
import queue
import threading
import time

import serial

import mk2_decode as d

BAUD = 2400
EMULATOR_PORT = "emulator"            # MK2("emulator") = internal emulator (mk2_emu.open_virtual)
LETTERS = (0x58, 0x59, 0x5A)          # X, Y, Z in turn (as in the captured traffic)
RAM_IDS = (9, 2, 1, 0, 8, 5, 7, 4, 6)  # polling order seen in the captured traffic
# 1/3/4 verified (seen in the captured traffic). 2 = "inverter only" (PDF chapter 5, S command): the
# official software does not send it, but it was verified on a real device on 1.11.2026: the AC input relay
# opens, the load runs from the battery (IMains 0, Idc -13 A @ 310 W), and on <-> inverter only
# transitions are seamless with two loads. RAM level, no EEPROM write.
SWITCH_STATES = {1: "charger only", 2: "inverter only", 3: "on", 4: "off"}
SWITCH_REPEAT_S = 10.0                 # the switch state is repeated about every 10 s

S_RESET = bytes.fromhex("53 04 00 ff 01 50 00 04")

# ---- writing settings (captured from a "Send settings" run, data/reference_settings_write.log)
# Write order seen in that capture (all settings); hysteresis values last.
WRITE_ORDER = (0, 1, 6, 13, 14, 44, 46, 5, 11, 48, 10, 2, 3, 4, 7, 8, 9, 15, 16, 18, 17, 23, 22, 21, 20,
               24, 25, 26, 27, 19, 28, 30, 29, 34, 35, 32, 33, 31, 36, 39, 40, 41, 42, 37, 38, 43, 45, 47, 12)
FLAG_IDS = (0, 1)
# Flag bits that may be changed (the ones exposed in the official software). Other bits are kept from the device value.
#   Flags0: 0..8, 11, 13, 14  (NOT 9, 10, 12 'NotPromoted', NOT 15)
#   Flags1: 16..28 = word bits 0..12
FLAG_WRITE_MASK = {0: 0x69FF, 1: 0x1FFF}
FLAG0_UPS, FLAG0_UPS_INV = 1 << 3, 1 << 7   # bit 7 = NOT bit 3, always toggled together in the captured traffic
# Device profiles (mk2.toml [device] nominal_ac_voltage). Hard extra limit on top of the device min/max
# (in display units): UInvSetpoint (5), AC low disconnect (44), AC high disconnect (46).
# Only the tested profile is in use. 230 V: placeholder, limits to be defined when the device is tested.
DEVICE_PROFILES = {
    120: {"tested": True, "hard_limits": {5: (94, 128), 44: (80, 120), 46: (120, 143)}},
    230: {"tested": False, "hard_limits": None},
}
HARD_LIMITS = DEVICE_PROFILES[120]["hard_limits"]


def device_profile(nominal_ac_voltage):
    """Returns the hard_limits table of the profile or raises NotAllowed if the profile is not usable."""
    p = DEVICE_PROFILES.get(nominal_ac_voltage)
    if p is None:
        raise NotAllowed(f"mains voltage {nominal_ac_voltage} V: unknown device profile "
                         f"(known: {sorted(DEVICE_PROFILES)})")
    if not p["tested"] or not p["hard_limits"]:
        raise NotAllowed(f"the {nominal_ac_voltage} V profile has not been defined or tested yet "
                         f"(project restriction: only {[v for v, q in DEVICE_PROFILES.items() if q['tested']]} V)")
    return dict(p["hard_limits"])
WRITE_GAP_S = 0.11      # 0x33 -> 0x34 (captured 0.11 s)


# Allowed frames. MK2 level: exact bytes. Winmon: command -> allowed arguments (None = any ID)
ALLOWED_MK2 = {
    b"V",
    b"R",
    bytes.fromhex("53 04 00 ff 01 10 00 04"),  # 'S' NO-SEND (connection sequence)
    b"F\x05",  # PDF 6.2 MasterMultiLED request (read-only). NOTE: 'F' 08 = reset -> NOT allowed
    b"L",      # PDF chapter 4 LED status (read-only); sent at the start of a connection in the captured traffic
    S_RESET,   # 'S' flags 0x50 (NO-SEND + forward panel frames): sent before RESET_FRAME in the captured traffic
}
KNOB_MIN, KNOB_MAX = 20, 159  # 0x04 value = 0.1 A; 2.0 A (minimum of setting 6) .. 15.9 A (knob maximum seen in the captured traffic)

ALLOWED_WINMON = {
    0x02: {b""},                                  # LED state
    0x03: {bytes([s]) for s in SWITCH_STATES},    # switch
    0x04: {bytes([v]) for v in range(KNOB_MIN, KNOB_MAX + 1)},  # current limit knob (undocumented)
    0x05: {b""}, 0x06: {b""},                     # version
    0x30: None,                                   # ReadRAMVar
    0x31: None,                                   # ReadSetting (read-only)
    0x35: None,                                   # GetSettingInfo (read-only)
    0x36: None,                                   # GetRAMVarInfo
}
SETTING_IDS = range(0, 66)  # PDF 7.3.13.2: 0..65; this device supports 0..48


def device_fw(fw):
    """Device firmware 0x82 | 0x83 << 16 (e.g. 1842144), or None if not read yet."""
    if 0x82 in fw and 0x83 in fw:
        return fw[0x82] | fw[0x83] << 16
    return None


# Device firmware verified on a real device for writing settings, reset and control (knob/switch):
# Phoenix MultiPlus 24/3000/70-30 120 V, firmware 1842144 (Winmon 0x05 = 0x1BE0, 0x06 = 0x001C), docs/hardware.md.
# Any other firmware is READ-ONLY: settings can be read and the device monitored, nothing is changed.
# Override only on purpose with allow_unknown_firmware=True (command line --allow-unknown-firmware), at your own risk.
WRITE_ALLOWED_FW = frozenset({1842144})


class WriteAborted(RuntimeError):
    """A settings write chain stopped at the first failure. The device may now hold a mix of old and new values.

    results:     (id, old, written, read_back, ok) of the settings handled so far (the failed one is last)
    failed_id:   the setting that failed
    not_written: settings of the request that were NOT written because the chain stopped"""

    def __init__(self, msg, results, failed_id, not_written):
        super().__init__(msg)
        self.results, self.failed_id, self.not_written = results, failed_id, not_written


MAINS_PRESENT_V = 90.0   # UMains from this value up = mains is connected to the input (120 V device)


def infer_switch(word, umains=None):
    """Switch state INFERRED from the LED word, because the device does not report the switch position.

    Returns (candidates, explanation) or None if there is no LED data. One candidate = unambiguous.
    Basis (docs/protocol.md, sections 6 and 7):
      - mains on LED: relay closed -> on or charger only
      - inverter on without mains on: on or inverter only; if mains is present -> inverter only
      - low battery without inverter/mains: shut down on battery undervoltage (switch on or inverter only)
      - all dark: off; without mains charger only looks the same
    """
    if word is None:
        return None
    mains_led, inv_led, low = word >> 5 & 1, word >> 4 & 1, word >> 1 & 1
    mains_v = umains is not None and umains >= MAINS_PRESENT_V
    if mains_led:
        return {3, 1}, "on or charger only (relay closed)"
    if inv_led:
        if mains_v:
            # exception: on + VS 'ignore AC' (VS OFF) looks the same (test 3a) - hence 'probably'
            return {2}, "probably inverter only (inverting although mains is present)"
        return {3, 2}, "on or inverter only (inverting, no mains)"
    if low:
        return {3, 2}, "shut down on battery undervoltage (switch on or inverter only)"
    if mains_v:
        return {4}, "off (all LEDs dark, mains present)"
    return {4, 1}, "off or charger only (all LEDs dark, no mains)"


class NotAllowed(Exception):
    pass


def build(payload: bytes) -> bytes:
    body = bytes([len(payload) + 1, 0xFF]) + payload
    return body + bytes([(-sum(body)) & 0xFF])


class MK2:
    """Same public state as mk2_panel.LogTail: values, ram_info, led, switch, mk2_version, mk2_mode, fw."""

    # Default limits; mk2.toml may narrow them (limits=AppConfig.link_limits()), not widen them.
    knob_min, knob_max = KNOB_MIN, KNOB_MAX
    repeat_s = SWITCH_REPEAT_S
    hard_limits = HARD_LIMITS
    max_charge_current_a = None
    # Monitor only by default: no knob (0x04) or switch (0x03) command is sent until control is enabled
    # (enable_control(), mk2.toml [control] enabled = true). Reading and monitoring work either way.
    control = False
    allow_unknown_firmware = False    # True = write/control also on firmware outside WRITE_ALLOWED_FW (risky)
    # True after a write chain stopped part-way (WriteAborted): the device may hold a mix of old and new settings.
    # Cleared by a complete successful write chain or a fresh read_settings().
    settings_dirty = False

    def __init__(self, port, log_dir=".", limits=None, control=False, allow_unknown_firmware=False):
        self.control = bool(control)
        self.allow_unknown_firmware = bool(allow_unknown_firmware)
        if limits:
            self.knob_min = max(KNOB_MIN, int(limits.get("knob_min", KNOB_MIN)))
            self.knob_max = min(KNOB_MAX, int(limits.get("knob_max", KNOB_MAX)))
            if self.knob_min > self.knob_max:
                raise NotAllowed(f"knob range {self.knob_min}..{self.knob_max} is empty")
            self.repeat_s = float(limits.get("repeat_s", SWITCH_REPEAT_S))
            self.max_charge_current_a = limits.get("max_charge_current_a")
            self.hard_limits = device_profile(limits.get("nominal_ac_voltage", 120))
        if port == EMULATOR_PORT:
            # internal emulator (mk2_emu.py) on a virtual port: no device, no com0com
            import mk2_emu
            self.ser = mk2_emu.open_virtual()
        else:
            self.ser = serial.Serial(port, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.05)
        self.ser.dtr = True  # the MK2.2 gets its power from DTR
        if log_dir is None:               # no capture log (long-running service, e.g. Venus /data)
            self.logf = open(os.devnull, "w", encoding="utf-8")
        else:
            self.logf = open(f"{log_dir}/capture_direct_{datetime.datetime.now():%Y%m%d_%H%M%S}.log",
                             "a", buffering=1, encoding="utf-8")
        self.t0 = time.perf_counter()
        self.lock = threading.RLock()     # one request at a time; RLock: the 0x33+0x34 pair is kept together
        self.rxq = queue.Queue()
        self.stop = threading.Event()
        self.letter_i = 0

        self.values = {}
        self.ram_info = {}
        self.led = None
        self.switch = None        # (state, wallclock) - last SENT
        self.desired_switch = None
        self.mk2_version = None
        self.mk2_mode = None
        self.fw = {}
        self.error = None
        self.rx_frames = 0        # accepted frames (link quality, link_quality.py)
        self.rx_dropped = 0       # dropped bytes (checksum)
        self.cycle_s = None
        self.master = None        # latest MasterMultiLED ('F' 05)
        self.knob = None          # (value, wallclock) last sent 0x04
        # Desired knob. The device does not report the knob value, so it is set right after the connection opens
        # and repeated like the switch: the limit is always known and is restored after a device reset/outage.
        self.desired_knob = None
        self.settings = None      # result of read_settings()
        self.settings_ts = None
        if self.control:
            # control from the start: conservative minimum limit unless the caller restores a saved one
            self.desired_knob = self.knob_min

        threading.Thread(target=self._reader, daemon=True).start()

    # ------------------------------------------------------------ low level
    def _log(self, direction, data):
        self.logf.write(f"{time.perf_counter() - self.t0:10.3f}  {direction}  " + " ".join(f"{b:02X}" for b in data) + "\n")

    def _reader(self):
        buf = bytearray()
        while not self.stop.is_set():
            try:
                chunk = self.ser.read(self.ser.in_waiting or 1)
            except serial.SerialException as e:
                self.error = str(e)
                return
            if not chunk:
                continue
            self._log("RX", chunk)
            buf += chunk
            while buf:
                if (buf[0] & 0x7F) > d.MAX_LEN:
                    del buf[:1]
                    continue
                n = (buf[0] & 0x7F) + 2
                if len(buf) < n:
                    break
                fr = bytes(buf[:n])
                if sum(fr) & 0xFF == 0:
                    self._spontaneous(fr)
                    self.rxq.put(fr)
                    self.rx_frames += 1
                    del buf[:n]
                else:
                    self.rx_dropped += 1      # checksum mismatch -> drop a byte (resync)
                    del buf[:1]

    def _spontaneous(self, fr):
        """Frames the MK2 sends on its own (V, 0x81 LED)."""
        if fr[1] != 0xFF:
            return
        if fr[2] == 0x56 and len(fr) >= 9:
            self.mk2_version = int.from_bytes(fr[3:7], "little")
            self.mk2_mode = chr(fr[7]) if 32 <= fr[7] < 127 else f"0x{fr[7]:02X}"
        elif fr[2] in LETTERS or fr[2] == 0x57:  # 'W': the MK2 sends the LED state on its own after a reset
            p = fr[3:-1]
            for i in range(0, len(p) - 2, 3):
                if p[i] == 0x81:
                    self.led = (p[i + 1], p[i + 2], time.time())

    def _send(self, payload: bytes):
        frame = build(payload)
        self._log("TX", frame)
        self.ser.write(frame)

    def _drain(self):
        while not self.rxq.empty():
            self.rxq.get_nowait()

    # ------------------------------------------------------------ requests
    def mk2_cmd(self, payload: bytes, expect: int, timeout=0.6, vebus=False):
        """vebus=True: the reply is a VE.Bus frame (no 0xFF byte), the identifier is in byte 1."""
        if payload not in ALLOWED_MK2:
            raise NotAllowed(f"MK2 command not allowed: {payload.hex(' ')}")
        with self.lock:
            self._drain()
            self._send(payload)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    fr = self.rxq.get(timeout=max(0.01, end - time.time()))
                except queue.Empty:
                    break
                if vebus and fr[1] == expect:
                    return fr
                if not vebus and fr[1] == 0xFF and fr[2] == expect:
                    return fr
            return None

    def read_master_led(self):
        """'F' 05 -> MasterMultiLED (0x41). PDF 6.2: bytes 5..10 current limits (dA), 11 switch register."""
        fr = self.mk2_cmd(b"F\x05", 0x41, timeout=1.0, vebus=True)  # PDF: the reply can take 750 ms
        if fr is None:
            return None
        data = fr[2:-1]
        if len(data) < 12:
            self.master = {"raw": fr.hex(" "), "error": "short frame"}
            return self.master
        self.master = {
            "raw": fr.hex(" "),
            "ac_cfg": data[4],
            "min_a": int.from_bytes(data[5:7], "little") / 10,
            "max_a": int.from_bytes(data[7:9], "little") / 10,
            "act_a": int.from_bytes(data[9:11], "little") / 10,
            "switch_reg": data[11],
            "ts": time.time(),
        }
        return self.master

    def winmon(self, cmd: int, args: bytes = b"", replies=1, timeout=0.5):
        """Sends an X/Y/Z Winmon command. Returns a list of (code, value) or [] if there is no reply."""
        allowed = ALLOWED_WINMON.get(cmd, set())
        if allowed is not None and args not in allowed:
            raise NotAllowed(f"Winmon 0x{cmd:02X} {args.hex(' ')} not allowed")
        return self._winmon(cmd, args, replies, timeout)

    def _winmon(self, cmd: int, args: bytes, replies, timeout):
        """Unchecked send. Called only from winmon() and write_settings()."""
        with self.lock:
            L = LETTERS[self.letter_i]
            self.letter_i = (self.letter_i + 1) % 3
            self._drain()
            self._send(bytes([L, cmd]) + args)
            out = []
            if replies == 0:
                return out
            end = time.time() + timeout
            while time.time() < end and len(out) < replies:
                try:
                    fr = self.rxq.get(timeout=max(0.01, end - time.time()))
                except queue.Empty:
                    break
                if fr[1] != 0xFF or fr[2] != L:
                    continue
                p = fr[3:-1]
                for i in range(0, len(p) - 2, 3):
                    out.append((p[i], p[i + 1] | p[i + 2] << 8))
                if out and out[0][0] in (0x8E, 0x89) and out[0][1] == 0:  # Sc=0: not supported, no further replies
                    break
            return out

    # ------------------------------------------------------------ high level
    def connect(self):
        """Replays the connection sequence: V, R (MK2 reset), S with the NO-SEND flag."""
        self.mk2_cmd(b"V", 0x56)
        self.mk2_cmd(b"R", 0x56, timeout=1.0)  # reset; the MK2 sends a V frame when ready
        # NOTE 2.11.2026: the "reply" to R can be a spontaneous V frame already in the buffer -> S was sent too
        # early, in the middle of the MK2 reset, and got no acknowledgement. Wait and retry.
        time.sleep(0.8)
        ok = None
        for _attempt in range(4):
            ok = self.mk2_cmd(bytes.fromhex("53 04 00 ff 01 10 00 04"), 0x53)
            if ok is not None:
                break
            time.sleep(0.7)
        if ok is None:
            raise RuntimeError("MK2 did not acknowledge the S command (4 attempts)")
        for cmd, code in ((0x05, 0x82), (0x06, 0x83)):
            for rc, val in self.winmon(cmd):
                if rc == code:
                    self.fw[rc] = val
        for rid in range(10):
            res = self.winmon(0x36, bytes([rid, 0]), replies=2)
            sc = next((v for c, v in res if c == 0x8E), None)
            off = next((v for c, v in res if c == 0x8F), 0)
            if sc is not None:
                self.ram_info[rid] = (sc, off)
        # NOTE: 'F' 05 (MasterMultiLED) tested on 29.10.2026 -> this device does not reply. Not queried.

    def conv(self, rid, raw):
        sc, off = self.ram_info.get(rid, (None, 0))
        if sc is None or d.ram_scale(sc) is None:
            return None
        scale, signed = d.ram_scale(sc)
        x = d.s16(raw) if signed else raw
        v = scale * (x + d.s16(off))
        if rid in (7, 8):
            v = d.period_to_hz(v) or 0.0
        return v

    def read_settings(self, progress=None):
        """Reads the settings info (0x35) + values (0x31). Result: self.settings[id] = dict.
        progress(i, n) is called after each setting."""
        keys = {0x89: "sc", 0x8A: "offset", 0x8B: "default", 0x8C: "min", 0x8D: "max"}
        result = {}
        ids = list(SETTING_IDS)
        for i, sid in enumerate(ids):
            info = {}
            for rc, val in self.winmon(0x35, bytes([sid & 0xFF, sid >> 8]), replies=5, timeout=0.8):
                if rc in keys:
                    info[keys[rc]] = val
            if info.get("sc"):
                res = self.winmon(0x31, bytes([sid & 0xFF, sid >> 8]))
                for rc, val in res:
                    if rc == 0x86:
                        info["raw"] = val
                    elif rc == 0x91:
                        info["raw"] = None
            result[sid] = info
            if progress:
                progress(i + 1, len(ids))
        self.settings = result
        self.settings_ts = time.time()
        self.settings_dirty = False       # the view now shows what the device really holds
        return result

    # ------------------------------------------------------------ safety gates
    def firmware_block(self):
        """None if the device firmware allows writing/reset/control, otherwise the reason (read-only)."""
        fw = device_fw(getattr(self, "fw", None) or {})
        if fw is None:
            return "device firmware not read yet: read-only"
        if fw in WRITE_ALLOWED_FW or self.allow_unknown_firmware:
            return None
        return (f"device firmware {fw} has not been verified with this program "
                f"(verified: {', '.join(str(v) for v in sorted(WRITE_ALLOWED_FW))}): read-only")

    def control_block(self):
        """None if knob/switch commands may be sent, otherwise the reason."""
        if not self.control:
            return "control is off (monitor only): enable control first"
        return self.firmware_block()

    def enable_control(self, on=True):
        """Monitor only <-> control.

        Enabling: if no limit has been chosen yet, the input current limiter starts at the CONSERVATIVE minimum
        (knob_min, default 2.0 A); the poll loop sends it within one cycle and the user raises it on purpose.
        The switch is not touched. Disabling forgets the desired knob/switch, so nothing is repeated."""
        self.control = bool(on)
        if self.control:
            if self.desired_knob is None:
                self.desired_knob = self.knob_min
        else:
            self.desired_knob = None
            self.desired_switch = None

    # ------------------------------------------------------------ writing
    @staticmethod
    def display_value(info, raw):
        sc = d.setting_scale(info.get("sc", 0))
        return None if sc is None or raw is None else sc * (raw + d.s16(info.get("offset", 0)))

    @staticmethod
    def raw_from_display(info, value):
        sc = d.setting_scale(info.get("sc", 0))
        if sc is None:
            raise ValueError("setting not supported")
        return int(round(value / sc - d.s16(info.get("offset", 0))))

    @staticmethod
    def merge_flags(sid, device_raw, wanted_raw):
        """Flag read-modify-write: only the FLAG_WRITE_MASK bits are taken from wanted_raw,
        the others from the current device value. Flags0: bit 7 = NOT bit 3 when bit 3 changes."""
        mask = FLAG_WRITE_MASK[sid]
        if sid == 0:
            mask &= ~FLAG0_UPS_INV  # bit 7 is not set directly
        new = (device_raw & ~mask & 0xFFFF) | (wanted_raw & mask)
        if sid == 0 and (new ^ device_raw) & FLAG0_UPS:
            new = (new & ~FLAG0_UPS_INV) | (0 if new & FLAG0_UPS else FLAG0_UPS_INV)
        return new

    def validate_write(self, changes):
        """Validates {id: raw}. Returns a list of errors (empty = OK)."""
        errs = []
        if not self.settings:
            return ["settings have not been read (Read settings first)"]
        block = self.firmware_block()
        if block:
            return [block]
        if self.settings_dirty:
            return ["the previous write stopped part-way: Read settings again before writing"]
        for sid, raw in changes.items():
            info = self.settings.get(sid, {})
            name = d.SETTING_NAMES.get(sid, sid)
            if sid not in WRITE_ORDER or not info.get("sc"):
                errs.append(f"{sid} {name}: not writable")
                continue
            if not isinstance(raw, int) or not 0 <= raw <= 0xFFFF:
                errs.append(f"{sid} {name}: raw {raw!r} is not 16-bit")
                continue
            if sid in FLAG_IDS:
                continue
            lo, hi = info.get("min"), info.get("max")
            if lo is None or hi is None or not lo <= raw <= hi:
                errs.append(f"{sid} {name}: raw {raw} outside device limits {lo}..{hi}")
                continue
            limits = dict(self.hard_limits)
            if self.max_charge_current_a is not None:      # mk2.toml [device] max_charge_current_a
                limits[4] = (0, self.max_charge_current_a)
            if sid in limits:
                v = self.display_value(info, raw)
                a, b = limits[sid]
                if not a <= v <= b:
                    errs.append(f"{sid} {name}: {v:g} outside safety limits {a}..{b}")
        return errs

    def _write_one(self, sid, raw):
        """0x33 <id> (no reply) + 0x34 <data> (reply 0x88). The pair is sent inside the lock."""
        with self.lock:
            self._winmon(0x33, bytes([sid & 0xFF, sid >> 8]), 0, 0)
            time.sleep(WRITE_GAP_S)
            res = self._winmon(0x34, bytes([raw & 0xFF, raw >> 8]), 1, 0.8)
        return any(rc == 0x88 for rc, _ in res)

    def write_settings(self, changes, progress=None):
        """Writes {id: raw} in the captured write order and reads each value back.
        Returns a list of (id, old, written, read_back, ok) when every write succeeded.

        Stops at the FIRST failure (no acknowledgement, read-back differs, or a link error such as an unplugged
        adapter): the
        remaining settings are not written, settings_dirty is set (if anything was written) and WriteAborted is
        raised with the results so far. While settings_dirty is set, validate_write refuses until the settings
        have been read again (read_settings clears it)."""
        errs = self.validate_write(changes)
        if errs:
            raise NotAllowed("; ".join(errs))
        ids = [sid for sid in WRITE_ORDER if sid in changes]
        out = []

        def abort(i, sid, why):
            if out:
                self.settings_dirty = True
            name = d.SETTING_NAMES.get(sid, sid)
            raise WriteAborted(f"writing stopped at {sid} {name}: {why}; "
                               f"{len(ids) - i - 1} remaining settings were not written",
                               out, sid, ids[i + 1:])

        for i, sid in enumerate(ids):
            info = self.settings[sid]
            old = info.get("raw")
            raw = changes[sid]
            if sid in FLAG_IDS:  # info + fresh value before writing flags
                self._winmon(0x35, bytes([sid, 0]), 5, 0.8)
                cur = [v for rc, v in self._winmon(0x31, bytes([sid, 0]), 1, 0.5) if rc == 0x86]
                if not cur:
                    abort(i - 1, sid, "reading the current flags failed, not written")   # this one is not written either
                old = cur[0]
                raw = self.merge_flags(sid, old, raw)
            try:
                ack = self._write_one(sid, raw)
                back = [v for rc, v in self.winmon(0x31, bytes([sid, 0])) if rc == 0x86]
            except Exception as e:  # noqa: BLE001 - link lost mid-write (USB-RS232 unplugged, port error)
                self.settings_dirty = True            # this setting may have reached the device
                out.append((sid, old, raw, None, False))
                abort(i, sid, f"link error: {e}")
            back = back[0] if back else None
            if back is not None:
                info["raw"] = back
            ok = bool(ack) and back == raw
            out.append((sid, old, raw, back, ok))
            if progress:
                progress(i + 1, len(ids))
            if not ok:
                self.settings_dirty = True            # this setting was sent: the device may have changed
                abort(i, sid, "no write acknowledgement" if not ack else
                      ("no read-back" if back is None else f"read back {back}, wrote {raw}"))
        self.settings_dirty = False
        return out

    def reset(self):
        """Device reset: 'S' 0x50 -> RESET_FRAME (raw, not MK2 format)
        -> version again -> switch and knob state again (sent right away in the captured traffic; only when control
        is on). Refused on unverified firmware. The caller decides whether a reset is sensible (settings_dirty)."""
        block = self.firmware_block()
        if block:
            raise NotAllowed(f"reset: {block}")
        with self.lock:
            ok = self.mk2_cmd(S_RESET, 0x53)
            time.sleep(0.1)
            self._log("TX", d.RESET_FRAME)
            self.ser.write(d.RESET_FRAME)
            time.sleep(1.0)
        for cmd, code in ((0x05, 0x82), (0x06, 0x83)):
            for rc, val in self.winmon(cmd):
                if rc == code:
                    self.fw[rc] = val
        if self.control_block() is None:
            if self.knob:
                self.set_knob(self.knob[0])
            if self.switch:
                self.set_switch(self.switch[0])
        return ok is not None

    def set_knob(self, value: int):
        """Current limit knob: Winmon 0x04 <value>, value = A * 10. No reply (observed). Needs control on."""
        block = self.control_block()
        if block:
            raise NotAllowed(f"knob: {block}")
        if not self.knob_min <= value <= self.knob_max:
            raise NotAllowed(f"knob value {value} outside range {self.knob_min}..{self.knob_max}")
        self.desired_knob = value
        self.winmon(0x04, bytes([value]), replies=0)
        self.knob = (value, time.time())

    def set_switch(self, state: int):
        """Switch: Winmon 0x03 <state>. Needs control on."""
        block = self.control_block()
        if block:
            raise NotAllowed(f"switch: {block}")
        if state not in SWITCH_STATES:
            raise NotAllowed(f"switch state {state}")
        if self.knob is None:
            # never switch (e.g. to charger) before a known current limit has been sent on this connection
            self.set_knob(self.desired_knob if self.desired_knob is not None else self.knob_min)
        self.desired_switch = state
        self.winmon(0x03, bytes([state]), replies=0)
        self.switch = (state, time.time())

    def poll_forever(self):
        try:
            self.connect()
        except Exception as e:  # noqa: BLE001 - show the error in the panel
            self.error = f"connection: {e}"
            return
        try:
            self._poll_loop()
        except (serial.SerialException, ValueError, OSError) as e:
            # close() in the middle of a cycle (port/log closed) -> quiet end; otherwise show the error
            if not self.stop.is_set():
                self.error = f"connection lost: {e}"

    def _poll_loop(self):
        self.reassert_controls()   # known switch and limit right at the start, before the first measurement
        n = 0
        while not self.stop.is_set():
            t_cycle = time.time()
            n += 1
            for rid in RAM_IDS:
                for rc, val in self.winmon(0x30, bytes([rid])):
                    if rc == 0x85:
                        self.values[rid] = (val, self.conv(rid, val), time.time())
            for rc, val in self.winmon(0x02):
                if rc == 0x81:
                    self.led = (val & 0xFF, val >> 8, time.time())
            self.reassert_controls()
            self.cycle_s = time.time() - t_cycle

    def reassert_controls(self, now=None):
        """Repeats the desired switch and knob every repeat_s (RAM level, no EEPROM).
        Sends nothing in monitor only mode or on unverified firmware (no exception: called from the poll loop)."""
        if self.control_block() is not None:
            return
        now = time.time() if now is None else now
        # the limit first, then the switch: charging never starts without a known limit
        if self.desired_knob is not None and (self.knob is None or now - self.knob[1] > self.repeat_s):
            self.set_knob(self.desired_knob)
        if self.desired_switch and (self.switch is None or now - self.switch[1] > self.repeat_s):
            self.set_switch(self.desired_switch)

    def start(self):
        self._poll_thread = threading.Thread(target=self.poll_forever, daemon=True)
        self._poll_thread.start()

    def close(self):
        self.stop.set()
        th = getattr(self, "_poll_thread", None)
        if th and th is not threading.current_thread():
            th.join(3.0)              # cycle in progress -> wait so the port/log is not closed in the middle of a write
        else:
            time.sleep(0.2)
        self.ser.close()
        self.logf.close()
