"""PhoenixConfig panel: real-time measurements, Digital Multi Control (LEDs, switch, knob) and settings.

Passive mode (--log) only reads the capture log of mk2_proxy.py (nothing is sent to the COM port).
Usage:  python mk2_panel.py --log [capture_XXXX.log]     (default: the newest capture_*.log)

Protocol findings: see docs/protocol.md.
"""
import glob
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import mk2_decode as d
import mk2_settings_view as vv

_STATE = {"umains": None}  # refresh() updates it; the Freq. In row uses it
# The mains frequency is shown only when UMains >= 60.00 V (raw >= 6000) = half of the nominal 120 V.
# Measured with the emulator 30.10.2026: 59.99 V -> '---', 60.00 V -> 60.0 Hz.
FREQ_IN_MIN_UMAINS = 60.0


# The last sent knob and switch are saved: the device does not report them, so with control on direct mode sets
# them right at start-up (mk2_link.desired_knob/desired_switch). The first time the knob is 2.0 A (minimum).
# Monitor only (default, mk2.toml [control] enabled = false) sends neither.
# The helper functions are in mk2_state.py (no tkinter, also used by mk2_gx.py).
from mk2_state import (PANEL_STATE_FILE, load_panel_state, save_panel_state,  # noqa: E402,F401
                       startup_knob, startup_switch)


APP_NAME = "PhoenixConfig"
APP_SUBTITLE = "Phoenix MultiPlus - VE.9bit MK2"
# PhoenixConfig.png/.ico: converted from the source image (white background made transparent), cropped square
_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
ICON_FILE = os.path.join(_ICON_DIR, "PhoenixConfig.png")
ICON_ICO = os.path.join(_ICON_DIR, "PhoenixConfig.ico")


def load_app_icons(root):
    """Window icon (title bar, taskbar) and a small icon for the top bar.
    Returns (big, small) PhotoImage objects or (None, None) if there is no image. Tk 8.6 reads PNG itself."""
    if not os.path.exists(ICON_FILE):
        return None, None
    try:
        img = tk.PhotoImage(master=root, file=ICON_FILE)          # 561 x 576
        big = img.subsample(max(1, img.width() // 64))            # about 64 px
        small = img.subsample(max(1, img.width() // 32))          # about 32 px
        bar = img.subsample(max(1, img.width() // 26))            # top bar, about 26 px
        root.iconphoto(True, big, small)                          # True: also dialogs and child windows
        if sys.platform == "win32" and os.path.exists(ICON_ICO):
            try:
                root.iconbitmap(default=ICON_ICO)    # Windows: exact taskbar icon (16..256 px)
            except tk.TclError:
                pass
        root._app_icon_refs = (img, big, small, bar)              # keep the reference (garbage collector)
        return big, bar
    except tk.TclError:
        return None, None


def _windows_app_id():
    """Windows: own taskbar group and icon (otherwise the python.exe icon is shown)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        except (AttributeError, OSError):
            pass


def knob_inactive(switch):
    """The knob has no effect in inverter only (2) and off (4): the AC input relay is open."""
    return switch in (2, 4)


def knob_limit_text(real, approx, switch, imains=None):
    """Limit text under the DMC knob according to the switch state: the first line is the limit value
    (drawn large), the rest is the explanation (small).

    The limit applies to the WHOLE mains current (load + charge): in on mode the charger reduces charging
    when the load grows, and IMains does not exceed the limit (verified 7.11.2026: knob 3.0, estimate 5.4 A,
    IMains 5.5 A, load 2.1 A, Idc 12 A). The calibration was made in charger only mode."""
    base = f"{approx} {real:.1f} A"
    if knob_inactive(switch):
        # the estimate is shown even when the knob has no effect: the limit can be set in advance
        return base + "\napplies in: on / charger only"
    if switch == 3:
        note = "grid limit (load + charge)"
    elif switch == 1:
        note = "grid limit (charger only)"
    else:
        note = "grid limit (charger only calibration)"
    # only when drawing from the mains (IMains > 0.3 A) but clearly staying below the limit (absorption/float)
    if imains is not None and 0.3 < imains < 0.9 * real:
        note += "\ncharger limiting"
    return base + "\n" + note


def _umains_low():
    u = _STATE["umains"]
    return u is not None and u < FREQ_IN_MIN_UMAINS


# (title, RAM ID, unit, formatter) - None = empty row
ROWS = [
    ("Freq. Out", 7, "Hz", lambda v, raw: f"{v:.1f}"),
    ("UOut", 2, "V", lambda v, raw: f"{v:.0f}"),
    # Negative IOut is always shown as 0.0 (emulator: -1.5 and -10 A -> 0.0).
    # The raw column still shows the real signed value.
    ("IOut", 9, "A", lambda v, raw: f"{max(v, 0.0):.1f}"),
    None,
    # '---' when there is no measurement (raw 0) or UMains < 60.00 V (display rule, measured with the emulator).
    # The device keeps the last period in memory when the mains disappears (seen when a GFCI tripped).
    ("Freq. In", 8, "Hz", lambda v, raw: "- - -" if raw == 0 or _umains_low() else f"{v:.1f}"),
    ("UMains", 0, "V", lambda v, raw: f"{v:.0f}"),
    ("IMains", 1, "A", lambda v, raw: f"{v:.1f}"),
    None,
    ("Udc", 4, "V", lambda v, raw: f"{v:.1f}"),
    ("Udc ripple", 6, "V", lambda v, raw: f"{v:.1f}"),
    ("Idc", 5, "A", lambda v, raw: f"{v:.0f}"),
]

# LED: name -> (bit of the 0x81 low byte or None = unknown, colour when lit)
# NOTE: the 'L' table of the PDF does NOT apply to this pre-VE.Bus device (0x81 reply).
# Verified against the Digital Multi Control view of the official configuration software 30.10.2026:
#   LED word = lo | hi << 8 (16 bits)
#   bit 4 = inverter on, 5 = mains on, 6 = bulk, 7 = absorption (Udc 28.42 V),
#   bit 8 (hi bit 0) = float (Udc 27.00 V) - real device 30.10.2026
#   bit 0 = overload, 1 = low battery, 2 = temperature - mk2_emu.py + Digital Multi Control view 30.10.2026
#   bits 3, 9-15: no effect on that view; bit 15 is always 1 on the real device
LED_MAP = {
    "inverter on": (4, "#3fe0ff"),
    "overload": (0, "#ff3030"),
    "low battery": (1, "#ff3030"),
    "temperature": (2, "#ff3030"),
    "mains on": (5, "#3fe0ff"),
    "bulk": (6, "#f5e03a"),
    "absorption": (7, "#f5e03a"),
    "float": (8, "#f5e03a"),
}
LED_ALWAYS_ON = {15}  # hi byte bit 7: has always been 1
LED_OFF = {"#e8f0ff": "#606870", "#f5e03a": "#5a5520", "#3fe0ff": "#4a5a66", "#ff3030": "#5a1414",
           "#22c55e": "#2f4a3a"}
# DMC panel layout, classic: left column, right column
LED_LEFT = ["inverter on", "overload", "low battery", "temperature"]
LED_RIGHT = ["mains on", "bulk", "absorption", "float"]
# Physical Multi Control panel (this firmware): 'charger' on the left, 'inverter' on the right;
# the LEDs of the top row are green. Default layout; the 'toggle layout' box switches to the classic layout above.
LED_CHARGER = ["mains on", "bulk", "absorption", "float"]
LED_INVERTER = ["inverter on", "overload", "low battery", "temperature"]
LED_PHYSICAL_COLORS = {"mains on": "#22c55e", "inverter on": "#22c55e"}
DMC_LAYOUTS = ("physical", "classic")
# The DMC is a low card that stretches with the width: LEDs + switch on the left,
# the knob display and the knob share the rest of the width. The width follows the <Configure> event.
DMC_W, DMC_H = 760, 196
DMC_MIN_W = 600
KNOWN_LED_BITS = {b for b, _ in LED_MAP.values() if b is not None}
# Real current of the knob: knob_cal.py + knob_calibration.json (piecewise linear calibration).
# Background: the 16 A scale of the panel -> 30 A device (Victron pre-VE.Bus: 30/16 = 1.875); measured ratio
# 1.52 (knob 1.0) ... 1.85 (knob >= 5.0), i.e. non-linear at the low end.
SWITCH = {1: "charger only", 2: "inverter only", 3: "on", 4: "off"}

# Units of the settings (PDF 7.3.10: V, A, min; refined from the captured values)
SETTING_UNITS = {
    2: "V", 3: "V", 4: "A", 5: "V", 6: "A", 7: "min", 8: "min", 9: "min",
    11: "V", 12: "V", 16: "A", 17: "V", 18: "V", 28: "A", 29: "V", 30: "V",
    **{i: "min" for i in list(range(19, 28)) + list(range(31, 44))},
    44: "V", 45: "V", 46: "V", 47: "V", 48: "x", 49: "A", 50: "A", 51: "A",
}
FLAG_SETTINGS = {0: 0, 1: 16, 60: 32, 61: 48}  # setting ID -> first flag bit

# Display names of the settings tabs (reference screenshots 29.10.2026). Value f(v, s): v = this setting scaled,
# s = all settings {id: scaled}. "?" = inferred, not verified.
SETTINGS_UI_NAMES = {
    5: ("Inverter", "Inverter output voltage", lambda v, s: f"{v:.0f} V"),
    6: ("General", "AC input current limit", lambda v, s: f"{v:.1f} A"),
    13: ("General", "Number of slaves", lambda v, s: f"{v:.0f}"),
    14: ("General", "2-3 phase mode", lambda v, s: {0: "3 phase", 1: "Split phase 180°", 2: "Two leg 3 phase 120°"}.get(int(v), v)),
    15: ("Virtual switch", "Usage", lambda v, s: {0: "Do not use VS", 1: "drive multifunctional relay",
                                                  2: "ignore AC input"}.get(int(v), v)),
    44: ("Grid", "AC low disconnect", lambda v, s: f"{v:.0f} V"),
    45: ("Grid", "AC low connect (=44+45)", lambda v, s: f"{s[44] + v:.0f} V" if 44 in s else "-"),
    46: ("Grid", "AC high disconnect", lambda v, s: f"{v:.0f} V"),
    47: ("Grid", "AC high connect (=46-47)", lambda v, s: f"{s[46] - v:.0f} V" if 46 in s else "-"),
    11: ("Inverter", "DC input low shut-down", lambda v, s: f"{v:.2f} V"),
    12: ("Inverter", "DC input low restart (=11+12)", lambda v, s: f"{s[11] + v:.2f} V" if 11 in s else "-"),
    48: ("Inverter", "Assist current boost factor", lambda v, s: f"{v:.1f}"),
    2: ("Charger", "Absorption voltage", lambda v, s: f"{v:.2f} V"),
    3: ("Charger", "Float voltage", lambda v, s: f"{v:.2f} V"),
    4: ("Charger", "Charge current", lambda v, s: f"{v:.0f} A"),
    7: ("Charger", "Repeated absorption time", lambda v, s: f"{v / 60:.2f} Hr"),
    8: ("Charger", "Repeated absorption interval", lambda v, s: f"{v / 1440:.2f} Days"),
    9: ("Charger", "Absorption time", lambda v, s: f"{v / 60:.0f} Hr"),
    10: ("Charger", "Charge curve ?", lambda v, s: {1: "Fixed"}.get(int(v), f"? ({v:.0f})")),
}
# flag bit -> (tab, label, inverted?)  inverted: box checked = bit 0
SETTINGS_UI_FLAGS = {
    0: ("General", "Enable 2-3 phase", False),
    1: ("General", "Leader", False),
    2: ("General", "60Hz", False),
    3: ("Grid", "UPS function", True),
    4: ("Charger", "Stop after excessive bulk", True),
    5: ("Inverter", "PowerAssist", False),
    6: ("Charger", "Enable charger", True),
    8: ("Inverter", "enable AES", True),
    11: ("Charger", "Storage mode", False),  # verified with a write test 29.10.2026
    13: ("Inverter", "Ground relay", True),
    14: ("Charger", "Weak AC input", False),
    27: ("Grid", "Accept wide input frequency", False),
    28: ("General", "Dynamic current limiter", False),
}

# Writable settings (write order) and the flag bits shown in the dialog.
WRITE_IDS = (0, 1, 6, 13, 14, 44, 46, 5, 11, 48, 10, 2, 3, 4, 7, 8, 9, 15, 16, 18, 17, 23, 22, 21, 20,
             24, 25, 26, 27, 19, 28, 30, 29, 34, 35, 32, 33, 31, 36, 39, 40, 41, 42, 37, 38, 43, 45, 47, 12)
FLAG_EDIT_MASK = {0: 0x697F, 1: 0x1FFF}  # Flags0 without bits 7 (pair), 9, 10, 12, 15
# Settings confirmed on the real device to take effect immediately without a reset (31.10.2026):
#   5 UInvSetpoint: UOut 117 -> 120 V while the inverter is running
#   4 IBatBulk:     Idc 15 -> 20 A during charging
# No reset question is asked for these. For the others it is asked until they have been tested (add the ID here after a test).
#   11 UBatLowLimit: the pre-warning (low battery LED blinks, limit = restart = 11+12) followed immediately 1.11.2026
#   15 vsUsage 0 -> 2 (ignore AC): the relay opened immediately, switch in on mode 1.11.2026
#   18 vsonUBatLow 26.80 V + 21 delay 0 s: VS ON -> the relay closed immediately, load on mains 1.11.2026
LIVE_VERIFIED = {4, 5, 11, 15, 18, 21}
# Flag bits (0..31) separately: Flags0/1 contain several different settings. A bit is added when verified.
#   6 Enable charger: on/off while running, load on mains, 2 times, no interruptions 2.11.2026
LIVE_VERIFIED_FLAGS = {6}


def untested_for_reset(link, before, changes):
    """Returns the list of changes whose effect without a reset has not been verified."""
    out = []
    for sid in sorted(changes):
        if sid in FLAG_SETTINGS:
            old = before.get(sid, {}).get("raw")
            if old is None:
                out.append(f"{sid} {d.SETTING_NAMES.get(sid, '')}")
                continue
            diff = old ^ link.merge_flags(sid, old, changes[sid])
            for b in range(16):
                n = FLAG_SETTINGS[sid] + b
                if diff >> b & 1 and n not in LIVE_VERIFIED_FLAGS and n != 7:  # 7 = pair of bit 3
                    label = SETTINGS_UI_FLAGS[n][1] if n in SETTINGS_UI_FLAGS else d.FLAG_NAMES.get(n, "?")
                    out.append(f"flag {n} {label}")
        elif sid not in LIVE_VERIFIED:
            out.append(f"{sid} {d.SETTING_NAMES.get(sid, '')}")
    return out
# Preview warning limits (24 V LiFePO4; LiFePO4 preset 28.40 / 27.00 V)
LIFEPO4_MAX_ABS_V = 28.6
LIFEPO4_MAX_FLOAT_V = 27.6


def write_aborted_text(e, limit=12):
    """Message for a write chain that stopped at the first failure (mk2_link.WriteAborted). No reset is offered."""
    ok = [x for x in e.results if x[4]]
    lines = [f"Writing STOPPED at {e.failed_id} {d.SETTING_NAMES.get(e.failed_id, '')}:", str(e), ""]
    lines.append(f"Written and verified ({len(ok)}): " +
                 (", ".join(f"{x[0]} {d.SETTING_NAMES.get(x[0], '')}" for x in ok[:limit]) or "none")
                 + (" ..." if len(ok) > limit else ""))
    bad = [x for x in e.results if not x[4]]
    for sid, _old, wrote, back, _ok in bad:
        lines.append(f"FAILED: {sid} {d.SETTING_NAMES.get(sid, '')}: wrote {wrote}, read back {back}")
    lines.append(f"Not written ({len(e.not_written)}): " +
                 (", ".join(str(s) for s in e.not_written[:limit]) or "none")
                 + (" ..." if len(e.not_written) > limit else ""))
    lines += ["", "The device may now hold a MIX of old and new settings. It was NOT reset.",
              "Read the settings again, check them, and write the remaining changes before any reset."]
    return "\n".join(lines)


def is_vs_seconds(info):
    """VS delays (ID 19-35, 38-42): the device reports minutes (scale 1/60, offset -1) -> seconds = raw - 1.
    Shown in seconds; raw 0 = -1 s = condition disabled."""
    sc = d.setting_scale(info.get("sc", 0))
    return sc is not None and sc < 0.02 and d.s16(info.get("offset", 0)) == -1


def dlg_display(link, info, raw):
    if raw is None:
        return None
    return raw - 1 if is_vs_seconds(info) else link.display_value(info, raw)


def dlg_raw(link, info, value):
    return int(round(value)) + 1 if is_vs_seconds(info) else link.raw_from_display(info, value)


def dlg_unit(sid, info):
    return "s" if is_vs_seconds(info) else SETTING_UNITS.get(sid, "")


def compute_changes(link, s, edits, wanted_flags, send_all=False):
    """edits = {id: user text in display units}, wanted_flags = {0|1: wanted word}.
    Returns ({id: raw}, errors). Text that has not been changed is not interpreted again (rounding)."""
    changes, errs = {}, []
    for sid, text in edits.items():
        info = s[sid]
        cur_raw = info.get("raw")
        cur_txt = "" if cur_raw is None else d.fmt(dlg_display(link, info, cur_raw))
        text = text.strip().replace(",", ".")
        if text == cur_txt:
            raw = cur_raw
        else:
            try:
                value = float(text)
            except ValueError:
                errs.append(f"{sid} {d.SETTING_NAMES.get(sid)}: '{text}' is not a number")
                continue
            raw = dlg_raw(link, info, value)
            step = 1 if is_vs_seconds(info) else abs(d.setting_scale(info["sc"]))
            if abs(dlg_display(link, info, raw) - value) > 0.1 * step + 1e-9:
                errs.append(f"{sid} {d.SETTING_NAMES.get(sid)}: {text} does not fit the step {d.fmt(step)}")
                continue
        if raw is None:
            continue
        if send_all or raw != cur_raw:
            changes[sid] = raw
    for sid, word in wanted_flags.items():
        if send_all or word != s[sid].get("raw"):
            changes[sid] = word
    return changes, errs


# ---------------------------------------------------------------- settings file (backup)
# Raw values + scaling + device firmware. Loading goes through the normal check -> preview ->
# write chain (validate_write, safety limits, protected flag bits are kept from the device value).
SETTINGS_FILE_FORMAT = "phoenixconfig-settings"
SETTINGS_FILE_VERSION = 1


def settings_file_data(settings, fw, app=APP_NAME, now=None):
    """Read settings (link.settings) -> JSON-compatible dict. Only settings that have a value."""
    import mk2_link
    out = {}
    for sid in sorted(settings):
        info = settings[sid]
        if not info.get("sc") or info.get("raw") is None:
            continue
        entry = {"name": d.SETTING_NAMES.get(sid, f"setting_{sid}"), "raw": info["raw"],
                 "sc": info["sc"], "offset": info.get("offset", 0)}
        if sid in FLAG_SETTINGS:
            entry["hex"] = f"0x{info['raw']:04X}"
        else:
            v = mk2_link.MK2.display_value(info, info["raw"])
            if v is not None:
                entry["value"] = round(v, 4)
                entry["unit"] = SETTING_UNITS.get(sid, "")
        out[str(sid)] = entry
    return {"format": SETTINGS_FILE_FORMAT, "version": SETTINGS_FILE_VERSION, "app": app,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "device": {"firmware": mk2_link.device_fw(fw)}, "settings": out}


def changes_from_file_data(data, settings, fw):
    """File contents -> ({id: raw} of the changed ones, errors). Writes nothing.
    Refuses if the format, firmware or scaling of a setting does not match the device."""
    import mk2_link
    if not isinstance(data, dict) or data.get("format") != SETTINGS_FILE_FORMAT:
        return {}, ["not a PhoenixConfig settings file"]
    if data.get("version") != SETTINGS_FILE_VERSION:
        return {}, [f"unsupported file version {data.get('version')!r}"]
    if not settings:
        return {}, ["read the settings from the device first (Read settings)"]
    dev_fw = mk2_link.device_fw(fw)
    file_fw = (data.get("device") or {}).get("firmware")
    if dev_fw is None or file_fw != dev_fw:
        return {}, [f"firmware differs: file {file_fw}, device {dev_fw} - not loaded"]
    entries = data.get("settings")
    if not isinstance(entries, dict):
        return {}, ["file has no settings"]
    changes, errs = {}, []
    for key, e in entries.items():
        try:
            sid = int(key)
            raw = e["raw"]
        except (ValueError, TypeError, KeyError):
            errs.append(f"bad entry {key!r}")
            continue
        if sid not in WRITE_IDS:
            continue                       # not writable (only informational in the file)
        info = settings.get(sid) or {}
        name = d.SETTING_NAMES.get(sid, sid)
        if not info.get("sc") or info.get("raw") is None:
            errs.append(f"{sid} {name}: not available on the device")
            continue
        if e.get("sc") != info["sc"] or e.get("offset", 0) != info.get("offset", 0):
            errs.append(f"{sid} {name}: scaling in the file differs from the device")
            continue
        if not isinstance(raw, int) or isinstance(raw, bool):
            errs.append(f"{sid} {name}: raw {raw!r} is not an integer")
            continue
        cur = info["raw"]
        if sid in FLAG_SETTINGS:
            # protected bits (9, 10, 12, 15) always come from the device: a change only if the allowed bits differ
            if mk2_link.MK2.merge_flags(sid, cur, raw) != cur:
                changes[sid] = raw
        elif raw != cur:
            changes[sid] = raw
    return changes, errs


# ---------------------------------------------------------------- connection management (direct mode)
def _port_sort_key(p):
    import re
    m = re.search(r"(\d+)$", p.device)
    return (0 if getattr(p, "vid", None) == 0x0403 else 1, p.device.rstrip("0123456789"),
            int(m.group(1)) if m else 0)


def list_serial_ports():
    """[(port, visible text)]: FTDI adapters (MK2-USB, US232R) first, then the others; the internal
    emulator last."""
    import mk2_link
    try:
        from serial.tools import list_ports
        ports = sorted(list_ports.comports(), key=_port_sort_key)
    except Exception:  # noqa: BLE001 - listing must not crash the panel
        ports = []
    out = []
    for p in ports:
        desc = (p.description or "").replace(f"({p.device})", "").strip()
        out.append((p.device, f"{p.device} - {desc}" if desc and desc != p.device else p.device))
    out.append((mk2_link.EMULATOR_PORT, "emulator (built-in, no device)"))
    return out


EMULATOR_CLIENT_ID = "mk2emu"
EMULATOR_DEVICE_NAME = "EMULATOR (PhoenixConfig)"
# Adjustable measurements of the emulator: (RAM ID, title, unit, min, max, step)
EMU_CONTROLS = (
    (4, "Battery voltage", "V", 18.0, 33.0, 0.1),
    (5, "Battery current (+ charge)", "A", -120.0, 70.0, 1.0),
    (9, "AC out current", "A", 0.0, 30.0, 0.1),
    (0, "AC in voltage", "V", 0.0, 140.0, 1.0),
    (1, "AC in current", "A", 0.0, 30.0, 0.1),
)


class Tooltip:
    """Tooltip shown after a delay under the mouse. text can be a string or a function (read every time)."""

    def __init__(self, widget, text, delay_ms=450):
        self.widget, self.text, self.delay = widget, text, delay_ms
        self.tip = self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, justify="left", bg="#fffbea", fg="#1f2937", relief="solid", bd=1,
                 font=("Consolas", 9), padx=8, pady=6).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        x = min(x, self.widget.winfo_screenwidth() - tip.winfo_reqwidth() - 8)   # not past the right edge
        tip.wm_geometry(f"+{max(0, x)}+{y}")
        self.tip = tip

    def _hide(self, _e=None):
        self._cancel()
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class Disconnected:
    """Placeholder for the link without a connection: same public state as mk2_link.MK2, controls refuse."""

    def __init__(self, limits=None):
        import mk2_link
        self.values, self.ram_info, self.fw = {}, {}, {}
        self.led = self.switch = self.knob = self.master = None
        self.mk2_version = self.mk2_mode = None
        self.settings = None
        self.cycle_s = None
        self.error = None
        lim = limits or {}
        self.knob_min = max(mk2_link.KNOB_MIN, int(lim.get("knob_min", mk2_link.KNOB_MIN)))
        self.knob_max = min(mk2_link.KNOB_MAX, int(lim.get("knob_max", mk2_link.KNOB_MAX)))
        self.display_value = mk2_link.MK2.display_value
        self.raw_from_display = mk2_link.MK2.raw_from_display
        self.merge_flags = mk2_link.MK2.merge_flags

    def _no(self, *_a, **_k):
        raise RuntimeError("not connected")

    set_switch = set_knob = read_settings = write_settings = reset = _no
    control = False
    settings_dirty = False

    def enable_control(self, _on=True):
        raise RuntimeError("not connected")

    def firmware_block(self):
        return "not connected"

    def control_block(self):
        return "not connected"

    def validate_write(self, _changes):
        return ["not connected"]

    def close(self):
        pass


class LinkManager:
    """Opens and closes the MK2 link (and the MQTT bridge) from the Connect/Disconnect buttons of the panel.

    Monitor only by default (mk2.toml [control] enabled = false): Connect sends no knob or switch command.
    With control on at connect, the saved knob limit is restored, and the saved switch only if restore_switch=True
    (command line --direct). Otherwise the switch stays in the current state of the device until the user selects
    it on the DMC. Control can be switched on/off while connected (set_control); switching on sets the input
    current limit to the conservative minimum (2.0 A by default) and leaves the switch alone."""

    def __init__(self, app, log_dir, mqtt_cfg=None, allow_unknown_firmware=False):
        self.app = app
        self.log_dir = log_dir
        self.allow_unknown_firmware = allow_unknown_firmware   # command line only (--allow-unknown-firmware)
        self.mqtt_cfg = mqtt_cfg           # mk2_mqtt.Config or None (created from the settings when needed)
        self.link = Disconnected(app.link_limits())
        self.port = None
        self.error = None                  # latest connection error (port busy etc.)
        self._stop_mqtt = None
        self.lock = threading.Lock()
        # password entered in the panel (overrides mk2.toml / the environment variable); "remember" = from the state file
        saved = load_panel_state().get("mqtt_password")
        self.password_override = saved if isinstance(saved, str) and saved else None
        # connection details entered in the panel {"host", "port", "username"} (state file, override mk2.toml)
        conn_saved = load_panel_state().get("mqtt_conn")
        self.conn_override = conn_saved if isinstance(conn_saved, dict) else {}
        self.last_mqtt_error = None        # latest rejection of a stopped bridge (password prompt)
        self._mqtt_t0 = None               # start time of the bridge (detects an unreachable broker)

    MQTT_REACH_S = 12.0                    # no connection within this time -> "cannot reach host"

    def _mqtt_cfg(self):
        import copy
        cfg = copy.copy(self.mqtt_cfg or self.app.mqtt_config())
        o = self.conn_override
        if isinstance(o.get("host"), str) and o["host"].strip():
            cfg.host = o["host"].strip()
        if isinstance(o.get("port"), int) and 0 < o["port"] < 65536:
            cfg.port = o["port"]
        if isinstance(o.get("username"), str):
            cfg.username = o["username"]
        if self.password_override:
            cfg.password = self.password_override
        return cfg

    def set_mqtt_settings(self, host, port, username, password, remember):
        """Saving of the MQTT dialog of the panel. Connection details always go to the state file, the password only if remember."""
        self.conn_override = {"host": host, "port": port, "username": username}
        self.password_override = password or None
        save_panel_state({"mqtt_conn": self.conn_override,
                          "mqtt_password": password if remember and password else None})

    def password_source(self):
        """Where the password comes from: 'panel', 'env', 'config' or None (no password)."""
        if self.password_override:
            return "panel"
        if os.environ.get("MK2_MQTT_PASSWORD"):
            return "env"
        if (self.mqtt_cfg or self.app.mqtt_config()).password:
            return "config"
        return None

    def auth_failed(self):
        """The broker rejected the password (running or just stopped bridge)."""
        pub = getattr(self._stop_mqtt, "publisher", None) if self._stop_mqtt else None
        err = pub.last_error if pub is not None else self.last_mqtt_error
        return bool(err) and "author" in err.lower()

    @property
    def connected(self):
        return not isinstance(self.link, Disconnected)

    @property
    def mqtt_running(self):
        return self._stop_mqtt is not None

    def mqtt_status(self):
        """(text, ok) for the top bar, or None when the bridge is not running."""
        if self._stop_mqtt is None:
            return None
        pub = getattr(self._stop_mqtt, "publisher", None)
        if pub is None:
            return "MQTT starting...", False
        if pub.last_error:
            if "author" in pub.last_error.lower():
                return "MQTT: password rejected (⚙ MQTT settings)", False
            return f"MQTT: {pub.last_error}", False
        if not pub.connected:
            if self._mqtt_t0 is not None and time.time() - self._mqtt_t0 > self.MQTT_REACH_S:
                return f"MQTT: cannot reach {pub.cfg.host}:{pub.cfg.port} (⚙ MQTT settings)", False
            return "MQTT: connecting...", False
        if not pub.registered:
            return "MQTT: waiting for Venus...", False
        return "MQTT on Venus", True

    @property
    def control_default(self):
        """Control state at connect: mk2.toml [control] enabled (default False = monitor only)."""
        return bool(getattr(getattr(self.app, "control", None), "enabled", False))

    def save_knob(self, raw):
        """The last current limit is remembered per port (an emulator value never reaches a real device)."""
        save_port_state(self.port, knob=raw)

    def connect(self, port, mqtt=False, restore_switch=False, control=None):
        """control=None: on if mk2.toml [control] enabled, or if the user left control on last time ON THIS PORT
        (state file "ports"). With control on, the last current limit of this port is restored (2.0 A the first
        time)."""
        import mk2_link
        if control is None:
            control = self.control_default or port_state(load_panel_state(), port).get("control") is True
        control = bool(control)
        with self.lock:
            self._disconnect()
            self.error = None
            emu = port == mk2_link.EMULATOR_PORT
            try:
                link = mk2_link.MK2(port, log_dir=None if emu else self.log_dir, limits=self.app.link_limits(),
                                    control=control, allow_unknown_firmware=self.allow_unknown_firmware)
            except Exception as e:  # noqa: BLE001 - port busy / does not exist
                self.error = f"{port}: {e}"
                raise
            if control:
                # control on: the saved limit is restored (the device does not report it), the switch only on request
                saved = load_panel_state()
                link.desired_knob = startup_knob(port_state(saved, port), link.knob_min, link.knob_max)
                if restore_switch:
                    link.desired_switch = startup_switch(saved)
            link.start()
            self.link, self.port = link, port
            try:
                save_panel_state({"port": port})
            except OSError:
                pass
            if mqtt:
                self._start_mqtt()

    def _start_mqtt(self):
        import mk2_link
        if not self.connected or self._stop_mqtt is not None:
            return
        import logging
        import mk2_mqtt
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        cfg = self._mqtt_cfg()
        if self.port == mk2_link.EMULATOR_PORT:
            # the emulator is its own device on Venus: different client_id -> different instance, no mixing with
            # the data of the real MultiPlus (VRM history, Mode, current limit)
            cfg.client_id = EMULATOR_CLIENT_ID
            cfg.custom_name = EMULATOR_DEVICE_NAME
        self.last_mqtt_error = None
        self._mqtt_t0 = time.time()
        self._stop_mqtt = mk2_mqtt.start_background(
            self.link, cfg, on_knob=self.save_knob,
            on_switch=lambda s: save_panel_state({"switch": s}))

    def start_mqtt(self):
        with self.lock:
            self._start_mqtt()

    def restart_mqtt(self):
        """Apply new connection details: a running bridge is stopped and started again."""
        with self.lock:
            if self._stop_mqtt is None:
                return
            self._stop_mqtt_locked()
            self._start_mqtt()

    def stop_mqtt(self):
        with self.lock:
            self._stop_mqtt_locked()

    def _stop_mqtt_locked(self):
        stop, self._stop_mqtt = self._stop_mqtt, None
        if stop:
            pub = getattr(stop, "publisher", None)
            self.last_mqtt_error = pub.last_error if pub is not None else None
            stop()                         # connected:0 to Venus before closing the port

    def _disconnect(self):
        self._stop_mqtt_locked()
        if self.connected:
            self.link.close()
        self.link = Disconnected(self.app.link_limits())
        self.port = None

    def disconnect(self):
        with self.lock:
            self._disconnect()

    def set_control(self, on):
        """Monitor only <-> control on the open link. Switching on restores the last current limit (the conservative
        minimum 2.0 A if none is saved; sent by the poll loop); the switch is not touched. The choice is saved.
        Raises if not connected, or (when switching on) if the firmware is not verified."""
        with self.lock:
            if not self.connected:
                raise RuntimeError("not connected")
            if on:
                block = self.link.firmware_block()
                if block:
                    raise RuntimeError(block)
                if self.link.desired_knob is None:
                    # continue from the limit where it was left (state file); the minimum 2.0 A the first time
                    self.link.desired_knob = startup_knob(port_state(load_panel_state(), self.port),
                                                          self.link.knob_min, self.link.knob_max)
            self.link.enable_control(on)
            try:
                save_port_state(self.port, control=bool(on))   # remembered per port: the next Connect starts the same way
            except OSError:
                pass


def port_state(state, port):
    """Per-port part of the state file: {"control": bool, "knob": raw}. A real port without its own knob value
    falls back to the old global "knob" (state files written before the per-port memory); the emulator never does,
    so emulator values never reach a real device and vice versa."""
    import mk2_link
    ports = state.get("ports")
    entry = dict(ports.get(port) or {}) if isinstance(ports, dict) and isinstance(ports.get(port), dict) else {}
    if "knob" not in entry and port != mk2_link.EMULATOR_PORT and "knob" in state:
        entry["knob"] = state["knob"]
    return entry


def save_port_state(port, **values):
    """Merges values into the state file entry of this port (state["ports"][port])."""
    if not port:
        return
    state = load_panel_state()
    ports = state.get("ports") if isinstance(state.get("ports"), dict) else {}
    entry = dict(ports.get(port) or {}) if isinstance(ports.get(port), dict) else {}
    entry.update(values)
    ports = dict(ports)
    ports[port] = entry
    save_panel_state({"ports": ports})


STALE_S = 5.0
# Light look (the palette is shared with mk2_settings_view.py). The DMC stays dark.
FONT = vv.FONT
BG = vv.BG
CARD = vv.CARD
BORDER = vv.BORDER
FG = vv.FG
FG2 = vv.FG2
MUTED = vv.MUTED
ACCENT = vv.ACCENT
CHANGE = vv.CHANGE
CHANGED_BG = vv.CHANGED_BG
TOPBAR = "#1f2a37"
STALE_FG = "#94a3b8"     # stale measurement
GOOD_FG = "#16a34a"      # battery charging (+)
ERR_FG = "#b91c1c"
DMC_BG = "#2b2b2b"
SWITCH_ON = "#22d3ee"    # active switch button of the DMC

# sidebar groups: (title, ((RAM ID, name), ...)); ROWS defines the unit and the formatting
GROUPS = (
    ("AC OUTPUT", ((2, "Voltage"), (9, "Current"), (7, "Frequency"))),
    ("AC INPUT", ((0, "Voltage"), (1, "Current"), (8, "Frequency"))),
    ("BATTERY", ((4, "Voltage"), (5, "Current"), (6, "Ripple"))),
)
ROW_BY_RID = {r[1]: r for r in ROWS if r}


def _round_rect(c, x1, y1, x2, y2, r=9, **kw):
    """Rounded rectangle for the canvas (smooth polygon)."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class LogTail:
    """Follows the log and decodes the X/Y/Z Winmon pairs."""

    def __init__(self, path):
        self.f = open(path, encoding="utf-8")
        self.bufs = {"TX": bytearray(), "RX": bytearray()}
        self.pending = {}
        self.ram_info = {}
        self.values = {}     # RAM id -> (raw, display, wallclock)
        self.switch = None   # (state, wallclock)
        self.knob = None     # (0x04 value, wallclock) - knob value sent by the other program
        self.led = None      # (lo, hi, wallclock)
        self.mk2_version = None
        self.mk2_mode = None
        self.fw = {}         # 0x82/0x83 -> value

    def poll(self):
        while True:
            line = self.f.readline()
            if not line:
                return
            self._feed(line)

    def _feed(self, line):
        p = line.split()
        if len(p) < 3 or p[1] not in ("TX", "RX"):
            return
        dr = p[1]
        buf = self.bufs[dr]
        buf.extend(int(h, 16) for h in p[2:])
        while buf:
            if (buf[0] & 0x7F) > d.MAX_LEN:
                del buf[:1]
                continue
            n = (buf[0] & 0x7F) + 2
            if len(buf) < n:
                break
            fr = bytes(buf[:n])
            if sum(fr) & 0xFF == 0:
                self._frame(dr, fr)
                del buf[:n]
            else:
                del buf[:1]

    def _frame(self, dr, fr):
        if fr[1] != 0xFF:
            return
        cmd = fr[2]
        if cmd == 0x56 and dr == "RX" and len(fr) >= 9:  # 'V' MK2 version
            self.mk2_version = int.from_bytes(fr[3:7], "little")
            self.mk2_mode = chr(fr[7]) if 32 <= fr[7] < 127 else f"0x{fr[7]:02X}"
            return
        if cmd not in (0x58, 0x59, 0x5A):
            return
        L, p = cmd, fr[3:-1]
        now = time.time()
        if dr == "TX":
            self.pending[L] = p
            if len(p) >= 2 and p[0] == 0x03:
                self.switch = (p[1], now)
            elif len(p) >= 2 and p[0] == 0x04:  # input current limiter knob
                self.knob = (p[1], now)
            return
        req = self.pending.get(L)
        for i in range(0, len(p) - 2, 3):
            rc, val = p[i], p[i + 1] | p[i + 2] << 8
            if rc == 0x81:  # LED state, can also arrive unrequested
                self.led = (p[i + 1], p[i + 2], now)
                continue
            if not req:
                continue
            if rc in (0x82, 0x83) and req[0] in (0x05, 0x06):
                self.fw[rc] = val
            elif req[0] == 0x36 and len(req) >= 2:
                rid = int.from_bytes(req[1:3], "little")
                sc, off = self.ram_info.get(rid, (0, 0))
                if rc == 0x8E:
                    self.ram_info[rid] = (val, off)
                elif rc == 0x8F:
                    self.ram_info[rid] = (sc, val)
            elif req[0] == 0x30 and len(req) >= 2 and rc == 0x85:
                rid = req[1]
                self.values[rid] = (val, self._conv(rid, val), now)

    def _conv(self, rid, raw):
        sc, off = self.ram_info.get(rid, (None, 0))
        if sc is None or d.ram_scale(sc) is None:
            return None
        scale, signed = d.ram_scale(sc)
        x = d.s16(raw) if signed else raw
        v = scale * (x + d.s16(off))
        if rid in (7, 8):
            v = d.period_to_hz(v) or 0.0
        return v


class Panel:
    def __init__(self, root, tail, path, debug=False, port=None, conn=None):
        # conn = LinkManager: the link changes with the Connect/Disconnect buttons (self.tail follows it)
        self.conn = conn
        self._tail = tail if conn is None else None
        self.debug = debug   # --debug: developer numbers (raw/age, LED word, 0x04, MK2/fw, cycle)
        self.logname = os.path.basename(path)
        self.logpath = path
        self._port = port
        self._progress = ""
        self._t0 = time.time()
        self._tick_n = 0
        self._busy = False   # connecting/disconnecting in progress in a background thread
        self._reading = False  # settings are being read
        # last selected switch (state file): for display only, when nothing has been sent to the device yet
        self._saved_switch = startup_switch(load_panel_state())
        self._seen_link = None
        direct = hasattr(self.tail, "set_switch")
        root.title(f"{APP_NAME} - {APP_SUBTITLE}" + (" (direct - controls the device)" if direct
                                                      else " (passive log)"))
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        vv.apply_style(root)

        # Layout: dark top bar, measurement cards + buttons on the left (fixed), settings tabs on the right (stretching)
        self.root = root
        root.resizable(True, True)
        root.minsize(940, 700)   # the DMC is below the tabs (low, stretches in width)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        self._build_topbar(root)

        body = tk.Frame(root, bg=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(10, 4))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        side = tk.Frame(body, bg=BG)
        side.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self._build_table(side)

        small = ("Segoe UI", 9)
        self.btn_settings = ttk.Button(side, text="Read settings", style="Accent.TButton",
                                       command=lambda: self._read_settings(root))
        self.btn_settings.pack(fill="x", pady=(4, 4))
        if hasattr(self.tail, "write_settings"):
            # edit in the tabs -> write the changes
            self.btn_send_tabs = ttk.Button(side, text="Write changes...", state="disabled",
                                            command=self._send_tabs)
            self.btn_send_tabs.pack(fill="x", pady=2)
            self.btn_write = ttk.Button(side, text="Raw editor...", state="disabled",
                                        command=self._open_write_dialog)
            self.btn_write.pack(fill="x", pady=2)
            # backup: save the read settings to a file / load from a file (through the preview)
            self.btn_save_file = ttk.Button(side, text="Save settings to file...", state="disabled",
                                            command=self._save_settings_file)
            self.btn_save_file.pack(fill="x", pady=(8, 2))
            self.btn_load_file = ttk.Button(side, text="Load settings from file...", state="disabled",
                                            command=self._load_settings_file)
            self.btn_load_file.pack(fill="x", pady=2)
        self.settings_status = tk.Label(side, text="", bg=BG, fg=MUTED, font=small, wraplength=230,
                                        justify="left", anchor="w")
        self.settings_status.pack(anchor="w", pady=(6, 0))
        self.emu_card = None
        if self.conn is not None:
            self._build_emu_card(side)

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        if hasattr(self.tail, "write_settings"):
            # orange bar when there are unsaved changes (tick updates it)
            self.change_bar = tk.Frame(right, bg=CHANGED_BG, highlightthickness=1, highlightbackground=CHANGE)
            self.change_label = tk.Label(self.change_bar, text="", bg=CHANGED_BG, fg=CHANGE,
                                         font=(FONT, 10, "bold"))
            self.change_label.pack(side="left", padx=12, pady=6)
            ttk.Button(self.change_bar, text="Review...", style="Change.TButton",
                       command=self._send_tabs).pack(side="right", padx=10, pady=4)
            self.change_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            self.change_bar.grid_remove()
        self.settings_notebook = ttk.Notebook(right)
        self.settings_notebook.grid(row=1, column=0, sticky="nsew")
        ph = tk.Frame(self.settings_notebook, bg=BG, width=560, height=380)
        tk.Label(ph, text="Press 'Read settings' to load the settings.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).place(relx=0.5, rely=0.4, anchor="center")
        self.settings_notebook.add(ph, text="General")
        if self.conn is not None:
            self._build_port_controls(right)   # right edge of the tab row

        self.info = tk.Label(root, text="", bg=BG, fg=MUTED, font=("Consolas", 9), anchor="w", justify="left")
        self.info.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))

        self._build_dmc(right)
        self._sync_conn_controls()
        self.refresh()
        self._fix_window_size(root)
        root.after(100, self.tick, root)

    @property
    def tail(self):
        return self.conn.link if self.conn is not None else self._tail

    @property
    def port(self):
        return self.conn.port if self.conn is not None else self._port

    # ---------------------------------------------------------- window size
    DEFAULT_SIZE = (1240, 900)

    def _fix_window_size(self, root):
        """Fixed size right at the start: otherwise Tk grows the window when the settings tabs (larger content)
        are loaded. The user can still resize; the size is saved in the state file (window_size)."""
        root.update_idletasks()
        w, h = self.DEFAULT_SIZE
        saved = load_panel_state().get("window_size")
        if (isinstance(saved, list) and len(saved) == 2 and all(isinstance(x, int) for x in saved)):
            w, h = saved
        min_w, min_h = 940, 700
        if getattr(self, "port_box", None) is not None:
            # the connection controls are at the right edge of the tab row: tabs and controls must fit side by side
            from tkinter import font as tkfont
            f = tkfont.Font(family=FONT, size=10)
            tabs_w = sum(f.measure(t) + 28 for t in vv.TOP_TABS + ["All settings"])   # padding 14+14
            side_w = self.settings_notebook.winfo_rootx() - root.winfo_rootx()
            if side_w <= 0:
                side_w = 260
            min_w = max(min_w, side_w + tabs_w + 24 + self.port_box.winfo_reqwidth() + 12)
            min_w = min(min_w, root.winfo_screenwidth())      # small screen: not wider than the screen
            root.minsize(min_w, min_h)
            self._min_w = min_w
        w = max(min_w, root.winfo_reqwidth(), w)
        h = max(min_h, h)
        w = min(w, root.winfo_screenwidth())
        h = min(h, root.winfo_screenheight() - 60)   # taskbar
        root.geometry(f"{w}x{h}")

    def save_window_size(self):
        try:
            if self.root.state() == "normal":
                save_panel_state({"window_size": [self.root.winfo_width(), self.root.winfo_height()]})
        except (OSError, tk.TclError):
            pass

    # ---------------------------------------------------------- top bar
    def _build_topbar(self, root):
        bar = tk.Frame(root, bg=TOPBAR)
        bar.grid(row=0, column=0, sticky="ew")
        _big, bar_icon = load_app_icons(self.root)
        if bar_icon is not None:
            tk.Label(bar, image=bar_icon, bg=TOPBAR).pack(side="left", padx=(12, 0), pady=6)
        tk.Label(bar, text=APP_NAME, bg=TOPBAR, fg="white",
                 font=(FONT, 12, "bold")).pack(side="left", padx=(8 if bar_icon is not None else 14, 8), pady=10)
        tk.Label(bar, text=APP_SUBTITLE, bg=TOPBAR, fg="#94a3b8",
                 font=(FONT, 9)).pack(side="left", padx=(0, 20), pady=10)
        self.conn_dot = tk.Canvas(bar, width=12, height=12, bg=TOPBAR, highlightthickness=0)
        self.conn_dot.pack(side="left")
        self.conn_oval = self.conn_dot.create_oval(1, 1, 11, 11, fill=STALE_FG, outline="")
        self.conn_label = tk.Label(bar, text="", bg=TOPBAR, fg="#cbd5e1", font=(FONT, 9))
        self.conn_label.pack(side="left", padx=6)
        tk.Label(bar, text="Unofficial - not a Victron product", bg=TOPBAR, fg=STALE_FG,
                 font=(FONT, 8)).pack(side="right", padx=14)

    def _build_port_controls(self, parent):
        """Port selection + Connect/Disconnect + MQTT (direct mode, LinkManager). Position: right edge of the
        tab row (placed over the notebook; the tabs are on the left, so they are not covered)."""
        box = tk.Frame(parent, bg=BG)
        box.place(in_=self.settings_notebook, relx=1.0, x=0, y=1, anchor="ne")
        box.lift(self.settings_notebook)
        self.port_box = box
        tk.Label(box, text="Port", bg=BG, fg=FG2, font=(FONT, 9)).pack(side="left", padx=(0, 6))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(box, textvariable=self.port_var, state="readonly", width=20)
        self.port_combo.pack(side="left")
        self.btn_refresh = ttk.Button(box, text="Refresh", command=self.refresh_ports)
        self.btn_refresh.pack(side="left", padx=(6, 0))
        # wide enough for the text 'Disconnect', so the button does not change size when the state changes
        self.btn_connect = ttk.Button(box, text="Connect", style="Accent.TButton", width=12,
                                      command=self._connect_clicked)
        self.btn_connect.pack(side="left", padx=(6, 0))
        self.mqtt_var = tk.BooleanVar(value=bool(load_panel_state().get("mqtt", False)))
        self.mqtt_check = tk.Checkbutton(box, text="Publish to Venus (MQTT)", variable=self.mqtt_var,
                                         command=self._mqtt_toggled, bg=BG, fg=FG2, activebackground=BG,
                                         activeforeground=FG, selectcolor=CARD, font=(FONT, 9), bd=0,
                                         highlightthickness=0)
        self.mqtt_check.pack(side="left", padx=(10, 0))
        Tooltip(self.mqtt_check, self._mqtt_tooltip_text)
        # MQTT connection details (host, port, user, password) at any time
        self.btn_mqtt_settings = ttk.Button(box, text="⚙", width=3, command=self._open_mqtt_settings)
        self.btn_mqtt_settings.pack(side="left", padx=(4, 0))
        Tooltip(self.btn_mqtt_settings, "MQTT settings: Venus address, port, username, password")
        self._port_items = []
        self.refresh_ports()

    def _mqtt_tooltip_text(self):
        """Help text over the MQTT box (updates: shows where the password comes from now)."""
        cfg = self.conn._mqtt_cfg()
        src = {"panel": "entered in this panel", "env": "environment variable MK2_MQTT_PASSWORD",
               "config": "mk2.toml [mqtt] password", None: "NOT SET"}[self.conn.password_source()]
        where = "MQTT settings (⚙)" if self.conn.conn_override.get("host") else "mk2.toml [mqtt] / default"
        return (f"Publishes the device to Venus GX over MQTT.\n"
                f"Venus:    {cfg.host}:{cfg.port}   (from {where})\n"
                f"Username: {cfg.username or '(none)'}\n"
                f"Password: {src}\n\n"
                "Change these with the ⚙ button next to this box. Ticking the box asks for them\n"
                "if the password is missing or Venus rejected it.\n"
                "Venus: Settings > Services > MQTT must be enabled. Address = the Venus IP\n"
                "(Settings > Ethernet / Wi-Fi), or venus.local on most networks.\n\n"
                "Other ways to give the password:\n"
                "  mk2.toml:     [mqtt] password = \"...\"\n"
                "  cmd.exe:      set MK2_MQTT_PASSWORD=...\n"
                "                python mk2_panel.py\n"
                "  PowerShell:   $env:MK2_MQTT_PASSWORD = \"...\"; python mk2_panel.py\n\n"
                f"Emulator: published as a separate device '{EMULATOR_DEVICE_NAME}'\n"
                f"(client id {EMULATOR_CLIENT_ID}), never as the real MultiPlus.")

    def _ask_mqtt_password(self):
        """MQTT dialog: Venus address, port, user and password.
        Returns True if saved (LinkManager + state file)."""
        win = tk.Toplevel(self.root)
        win.title("MQTT settings - Venus GX")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.attributes("-topmost", True)
        cfg = self.conn._mqtt_cfg()
        failed = self.conn.auth_failed()
        msg = ("Venus rejected the password. Check the settings." if failed else
               "Connection to the MQTT broker of your Venus GX / Cerbo GX.")
        tk.Label(win, text=msg, bg=BG, fg=ERR_FG if failed else FG, font=(FONT, 10),
                 anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 2))
        tk.Label(win, text="On the Venus: Settings > Services > MQTT on LAN must be enabled.", bg=BG,
                 fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, columnspan=3, sticky="w", padx=14,
                                                pady=(0, 6))

        def field(row, label, value, hint="", show=None, width=28):
            tk.Label(win, text=label, bg=BG, fg=FG2, font=(FONT, 9)).grid(row=row, column=0, sticky="w",
                                                                        padx=(14, 6), pady=3)
            var = tk.StringVar(value=value)
            e = tk.Entry(win, textvariable=var, width=width, relief="flat", bd=0, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT, show=show or "")
            e.grid(row=row, column=1, sticky="w", pady=3)
            if hint:
                tk.Label(win, text=hint, bg=BG, fg=MUTED, font=(FONT, 8)).grid(row=row, column=2, sticky="w",
                                                                             padx=(6, 14))
            return var, e

        host, host_e = field(2, "Venus address", cfg.host, "IP address, or venus.local")
        port, port_e = field(3, "Port", str(cfg.port), "1883 (plain MQTT)", width=8)
        user, _ = field(4, "Username", cfg.username, "usually empty")
        # the password is not pre-filled on screen; empty = keep the current one (if there is one)
        pw, pw_e = field(5, "Password", "", "", show="•")
        has_pw = self.conn.password_source() is not None
        tk.Label(win, text=("leave empty to keep the current one" if has_pw else
                            "the Venus MQTT / Remote Console password"),
                 bg=BG, fg=MUTED, font=(FONT, 8)).grid(row=5, column=2, sticky="w", padx=(6, 14))
        show = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="Show", variable=show, bg=BG, activebackground=BG, font=(FONT, 8),
                       command=lambda: pw_e.config(show="" if show.get() else "•")).grid(
            row=6, column=1, sticky="w")
        remember = tk.BooleanVar(value=bool(load_panel_state().get("mqtt_password")))
        tk.Checkbutton(win, text="Remember the password on this computer (saved unencrypted in "
                                 "mk2_panel_state.json)",
                       variable=remember, bg=BG, activebackground=BG, font=(FONT, 8)).grid(
            row=7, column=0, columnspan=3, sticky="w", padx=10)
        err = tk.Label(win, text="", bg=BG, fg=ERR_FG, font=(FONT, 9))
        err.grid(row=8, column=0, columnspan=3, sticky="w", padx=14)
        result = {"ok": False}

        def ok(_e=None):
            h = host.get().strip()
            if not h or " " in h:
                err.config(text="Enter the Venus address (IP or host name).")
                host_e.focus_set()
                return
            try:
                pn = int(port.get().strip())
                if not 0 < pn < 65536:
                    raise ValueError
            except ValueError:
                err.config(text="Port must be a number 1-65535.")
                port_e.focus_set()
                return
            value = pw.get() or (self.conn.password_override if self.conn.password_override else "")
            if not value and not has_pw:
                err.config(text="Enter the password.")
                pw_e.focus_set()
                return
            try:
                self.conn.set_mqtt_settings(h, pn, user.get().strip(), value, remember.get())
            except OSError as e:
                self._progress = f"state file: {e}"
            result["ok"] = True
            win.destroy()

        bar = tk.Frame(win, bg=BG)
        bar.grid(row=9, column=0, columnspan=3, sticky="e", padx=10, pady=10)
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(bar, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=4)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 80
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        (pw_e if failed or has_pw else host_e).focus_set()
        win.grab_set()
        self.root.wait_window(win)
        return result["ok"]

    def _open_mqtt_settings(self):
        """⚙ button: connection details at any time; a running bridge is restarted with the new details."""
        if not self._ask_mqtt_password():
            return
        if self.conn.mqtt_running and not self._busy:
            self._run_conn(self.conn.restart_mqtt, "MQTT restarting...", "MQTT settings applied")

    def _ensure_mqtt_password(self):
        """Asks for the connection details if there is no password or the broker rejected it. False = user cancelled."""
        if self.conn.password_source() is not None and not self.conn.auth_failed():
            return True
        return self._ask_mqtt_password()

    def refresh_ports(self, select=None):
        """Updates the port list. Selection: given > current > last used > mk2.toml > first."""
        items = list_serial_ports()
        self._port_items = items
        self.port_combo.config(values=[label for _p, label in items])
        current = self._selected_port()
        wanted = [select, self.conn.port, current, load_panel_state().get("port"),
                  getattr(getattr(self.conn.app, "serial", None), "port", None)]
        ports = [p for p, _l in items]
        for w in wanted:
            if w in ports:
                self.port_combo.current(ports.index(w))
                return
        if items:
            self.port_combo.current(0)

    def _selected_port(self):
        label = self.port_var.get() if hasattr(self, "port_var") else ""
        return next((p for p, lab in self._port_items if lab == label), None)

    def _connect_clicked(self):
        from tkinter import messagebox
        if self._busy:
            return
        if self.conn.connected:
            self._run_conn(self.conn.disconnect, "disconnecting...", "disconnected")
            return
        port = self._selected_port()
        if not port:
            messagebox.showinfo("Connect", "Select a port first.", parent=self.root)
            return
        mqtt = bool(self.mqtt_var.get())
        import mk2_link
        if mqtt and not self._ensure_mqtt_password():
            mqtt = False                   # cancelled: connect without MQTT
            self.mqtt_var.set(False)

        def work():
            self.conn.connect(port, mqtt=False)
            if mqtt:
                try:
                    self.conn.start_mqtt()
                except Exception as e:  # noqa: BLE001 - the link stays open without MQTT
                    self._progress = f"MQTT: {e}"
        self._run_conn(work, f"connecting {port}...", f"connected {port}")

    def _run_conn(self, fn, busy_text, done_text):
        self._busy = True
        self._progress = busy_text
        self._sync_conn_controls()

        def work():
            try:
                fn()
                if not self._progress.startswith("MQTT:"):
                    self._progress = done_text
            except Exception as e:  # noqa: BLE001
                self._progress = f"ERROR: {e}"
            self._busy = False
        threading.Thread(target=work, daemon=True).start()

    def _mqtt_toggled(self):
        import mk2_link
        on = bool(self.mqtt_var.get())
        if on and not self._ensure_mqtt_password():
            self.mqtt_var.set(False)       # cancelled
            return
        try:
            save_panel_state({"mqtt": on})
        except OSError:
            pass
        if not self.conn.connected or self._busy:
            return              # takes effect at the next Connect

        def work():
            if on:
                self.conn.start_mqtt()
            else:
                self.conn.stop_mqtt()
        self._run_conn(work, "MQTT starting..." if on else "MQTT stopping...",
                       "MQTT on" if on else "MQTT off")

    # ---------------------------------------------------------- monitor only <-> control (DMC state button)
    def _control_clicked(self):
        """The state button in the DMC header toggles monitor only <-> control."""
        self._set_control(not bool(getattr(self.conn.link, "control", False)))

    def _set_control(self, on, ask=True):
        """Monitor only <-> control. The choice is remembered per port (state file): the next Connect on the same
        port starts the same way. Asked only the first time control is switched on on that port."""
        from tkinter import messagebox
        if not self.conn.connected or self._busy:
            return
        state = port_state(load_panel_state(), self.conn.port)
        if on and ask and not state.get("confirmed"):
            lim = startup_knob(state, getattr(self.conn.link, "knob_min", 20),
                               getattr(self.conn.link, "knob_max", 159))
            if not messagebox.askyesno(
                    "Enable control",
                    "Allow this program to control the device?\n\n"
                    f"The input current limiter is set to {lim / 10:.1f} A right away "
                    + ("(the conservative minimum; raise it yourself with the knob).\n\n" if "knob" not in state
                       else "(the value where you left it).\n\n")
                    + "The knob and the switch on the DMC then send commands to the device, and Venus control if "
                    "it is allowed in mk2.toml. The switch can turn the load and the mains off or on. The switch "
                    "is not changed until you click it.\n\n"
                    f"This choice is remembered for {self.conn.port}: the next Connect on this port starts with "
                    "control on and the last current limit, without asking again. Click the green CONTROL ON "
                    "button for monitor only.",
                    parent=self.root):
                return
            try:
                save_port_state(self.conn.port, confirmed=True)
            except OSError:
                pass
        try:
            self.conn.set_control(on)
            lim = getattr(self.conn.link, "desired_knob", None)
            self._progress = (f"control ON: current limit {lim / 10:.1f} A" if on and lim is not None else
                              "control ON" if on else "monitor only: nothing is sent")
        except Exception as e:  # noqa: BLE001 - e.g. unverified firmware
            messagebox.showerror("Control", str(e), parent=self.root)
        self._style_control_btn()

    def _control_blocked(self):
        """None if the DMC controls may send, otherwise the reason (monitor only, not connected, firmware)."""
        fn = getattr(self.tail, "control_block", None)
        return fn() if fn else None

    def _control_hint_if_blocked(self, e):
        """Click on a DMC control while it cannot send: show a tip next to the cursor. True = blocked."""
        why = self._control_blocked()
        if not why:
            return False
        if self.conn is not None and not self.conn.connected:
            msg = "Not connected.\nPress Connect first."
        elif "monitor only" in why:
            msg = ("Monitor only: nothing is sent to the device.\n"
                   "Enable control first: click the yellow\n'Monitor only - click to enable control' button.")
        else:
            msg = why
        self._show_hint(e.x_root, e.y_root, msg)
        self._flash_control_btn()
        return True

    def _dmc_switch_clicked(self, e, state):
        if self._control_hint_if_blocked(e):
            return
        self._click_switch(state)

    def _show_hint(self, x, y, text, ms=4000):
        if self._hint_win is not None:
            try:
                self._hint_win.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(win, text=text, bg="#fef3c7", fg="#111827", font=(FONT, 9), justify="left",
                 relief="solid", bd=1, padx=10, pady=6).pack()
        win.wm_geometry(f"+{x + 14}+{y + 14}")
        self._hint_win = win
        self.hint_text = text

        def close():
            if self._hint_win is win:
                self._hint_win = None
            try:
                win.destroy()
            except tk.TclError:
                pass
        win.after(ms, close)
        win.bind("<Button-1>", lambda _e: close())

    def _flash_control_btn(self):
        btn = getattr(self, "control_btn", None)
        if btn is None or str(btn["state"]) == "disabled":
            return
        btn.config(bg="#fde047", activebackground="#fde047")
        self._control_btn_key = None
        btn.after(700, self._style_control_btn)

    def _style_control_btn(self):
        """Text and colour of the DMC state button: not connected / read-only / monitor only / control on."""
        btn = getattr(self, "control_btn", None)
        if btn is None:
            return
        conn = self.conn
        link = conn.link if conn is not None else None
        if conn is None or not conn.connected:
            key = ("Monitor only (not connected)", "#374151", "#9ca3af", "disabled")
        elif link.firmware_block() and 0x82 in getattr(link, "fw", {}):
            key = ("READ-ONLY: unverified firmware", "#7f1d1d", "white", "disabled")
        elif getattr(link, "control", False):
            key = ("\u25cf  CONTROL ON  -  click for monitor only", "#16a34a", "white", "normal")
        else:
            key = ("Monitor only  -  click to enable control", "#f59e0b", "#111827", "normal")
        if self._busy and key[3] == "normal":
            key = key[:3] + ("disabled",)
        if getattr(self, "_control_btn_key", None) == key:
            return
        self._control_btn_key = key
        text, bg, fg, state = key
        btn.config(text=text, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                   disabledforeground=fg, state=state)

    def _sync_conn_controls(self):
        """Buttons according to the connection state; when the link changes, the old settings tabs are cleared."""
        if self.conn is None:
            return
        if self.tail is not self._seen_link:
            self._seen_link = self.tail
            self._t0 = time.time()             # 'Connecting...' from the start of the new link
            self._reset_settings_view()
        self._sync_emu_card()
        busy, conn = self._busy, self.conn.connected
        self.btn_connect.config(text="Disconnect" if conn else "Connect",
                                state="disabled" if busy else "normal")
        self.port_combo.config(state="disabled" if busy or conn else "readonly")
        self.btn_refresh.config(state="disabled" if busy or conn else "normal")
        self._style_control_btn()
        self.btn_settings.config(state="normal" if conn and not busy and not self._reading else "disabled")

    def _reset_settings_view(self):
        """New link (or no link): the settings of the previous device are removed from view."""
        nb = self.settings_notebook
        if getattr(nb, "settings_ui", None) is None and len(nb.tabs()) <= 1:
            return
        for t in nb.tabs():
            w = nb.nametowidget(t)
            nb.forget(t)
            w.destroy()
        nb.settings_ui = None
        ph = tk.Frame(nb, bg=BG, width=560, height=380)
        tk.Label(ph, text="Press 'Read settings' to load the settings.", bg=BG, fg=MUTED,
                 font=(FONT, 10)).place(relx=0.5, rely=0.4, anchor="center")
        nb.add(ph, text="General")
        self._set_settings_buttons("disabled")

    def _update_conn(self, now):
        t = self.tail
        if not hasattr(t, "set_switch"):
            text, col = "Passive log", STALE_FG
        elif self.conn is not None and not self.conn.connected:
            text, col = ("Busy..." if self._busy else "Not connected"), STALE_FG
        else:
            fresh = any(now - v[2] < STALE_S for v in t.values.values())
            if getattr(t, "error", None):
                text, col = "Not connected", STALE_FG
            elif not fresh:
                text, col = ("Connecting...", STALE_FG) if now - self._t0 < 6 else ("Not connected", STALE_FG)
            else:
                parts = ["Connected"]
                if self.port:
                    parts.append(str(self.port))
                if 0x82 in t.fw and 0x83 in t.fw:
                    parts.append(f"fw {t.fw[0x82] | t.fw[0x83] << 16}")
                fw_block = t.firmware_block() if hasattr(t, "firmware_block") else None
                if fw_block and 0x82 in t.fw:
                    parts.append("READ-ONLY: unverified firmware")
                elif hasattr(t, "control"):
                    parts.append("control ON" if t.control else "monitor only")
                if getattr(t, "settings_dirty", False):
                    parts.append("SETTINGS PARTLY WRITTEN")
                mq = self.conn.mqtt_status() if self.conn is not None else None
                if mq:
                    parts.append(mq[0])
                text, col = " - ".join(parts), "#22c55e"
                if self.port == "emulator":
                    col = "#f59e0b"            # orange: not a real device
                if mq and not mq[1] and mq[0].startswith("MQTT: ") and "..." not in mq[0]:
                    col = "#ef4444"            # red: an MQTT error takes precedence (password, unreachable)
                if getattr(t, "settings_dirty", False):
                    col = "#ef4444"            # red: a write chain stopped part-way
        self.conn_label.config(text=text)
        self.conn_dot.itemconfig(self.conn_oval, fill=col)

    # ---------------------------------------------------------- measurement cards
    def _build_table(self, parent):
        big = (FONT, 13, "bold")
        small = ("Consolas", 8)
        self.cells = {}
        for title, rows in GROUPS:
            card = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", pady=(0, 8))
            tk.Label(card, text=title, bg=CARD, fg=MUTED, font=(FONT, 8, "bold"), anchor="w").grid(
                row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 2))
            if self.debug:
                for c, h in ((2, "raw"), (3, "age s")):
                    tk.Label(card, text=h, bg=CARD, fg=MUTED, font=small).grid(row=0, column=c, sticky="e", padx=4)
            for r, (rid, name) in enumerate(rows, start=1):
                unit = ROW_BY_RID[rid][2]
                tk.Label(card, text=name, bg=CARD, fg=FG2, font=(FONT, 10), anchor="w", width=10).grid(
                    row=r, column=0, sticky="w", padx=(12, 0), pady=2)
                val = tk.Label(card, text="--", bg=CARD, fg=FG, font=big, anchor="e", width=9)
                val.grid(row=r, column=1, sticky="e", padx=(0, 12))
                raw = age = None
                if self.debug:
                    raw = tk.Label(card, text="", bg=CARD, fg=MUTED, font=small, anchor="e", width=6)
                    raw.grid(row=r, column=2, sticky="e", padx=4)
                    age = tk.Label(card, text="", bg=CARD, fg=MUTED, font=small, anchor="e", width=5)
                    age.grid(row=r, column=3, sticky="e", padx=(0, 10))
                self.cells[rid] = (val, raw, age, unit)
            tk.Frame(card, bg=CARD, height=6).grid(row=len(rows) + 1, column=0)

    # ---------------------------------------------------------- DMC: LEDs + switch
    def _build_dmc(self, root):
        # the DMC is part of the main window below the tabs (dark card)
        top = tk.Frame(root, bg=DMC_BG, highlightthickness=0)
        top.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.dmc_win = top
        c = tk.Canvas(top, width=DMC_W, height=DMC_H, bg=DMC_BG, highlightthickness=0)
        c.pack(fill="x", expand=True, padx=8, pady=(6, 0))
        self.canvas = c
        self.dmc_w = DMC_W
        self._dmc_resize_job = None

        def on_resize(e):
            w = max(DMC_MIN_W, e.width)
            if abs(w - self.dmc_w) < 4:
                return
            self.dmc_w = w
            if self._dmc_resize_job:
                c.after_cancel(self._dmc_resize_job)

            def redraw():
                self._dmc_resize_job = None
                self._draw_dmc()
                self.refresh()
            self._dmc_resize_job = c.after(60, redraw)   # redraw only after the size change settles
        c.bind("<Configure>", on_resize)
        # layout: physical panel (default) or classic; the choice is saved in the state file
        layout = load_panel_state().get("dmc_layout")
        self.dmc_layout = layout if layout in DMC_LAYOUTS else "physical"
        self.dmc_classic_var = tk.IntVar(value=1 if self.dmc_layout == "classic" else 0)
        tk.Checkbutton(top, text="toggle layout", variable=self.dmc_classic_var, command=self._toggle_dmc_layout,
                       bg=DMC_BG, fg="#9ca3af", activebackground=DMC_BG, activeforeground="#e5e7eb",
                       selectcolor=DMC_BG, font=("Segoe UI", 8), bd=0, highlightthickness=0).pack(
            anchor="w", padx=10, pady=(0, 6))
        self._hint_win = None
        if self.conn is not None:
            # Monitor only (default) <-> control: a large state button in the DMC header (top right of the card)
            self._control_btn_key = None
            self.control_btn = tk.Button(top, text="", command=self._control_clicked, relief="flat", bd=0,
                                         font=("Segoe UI", 10, "bold"), padx=14, pady=3, cursor="hand2")
            self.control_btn.place(in_=c, relx=1.0, x=-8, y=2, anchor="ne")   # above the knob readout
            Tooltip(self.control_btn, lambda: (
                "Monitor only (default): the panel reads and shows values, nothing is sent to the device\n"
                "(no current limit, no switch).\n"
                "Control on: the knob and the switch on the DMC (and Venus control, if allowed in mk2.toml)\n"
                "change the device. The current limit continues from where you left it (2.0 A the first time).\n"
                "The switch is not changed until you click it.\n"
                "The choice is remembered for the next Connect (or set mk2.toml [control] enabled = true)."))
            self._style_control_btn()
        self._draw_dmc()

    def _toggle_dmc_layout(self):
        self.set_dmc_layout("classic" if self.dmc_classic_var.get() else "physical")

    def set_dmc_layout(self, layout, save=True):
        """Changes the DMC layout and redraws the window (the behaviour of the LEDs and the switch does not change)."""
        if layout not in DMC_LAYOUTS:
            raise ValueError(layout)
        self.dmc_layout = layout
        self.dmc_classic_var.set(1 if layout == "classic" else 0)
        if save:
            try:
                save_panel_state({"dmc_layout": layout})
            except OSError as e:
                self._progress = f"state file: {e}"
        self._draw_dmc()
        self.refresh()

    def _draw_dmc(self):
        c = self.canvas
        c.delete("all")
        W, H = self.dmc_w, DMC_H
        physical = self.dmc_layout == "physical"
        # The left area (LEDs + switch) grows with the width (about half, 360..680 px); the LED columns and
        # the buttons spread inside it. On the right the knob at the edge and the readout between the knob and the left area.
        L = max(360, min(680, int(W * 0.5)))
        self.dmc_left_w = L
        self.KNOB_CX, self.KNOB_CY = W - 62, 100
        self.dmc_disp_x = (L + (W - 124)) / 2
        c.create_text(14, 16, text="Digital Multi Control", fill="white", font=("Segoe UI", 11, "bold"),
                      anchor="w")
        self.leds = {}
        font = ("Segoe UI", 9)
        # physical panel: 'charger' on the left (text, LED on the right), 'inverter' on the right (LED, text)
        if physical:
            left, right = LED_CHARGER, LED_INVERTER
            head_left, head_right = "charger", "inverter"
        else:   # classic: inverter on the left, charger on the right
            left, right = LED_LEFT, LED_RIGHT
            head_left, head_right = "inverter", "charger"
        y0 = 58
        c.create_text(14, 38, text=head_left, fill="#9ca3af", font=("Segoe UI", 8, "bold"), anchor="w")
        c.create_text(L - 14, 38, text=head_right, fill="#9ca3af", font=("Segoe UI", 8, "bold"), anchor="e")
        dy = 24
        mid = L / 2
        gap = max(23, min(70, L * 0.07))      # half distance of the LED columns from the centre line
        for col, names in ((0, left), (1, right)):
            for i, text in enumerate(names):
                y = y0 + i * dy
                bit, on_col = LED_MAP[text]
                if physical:
                    on_col = LED_PHYSICAL_COLORS.get(text, on_col)
                verified = bit is not None
                if col == 0:
                    tx, cx = 14, mid - gap
                    c.create_text(tx, y, text=text, fill="white", font=font, anchor="w")
                else:
                    tx, cx = L - 14, mid + gap
                    c.create_text(tx, y, text=text, fill="white", font=font, anchor="e")
                c.create_line(tx if col == 0 else cx + 10, y + 11, cx - 10 if col == 0 else tx, y + 11, fill="#444")
                oval = c.create_oval(cx - 7, y - 7, cx + 7, y + 7, fill=LED_OFF[on_col], outline="#111", width=2)
                # emulator mode: clicking an LED toggles it (does nothing on a real device)
                c.tag_bind(oval, "<Button-1>", lambda e, n=text: self._click_led(n))
                c.tag_bind(oval, "<Enter>", lambda e: c.config(cursor="hand2") if self._emulator_active() else None)
                c.tag_bind(oval, "<Leave>", lambda e: c.config(cursor=""))
                if not verified:  # bit not identified yet -> the LED stays off
                    c.create_text(cx + (12 if col == 0 else -12), y - 7, text="?", fill="#888", font=("Segoe UI", 7))
                self.leds[text] = (oval, on_col, bit)

        # switch: four positions (rounded buttons)
        sy = y0 + 4 * dy - 2
        control = hasattr(self.tail, "set_switch")
        c.create_text(mid, sy + 2, fill="#aaa", font=("Segoe UI", 8),
                      text="switch: click to change" if control else "switch (monitored, read-only)")
        self.sw_items = {}
        # 2 = inverter only (AC input ignored, load from the battery) - verified 1.11.2026, transitions without interruption.
        # The physical panel has no inverter only position: it is last, slightly apart.
        # the buttons are spread over the width of the left area; an extra gap before the last one (inverter only)
        if physical:
            order, last = ((1, "charger only"), (4, "off"), (3, "on"), (2, "inverter only")), 3.35
        else:
            order, last = ((3, "on"), (4, "off"), (1, "charger only"), (2, "inverter only")), 3.15
        # half width so that 4 buttons + gaps of at least 16 px fit in the area (L - 28); the button width
        # is limited (at most 96 px), so a wider window grows the gaps and not the buttons
        hw = max(33, min(48, (L - 28 - last * 16) / (2 + 2 * last)))
        step = (L - 28 - 2 * hw) / last
        buttons = [(s, lab, 14 + hw + step * (k if k < 3 else last)) for k, (s, lab) in enumerate(order)]
        for state, label, x in buttons:
            rect = _round_rect(c, x - hw, sy + 12, x + hw, sy + 38, 10, outline="#666", width=1, fill=DMC_BG)
            txt = c.create_text(x, sy + 25, text=label, fill="#888", font=("Segoe UI", 8, "bold"))
            self.sw_items[state] = (rect, txt)
            if control:
                for item in (rect, txt):
                    c.tag_bind(item, "<Button-1>", lambda e, s=state: self._dmc_switch_clicked(e, s))
                    c.tag_bind(item, "<Enter>", lambda e: c.config(cursor="hand2"))
                    c.tag_bind(item, "<Leave>", lambda e: c.config(cursor=""))
        # LED word row only in --debug mode (refresh fills it)
        self.led_raw = c.create_text(mid, H - 8, text="", fill="#888", font=("Consolas", 8))
        self._build_knob(c, control)

    # ---------------------------------------------------------- input current limiter knob (Winmon 0x04)
    KNOB_CX, KNOB_CY, KNOB_R = 698, 100, 34   # _draw_dmc updates KNOB_CX according to the width
    KNOB_SPAN = 270.0      # angle range of the pointer in degrees (-135..+135 from 12 o'clock)
    KNOB_FULL = 159        # 15.9 A = maximum of the knob

    def _build_knob(self, c, control):
        import mk2_link
        cx, cy, r = self.KNOB_CX, self.KNOB_CY, self.KNOB_R
        # number display and texts on the left of the knob (dmc_disp_x), the knob at the right edge
        dx = self.dmc_disp_x
        c.create_rectangle(dx - 65, 30, dx + 65, 76, fill="black", outline="#333")
        self.knob_disp = c.create_text(dx, 53, text="", fill="#3fe0ff", font=("Consolas", 26, "bold"))
        # knob
        c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill="#111", outline="#000")
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#1c1c1c", outline="#555", width=2)
        c.create_oval(cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12, fill="#222", outline="#666")
        self.knob_line = c.create_line(cx, cy, cx, cy - r, fill="white", width=3)
        # texts under the readout, each on its own line: title, large limit value, small explanation
        c.create_text(dx, 92, text="input current limiter", fill="white", font=("Segoe UI", 9))
        self.knob_real = c.create_text(dx, 116, text="", fill="#e2e8f0", font=("Segoe UI", 15, "bold"))
        self.knob_info = c.create_text(dx, 136, text="", fill="#94a3b8", font=("Segoe UI", 8),
                                       width=220, justify="center", anchor="n")
        self.knob_pending = None   # direct mode: value set by the user, not sent yet
        self._knob_job = None
        import knob_cal
        self.knob_cal = knob_cal.KnobCal.load()  # knob -> real IMains (piecewise linear)
        if not control:
            return
        self._knob_min = getattr(self.tail, "knob_min", mk2_link.KNOB_MIN)   # mk2.toml [knob]
        self._knob_max = getattr(self.tail, "knob_max", mk2_link.KNOB_MAX)

        def value_from_xy(x, y):
            import math
            ang = math.degrees(math.atan2(x - cx, cy - y))  # 0 = up, + = clockwise
            ang = max(-self.KNOB_SPAN / 2, min(self.KNOB_SPAN / 2, ang))
            return round((ang + self.KNOB_SPAN / 2) / self.KNOB_SPAN * self.KNOB_FULL)

        def clamp(v):
            # limits from the link every time: the link can change (Connect/Disconnect)
            lo = getattr(self.tail, "knob_min", self._knob_min)
            hi = getattr(self.tail, "knob_max", self._knob_max)
            return max(lo, min(hi, v))

        def press(e):
            if self._control_hint_if_blocked(e):
                return
            drag(e)

        def drag(e):
            if self._control_blocked():
                return
            self.knob_pending = clamp(value_from_xy(e.x, e.y))

        def release(_e):
            self._send_knob()

        def wheel(e):
            if self._control_hint_if_blocked(e):
                return
            base = self.knob_pending if self.knob_pending is not None else (
                self.tail.knob[0] if getattr(self.tail, "knob", None) else self._knob_min)
            self.knob_pending = clamp(base + (1 if e.delta > 0 else -1))
            if self._knob_job:
                c.after_cancel(self._knob_job)
            self._knob_job = c.after(500, self._send_knob)  # send when scrolling stops

        hit = c.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, outline="", fill="")
        for item in (hit, self.knob_line):
            c.tag_bind(item, "<B1-Motion>", drag)
            c.tag_bind(item, "<Button-1>", press)
            c.tag_bind(item, "<ButtonRelease-1>", release)
            c.tag_bind(item, "<Enter>", lambda e: c.config(cursor="hand2"))
            c.tag_bind(item, "<Leave>", lambda e: c.config(cursor=""))
        c.bind("<MouseWheel>", wheel)

    def _send_knob(self):
        import threading
        self._knob_job = None
        v = self.knob_pending
        if v is None:
            return

        def work():
            try:
                if self.conn is not None and not self.conn.connected:
                    # no connection: save the limit, it is sent at the next Connect if control is on (desired_knob)
                    save_panel_state({"knob": v})
                    self._progress = f"knob {v / 10:.1f} saved (sent on connect when control is on)"
                    self.knob_pending = None
                    return
                self.tail.set_knob(v)
                if self.conn is not None:
                    self.conn.save_knob(v)              # remembered per port
                else:
                    save_panel_state({"knob": v})
            except Exception as e:  # noqa: BLE001
                self._progress = f"knob: {e}"
            self.knob_pending = None
        threading.Thread(target=work, daemon=True).start()

    def _draw_knob(self, now):
        import math
        c = self.canvas
        knob = getattr(self.tail, "knob", None)
        v = self.knob_pending if self.knob_pending is not None else (knob[0] if knob else None)
        if v is None and getattr(self.tail, "control", False):
            v = getattr(self.tail, "desired_knob", None)    # control on: the limit about to be sent (e.g. 2.0)
        if v is None:
            c.itemconfig(self.knob_disp, text="")
            c.itemconfig(self.knob_real, text="")
            c.itemconfig(self.knob_info, text="unknown: the device does not\nreport the value")
            return
        # inverter only / off: the knob has no effect now (mains not in use) -> grey, but adjustable,
        # so that the limit can be set in advance before switching to on / charger only
        idle = knob_inactive(self._switch_state())
        c.itemconfig(self.knob_disp, text=f"{v / 10:4.1f}",
                     fill="#ffb000" if self.knob_pending is not None else ("#5f7378" if idle else "#3fe0ff"))
        c.itemconfig(self.knob_line, fill="#777777" if idle else "white")
        ang = math.radians(-self.KNOB_SPAN / 2 + min(v, self.KNOB_FULL) / self.KNOB_FULL * self.KNOB_SPAN)
        cx, cy, r = self.KNOB_CX, self.KNOB_CY, self.KNOB_R
        c.coords(self.knob_line, cx + 0.35 * r * math.sin(ang), cy - 0.35 * r * math.cos(ang),
                 cx + r * math.sin(ang), cy - r * math.cos(ang))
        # Pre-VE.Bus: the panel assumes a 16 A device, this one is 30 A. The real IMains limit
        # comes from the calibration (knob_calibration.json, piecewise linear); '~' = extrapolated.
        # The knob value is the UPPER LIMIT of IMains (calibrated in the bulk stage). In absorption/float the charger
        # limits itself, so IMains stays below the limit.
        real, ext = self.knob_cal.to_amps(v)
        approx = "~" if ext else "≈"
        if self.knob_pending is not None:
            c.itemconfig(self.knob_real, text=f"{approx} {real:.1f} A", fill="#ffb000")
            c.itemconfig(self.knob_info, text="sending...")
            return
        main, _, note = knob_limit_text(real, approx, self._switch_state(), self._imains(now)).partition("\n")
        c.itemconfig(self.knob_real, text=main, fill="#5f7378" if idle else "#e2e8f0")
        c.itemconfig(self.knob_info, text=f"0x04={v}\n{note}" if self.debug else note)

    def _switch_state(self):
        sw = getattr(self.tail, "switch", None)
        return sw[0] if sw else None

    def _imains(self, now):
        im = self.tail.values.get(1) if hasattr(self.tail, "values") else None
        return im[1] if im and im[1] is not None and now - im[2] < STALE_S else None

    # ---------------------------------------------------------- emulator control (emulator mode only)
    def _emulator_active(self):
        import mk2_link
        return self.conn is not None and self.conn.connected and self.conn.port == mk2_link.EMULATOR_PORT

    def _build_emu_card(self, side):
        """Card for adjusting the measurements of the emulator. Shown only while connected to the emulator."""
        card = tk.Frame(side, bg=CARD, highlightthickness=1, highlightbackground="#f59e0b")
        tk.Label(card, text="EMULATOR", bg=CARD, fg="#b45309", font=(FONT, 8, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 2))
        self.emu_vars = {}
        for r, (rid, label, unit, lo, hi, step) in enumerate(EMU_CONTROLS, start=1):
            tk.Label(card, text=label, bg=CARD, fg=FG2, font=(FONT, 9), anchor="w").grid(
                row=r, column=0, sticky="w", padx=(12, 4), pady=1)
            var = tk.StringVar()
            fmt = "%.1f" if step < 1 else "%.0f"
            sb = tk.Spinbox(card, from_=lo, to=hi, increment=step, format=fmt, textvariable=var, width=7,
                            justify="right", relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=ACCENT, buttonbackground=CARD,
                            command=lambda rid=rid: self._emu_apply(rid))
            sb.grid(row=r, column=1, sticky="e", pady=1)
            sb.bind("<Return>", lambda _e, rid=rid: self._emu_apply(rid))
            sb.bind("<FocusOut>", lambda _e, rid=rid: self._emu_apply(rid))
            tk.Label(card, text=unit, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=r, column=2, sticky="w",
                                                                           padx=(3, 10))
            self.emu_vars[rid] = (var, lo, hi)
        tk.Label(card, text="Click the DMC LEDs to switch them on/off.", bg=CARD, fg=MUTED, font=(FONT, 8),
                 wraplength=220, justify="left").grid(row=len(EMU_CONTROLS) + 1, column=0, columnspan=3,
                                                       sticky="w", padx=12, pady=(4, 8))
        self.emu_card = card
        self._emu_shown = False

    def _emu_load_values(self):
        """Card fields from the current values of the emulator."""
        import mk2_emu
        for rid, (var, _lo, _hi) in self.emu_vars.items():
            v = mk2_emu.ram_value(rid)
            if v is not None:
                step = next(c[5] for c in EMU_CONTROLS if c[0] == rid)
                var.set(f"{v:.1f}" if step < 1 else f"{v:.0f}")

    def _emu_apply(self, rid):
        import mk2_emu
        if not self._emulator_active():
            return
        var, lo, hi = self.emu_vars[rid]
        try:
            v = float(var.get().replace(",", "."))
        except ValueError:
            self._emu_load_values()            # invalid input -> back to the current value
            return
        v = max(lo, min(hi, v))
        try:
            mk2_emu.set_ram_value(rid, v)
        except (KeyError, ValueError) as e:
            self._progress = f"emulator: {e}"
            return
        self._progress = f"emulator: {ROW_BY_RID[rid][0]} = {v:g} {ROW_BY_RID[rid][2]}"

    def _sync_emu_card(self):
        if self.emu_card is None:
            return
        want = self._emulator_active()
        if want and not self._emu_shown:
            self.emu_card.pack(fill="x", pady=(10, 0), before=self.settings_status)
            self._emu_load_values()
        elif not want and self._emu_shown:
            self.emu_card.pack_forget()
        self._emu_shown = want

    def _click_led(self, name):
        """Emulator mode: clicking an LED toggles a bit of the LED word (shown at the next polling round)."""
        import mk2_emu
        if not self._emulator_active():
            return
        bit = LED_MAP[name][0]
        if bit is None:
            return
        on = not (mk2_emu.led_word() >> bit & 1)
        mk2_emu.set_led_bit(bit, on)
        self._progress = f"emulator: LED {name} {'on' if on else 'off'}"

    def _set_settings_buttons(self, state):
        for name in ("btn_write", "btn_send_tabs", "btn_save_file", "btn_load_file"):
            b = getattr(self, name, None)
            if b is not None:
                b.config(state=state)

    # ---------------------------------------------------------- settings to file / from file
    def _save_settings_file(self):
        from tkinter import filedialog, messagebox
        import mk2_link
        s = getattr(self.tail, "settings", None)
        if not s:
            messagebox.showinfo("Save settings", "Read the settings first (Read settings).", parent=self.root)
            return
        fw = mk2_link.device_fw(self.tail.fw)
        p = filedialog.asksaveasfilename(
            parent=self.root, title="Save device settings", defaultextension=".json",
            filetypes=[("PhoenixConfig settings", "*.json"), ("All files", "*.*")],
            initialfile=f"phoenix_settings_{fw or 'fw'}_{time.strftime('%Y%m%d_%H%M%S')}.json")
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(settings_file_data(s, self.tail.fw), f, indent=1, ensure_ascii=False)
            self._progress = f"saved {os.path.basename(p)}"
        except OSError as e:
            messagebox.showerror("Save settings", str(e), parent=self.root)

    def _load_settings_file(self):
        """Reads the file and passes the changed values to the normal preview -> write chain."""
        from tkinter import filedialog, messagebox
        if not getattr(self.tail, "settings", None):
            messagebox.showinfo("Load settings", "Read the settings from the device first (Read settings).",
                                parent=self.root)
            return
        p = filedialog.askopenfilename(parent=self.root, title="Load device settings",
                                       filetypes=[("PhoenixConfig settings", "*.json"), ("All files", "*.*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror("Load settings", f"Cannot read the file:\n{e}", parent=self.root)
            return
        changes, errs = changes_from_file_data(data, self.tail.settings, self.tail.fw)
        if errs:
            messagebox.showerror("Load settings", "\n".join(errs[:25]), parent=self.root)
            return
        if not changes:
            messagebox.showinfo("Load settings", "The device already has the settings in the file.",
                                parent=self.root)
            return
        self._confirm_and_write(changes, [], self.root, getattr(self, "btn_load_file", None))

    # ---------------------------------------------------------- settings
    def _read_settings(self, root):
        import threading
        self.btn_settings.config(state="disabled")
        if hasattr(self.tail, "read_settings"):  # direct mode: read from the device
            link = self.tail
            self._reading = True

            def progress(i, n):
                self._progress = f"reading {i}/{n}..."

            def show(s):
                if self.tail is link:        # the connection did not change during the read
                    self._show_settings(s)
                    if hasattr(self, "btn_write"):
                        self._set_settings_buttons("normal")
                        block = link.firmware_block() if hasattr(link, "firmware_block") else None
                        if block:            # unverified firmware: reading and saving to a file only
                            for name in ("btn_write", "btn_send_tabs", "btn_load_file"):
                                getattr(self, name).config(state="disabled")
                            self._progress += f" - {block}"

            def work():
                try:
                    s = link.read_settings(progress)
                    self._progress = f"read {sum(1 for v in s.values() if v.get('sc'))} settings from the device"
                    root.after(0, lambda: show(s))
                except Exception as e:  # noqa: BLE001
                    self._progress = f"ERROR: {e}"
                self._reading = False
                root.after(0, lambda: self.btn_settings.config(
                    state="normal" if self.conn is None or self.conn.connected else "disabled"))
            self._progress = "starting..."
            threading.Thread(target=work, daemon=True).start()
        else:  # passive: decode from the log (Get settings of the other program)
            st = d.decode(self.logpath)
            s = {}
            for sid in set(st.setting_info) | set(st.setting_val):
                info = dict(st.setting_info.get(sid, {}))
                if sid in st.setting_val:
                    info["raw"] = st.setting_val[sid]
                s[sid] = info
            n = sum(1 for v in s.values() if v.get("sc"))
            self._progress = (f"decoded {n} settings from the log" if n
                              else "no settings in the log (read the settings with the monitored program)")
            self.btn_settings.config(state="normal")
            if n:
                self._show_settings(s)

    def _show_settings(self, s):
        from tkinter import ttk, filedialog
        import mk2_settings_view as vv
        nb = self.settings_notebook
        current = nb.index("current") if nb.tabs() else 0
        for t in nb.tabs():  # clear the previous read
            w = nb.nametowidget(t)
            nb.forget(t)
            w.destroy()
        # in direct mode the tabs are editable (step 1 fields), in passive mode read-only
        self.ve_pages = vv.build_tabs(nb, vv.Model(s), editable=hasattr(self.tail, "write_settings"))
        raw_tab = tk.Frame(nb, bg=BG)
        nb.add(raw_tab, text="All settings")
        nb.select(min(current, len(nb.tabs()) - 1))
        win = raw_tab  # parent of the CSV dialog
        cols = ("id", "name", "value", "unit", "default", "min", "max", "raw", "scale", "offs", "tab", "tabvalue")
        heads = ("ID", "name", "value", "unit", "default", "min", "max", "raw", "scale", "offset",
                 "Settings tab", "Tab value")
        widths = (40, 220, 75, 40, 75, 65, 65, 55, 65, 50, 270, 110)

        # scaled values for all (the derived fields of the tab value need the other settings)
        scaled = {}
        for sid, info in s.items():
            sc, raw = info.get("sc"), info.get("raw")
            sc_f = d.setting_scale(sc) if sc else None
            if sc_f is not None and raw is not None:
                scaled[sid] = sc_f * (raw + d.s16(info.get("offset", 0)))
        # small height: the tree stretches to the size of the tab (sticky nsew) and does not grow the window
        tree = ttk.Treeview(raw_tab, columns=cols, show="tree headings", height=10)
        tree.column("#0", width=20, stretch=False)
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w" if c == "name" else "e")
        tree.tag_configure("changed", background=CHANGED_BG)
        tree.tag_configure("flagon", foreground=FG)
        tree.tag_configure("flagoff", foreground=STALE_FG)
        tree.tag_configure("warn", foreground=ERR_FG)
        sb = ttk.Scrollbar(raw_tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        raw_tab.grid_rowconfigure(0, weight=1)
        raw_tab.grid_columnconfigure(0, weight=1)

        rows_csv = []
        for sid in sorted(s):
            info = s[sid]
            sc = info.get("sc")
            if not sc:
                continue
            scale = d.setting_scale(sc)
            off = d.s16(info.get("offset", 0))

            def disp(x):
                return "-" if x is None or scale is None else d.fmt(scale * (x + off))
            raw = info.get("raw")
            name = d.SETTING_NAMES.get(sid, f"setting_{sid}")
            unit = SETTING_UNITS.get(sid, "")
            changed = raw is not None and info.get("default") is not None and raw != info["default"]
            if sid in FLAG_SETTINGS:  # flags as hex
                def disp(x):  # noqa: F811
                    return "-" if x is None else f"0x{x:04X}"
            ui_tab, ui_name, ui_val = "", "", ""
            if sid in SETTINGS_UI_NAMES and sid in scaled:
                tab, label, conv = SETTINGS_UI_NAMES[sid]
                ui_tab, ui_name = tab, label
                try:
                    ui_val = conv(scaled[sid], scaled)
                except Exception:  # noqa: BLE001
                    ui_val = "?"
            vals = (sid, name, disp(raw), unit, disp(info.get("default")), disp(info.get("min")),
                    disp(info.get("max")), "-" if raw is None else raw, d.fmt(scale), off,
                    f"{ui_tab}: {ui_name}" if ui_name else "", ui_val)
            node = tree.insert("", "end", values=vals, tags=("changed",) if changed else ())
            rows_csv.append(vals)
            if sid in FLAG_SETTINGS and raw is not None:
                base, mask = FLAG_SETTINGS[sid], info.get("max", 0xFFFF)
                default = info.get("default", 0)
                for b in range(16):
                    if not (mask >> b) & 1:
                        continue
                    on = raw >> b & 1
                    fname = d.FLAG_NAMES.get(base + b, "?")
                    # varoitus korvaa harmaan, muuten ttk-tagien prioriteetti peittaa punaisen
                    tags = ["warn" if "NotPromoted" in fname else ("flagon" if on else "flagoff")]
                    if on != (default >> b & 1):
                        tags.append("changed")
                    if "NotPromoted" in fname:
                        fname = "⚠ " + fname
                    ui_txt, ui_box = "", ""
                    if base + b in SETTINGS_UI_FLAGS:
                        tab, label, inv = SETTINGS_UI_FLAGS[base + b]
                        checked = (not on) if inv else bool(on)
                        ui_txt = f"{tab}: {label}" + ("  (inverted)" if inv else "")
                        ui_box = "☑" if checked else "☐"
                    tree.insert(node, "end", values=("", f"  flag[{base + b}] {fname}", "ON" if on else "off", "",
                                                     "ON" if default >> b & 1 else "off", "", "", "", "", "",
                                                     ui_txt, ui_box),
                                tags=tuple(tags))
                tree.item(node, open=True)

        bar = tk.Frame(raw_tab, bg=BG)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        ts = time.strftime("%H:%M:%S")
        tk.Label(bar, text=f"Read {ts}.  All settings: orange = differs from default, red = 'not promoted'.",
                 anchor="w", bg=BG, fg=MUTED).pack(side="left", padx=6, pady=4)

        def save_csv():
            p = filedialog.asksaveasfilename(parent=win, defaultextension=".csv",
                                             initialfile=f"settings_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(";".join(heads) + "\n")
                    for r in rows_csv:
                        f.write(";".join(str(x) for x in r) + "\n")
        ttk.Button(bar, text="Save CSV", command=save_csv).pack(side="right", padx=6, pady=4)

    # ---------------------------------------------------------- writing settings (direct mode)
    def _open_write_dialog(self):
        from tkinter import messagebox
        s = getattr(self.tail, "settings", None)
        if not s:
            messagebox.showinfo("Write settings", "Read the settings first (Read settings).", parent=self.root)
            return
        if getattr(self, "_write_win", None) and self._write_win.winfo_exists():
            self._write_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._write_win = win
        win.title("Raw editor - write settings to the device")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, width=760, height=520, highlightthickness=0, bg=CARD)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        body = tk.Frame(cv, bg=CARD)
        body.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=body, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        win.bind("<MouseWheel>", lambda e: cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        mono = ("Consolas", 9)
        entries, flag_vars = {}, {}

        def lab(text, **kw):
            kw.setdefault("bg", CARD)
            kw.setdefault("fg", FG)
            return tk.Label(body, text=text, **kw)

        lab("Flags (Flags0/Flags1, checkboxes) are at the end of the list - scroll down.",
            font=(FONT, 9), fg=ERR_FG).grid(row=0, column=0, columnspan=7, sticky="w", padx=3)
        r = 1
        for col, h in enumerate(("ID", "name", "Settings tab", "now", "new", "unit", "min..max")):
            lab(h, font=(FONT, 9, "bold")).grid(row=r, column=col, sticky="w", padx=3)
        r += 1
        for sid in sorted(s):
            info = s[sid]
            if not info.get("sc") or sid not in WRITE_IDS or sid in FLAG_SETTINGS:
                continue
            cur = dlg_display(self.tail, info, info.get("raw"))
            lo = dlg_display(self.tail, info, info.get("min"))
            hi = dlg_display(self.tail, info, info.get("max"))
            ui_info = SETTINGS_UI_NAMES.get(sid)
            lab(str(sid), font=mono).grid(row=r, column=0, sticky="e", padx=3)
            lab(d.SETTING_NAMES.get(sid, ""), font=mono).grid(row=r, column=1, sticky="w", padx=3)
            lab(f"{ui_info[0]}: {ui_info[1]}" if ui_info else "", font=(FONT, 8), fg=MUTED).grid(
                row=r, column=2, sticky="w", padx=3)
            lab("-" if cur is None else d.fmt(cur), font=mono).grid(row=r, column=3, sticky="e", padx=3)
            var = tk.StringVar(value="" if cur is None else d.fmt(cur))
            e = tk.Entry(body, textvariable=var, width=9, font=mono, justify="right", relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            e.grid(row=r, column=4, padx=3)

            def mark(*_a, e=e, v=var, c=("" if cur is None else d.fmt(cur))):
                ch = v.get().strip() != c
                e.config(bg=CHANGED_BG if ch else "white", highlightbackground=CHANGE if ch else BORDER)
            var.trace_add("write", mark)
            lab(dlg_unit(sid, info), font=mono).grid(row=r, column=5, sticky="w")
            rng = f"{d.fmt(lo)}..{d.fmt(hi)}" if lo is not None else ""
            if is_vs_seconds(info):
                rng += "  (-1 = off)"
            lab(rng, font=mono, fg=MUTED).grid(row=r, column=6, sticky="w", padx=3)
            entries[sid] = var
            r += 1

        for sid in (0, 1):
            info = s.get(sid, {})
            if info.get("raw") is None:
                continue
            base = FLAG_SETTINGS[sid]
            lab(f"{d.SETTING_NAMES[sid]} (now 0x{info['raw']:04X}) - checkboxes",
                font=(FONT, 9, "bold")).grid(row=r, column=0, columnspan=7, sticky="w", pady=(10, 2))
            r += 1
            for b in range(16):
                if not FLAG_EDIT_MASK[sid] >> b & 1:
                    continue
                n = base + b
                on = info["raw"] >> b & 1
                if n in SETTINGS_UI_FLAGS:
                    tab, label, inv = SETTINGS_UI_FLAGS[n]
                    text = f"[{n}] {tab}: {label}" + ("  (inverted bit)" if inv else "")
                else:
                    inv, text = False, f"[{n}] {d.FLAG_NAMES.get(n, '?')}"
                if n == 3:
                    text += "  (bit 7 is inverted automatically)"
                var = tk.BooleanVar(value=bool(on) != inv)
                tk.Checkbutton(body, text=text, variable=var, font=mono, anchor="w", bg=CARD,
                               activebackground=CARD, bd=0).grid(
                    row=r, column=0, columnspan=7, sticky="w", padx=20)
                flag_vars[(sid, b)] = (var, inv)
                r += 1
        lab("Bits 7 (pair of bit 3), 9, 10, 12 ('Not promoted') and 15 are kept from the device value.",
            font=(FONT, 8), fg=ERR_FG).grid(row=r, column=0, columnspan=7, sticky="w", pady=(4, 0))

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x")
        mode = tk.StringVar(value="modified")
        tk.Radiobutton(bar, text="Modified only", variable=mode, value="modified", bg=BG,
                       activebackground=BG).pack(side="left", padx=6)
        tk.Radiobutton(bar, text="All settings (send all)", variable=mode, value="all", bg=BG,
                       activebackground=BG).pack(side="left")
        status = tk.Label(bar, text="", bg=BG, fg=MUTED)
        status.pack(side="left", padx=10)

        def collect():
            edits = {sid: v.get() for sid, v in entries.items()}
            wanted = {}
            for (sid, b), (var, inv) in flag_vars.items():
                bit = (not var.get()) if inv else var.get()
                wanted.setdefault(sid, s[sid]["raw"])
                wanted[sid] = (wanted[sid] & ~(1 << b)) | (int(bit) << b)
            return compute_changes(self.tail, s, edits, wanted, mode.get() == "all")

        def send():
            changes, errs = collect()
            self._confirm_and_write(changes, errs, win, send_btn)

        send_btn = ttk.Button(bar, text="Preview and write...", style="Accent.TButton", command=send)
        send_btn.pack(side="right", padx=6, pady=6)
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right", pady=6)

    def preview_lines(self, changes):
        s = self.tail.settings
        lines = []
        for sid in [i for i in WRITE_IDS if i in changes]:
            info, new = s[sid], changes[sid]
            if sid in FLAG_SETTINGS:
                final = self.tail.merge_flags(sid, info["raw"], new)
                diff = info["raw"] ^ final
                names = []
                for b in range(16):
                    if diff >> b & 1:
                        n = FLAG_SETTINGS[sid] + b
                        if n in SETTINGS_UI_FLAGS:
                            tab, label, inv = SETTINGS_UI_FLAGS[n]
                            on = bool(final >> b & 1) != inv
                            names.append(f"{label} {'☑' if on else '☐'}")
                        else:
                            names.append(f"[{n}] {d.FLAG_NAMES.get(n, '?')} {'ON' if final >> b & 1 else 'off'}")
                lines.append(f"{sid} {d.SETTING_NAMES[sid]}: 0x{info['raw']:04X} -> 0x{final:04X}  ({', '.join(names)})")
            else:
                a, b = dlg_display(self.tail, info, info.get("raw")), dlg_display(self.tail, info, new)
                lines.append(f"{sid} {d.SETTING_NAMES.get(sid)}: {d.fmt(a)} -> {d.fmt(b)} {dlg_unit(sid, info)}"
                             f"   (raw {info.get('raw')} -> {new})")
        return lines

    def _confirm_and_write(self, changes, errs, parent, button=None):
        """Common sending chain (raw editor and tabs): check -> preview -> write
        -> read-back check -> 'Reset the device now?'."""
        from tkinter import messagebox
        import threading
        if not errs:
            errs = self.tail.validate_write(changes)
        if errs:
            messagebox.showerror("Write settings", "\n".join(errs), parent=parent)
            return
        if not changes:
            messagebox.showinfo("Write settings", "No changes.", parent=parent)
            return
        lines = self.preview_lines(changes)
        # save the flag values before writing (the reset question looks at the changed bits)
        s_before = {sid: dict(self.tail.settings[sid]) for sid in changes if sid in self.tail.settings}
        # LiFePO4 protection: the presets of lead-acid batteries (28.8-30.0 V) exceed the absorption level of LiFePO4
        s = self.tail.settings
        for sid, lim, name in ((2, LIFEPO4_MAX_ABS_V, "Absorption"), (3, LIFEPO4_MAX_FLOAT_V, "Float")):
            if sid in changes:
                v = dlg_display(self.tail, s[sid], changes[sid])
                if v is not None and v > lim:
                    lines.append(f"⚠ {name} {d.fmt(v)} V > {lim} V: too high for a LiFePO4 battery!")
        shown = lines if len(lines) <= 25 else lines[:25] + [f"... and {len(lines) - 25} more"]
        if not messagebox.askyesno("Write settings", f"Writing {len(lines)} settings to the device:\n\n"
                                   + "\n".join(shown) + "\n\nContinue?", parent=parent):
            return
        if button:
            button.config(state="disabled")

        def progress(i, n):
            self._progress = f"writing {i}/{n}..."

        def done(res, bad):
            if button and button.winfo_exists():
                button.config(state="normal")
            self._show_settings(self.tail.settings)
            if bad or getattr(self.tail, "settings_dirty", False):
                # should not happen (the link stops at the first failure), but never offer a reset after one
                messagebox.showwarning("Write settings", "Read-back mismatch:\n" + "\n".join(
                    f"{sid} {d.SETTING_NAMES.get(sid)}: wrote {w}, read {b}" for sid, _o, w, b, _ in bad)
                    + "\n\nThe device was NOT reset. Read the settings and check them.", parent=parent)
                self._progress += " (not reset)"
                return
            untested = untested_for_reset(self.tail, s_before, changes)
            if not untested:
                # all changed settings have been verified on the real device to work without a reset
                self._progress += " (no reset needed)"
                return
            names = ", ".join(untested)
            if messagebox.askyesno("Write settings", "Settings written.\n\nReset the device now?\n\n"
                                   f"The effect of these without a reset has not been tested:\n{names}\n\n"
                                   "(UInvSetpoint and "
                                   "IBatBulk are known to work without a reset.)", parent=parent):
                def do_reset():
                    try:
                        ok = self.tail.reset()
                        self._progress = "reset sent" + ("" if ok else " (S acknowledgement missing)")
                    except Exception as e:  # noqa: BLE001
                        self._progress = f"reset ERROR: {e}"
                threading.Thread(target=do_reset, daemon=True).start()
            else:
                self._progress += " (not reset)"

        def fail(e):
            if button and button.winfo_exists():
                button.config(state="normal")
            import mk2_link
            if isinstance(e, mk2_link.WriteAborted):
                if self.tail.settings:
                    self._show_settings(self.tail.settings)
                messagebox.showerror("Write settings", write_aborted_text(e), parent=parent)
            else:
                messagebox.showerror("Write settings", str(e), parent=parent)

        def work():
            import mk2_link
            try:
                res = self.tail.write_settings(changes, progress)
                bad = [x for x in res if not x[4]]
                self._progress = f"wrote {len(res)} settings" + (f", {len(bad)} ERRORS" if bad else ", OK")
                self.root.after(0, lambda: done(res, bad))
            except mk2_link.WriteAborted as e:
                ok_n = sum(1 for x in e.results if x[4])
                self._progress = f"write STOPPED at {e.failed_id}: {ok_n} written, {len(e.not_written)} not written"
                self.root.after(0, lambda: fail(e))
            except Exception as e:  # noqa: BLE001
                self._progress = f"write ERROR: {e}"
                self.root.after(0, lambda: fail(e))
        threading.Thread(target=work, daemon=True).start()

    def tab_changes(self):
        """Changes of the tabs -> ({id: raw}, errors)."""
        import mk2_settings_view as vv
        ui = getattr(self.settings_notebook, "settings_ui", None)
        s = getattr(self.tail, "settings", None)
        if ui is None or not s:
            return {}, ["read the settings first (Read settings)"]
        edits, raws, flags, errs2 = vv.collect(ui, s)
        changes, errs = compute_changes(self.tail, s, edits, flags)
        changes.update(raws)
        return changes, errs + errs2

    def _send_tabs(self):
        changes, errs = self.tab_changes()
        self._confirm_and_write(changes, errs, self.root, getattr(self, "btn_send_tabs", None))

    def _click_switch(self, state):
        # send in a background thread so that the UI does not freeze during the serial traffic
        import threading

        def work():
            try:
                self.tail.set_switch(state)
                save_panel_state({"switch": state})   # restored at the next start
                self._saved_switch = state
            except Exception as e:  # noqa: BLE001
                self._progress = f"switch: {e}"
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- update
    def tick(self, root):
        if hasattr(self.tail, "poll"):
            self.tail.poll()
        self._sync_conn_controls()
        self.refresh()
        self._tick_n += 1
        if self._tick_n % 5 == 0:
            self._update_change_bar()
        root.after(100, self.tick, root)

    def _update_change_bar(self):
        """Orange bar when the tabs have unsaved changes ('Review...' = write chain)."""
        if not hasattr(self, "change_bar"):
            return
        n = 0
        if getattr(self.settings_notebook, "settings_ui", None) is not None and getattr(self.tail, "settings", None):
            try:
                changes, errs = self.tab_changes()
                n = len(changes) or len(errs)
            except Exception:  # noqa: BLE001 - the bar must not crash the update
                n = 0
        if n:
            self.change_label.config(text=f"{n} unsaved change{'s' if n != 1 else ''}")
            self.change_bar.grid()
        else:
            self.change_bar.grid_remove()

    def refresh(self):
        now = time.time()
        t = self.tail
        if 0 in t.values and t.values[0][1] is not None:
            _STATE["umains"] = t.values[0][1]
        for row in ROWS:
            if row is None:
                continue
            _, rid, unit, f = row
            val, raw, age, unit = self.cells[rid]
            if rid not in t.values:
                continue
            r, v, ts = t.values[rid]
            a = now - ts
            color = FG if a < STALE_S else STALE_FG
            txt = "?" if v is None else f(v, r)
            if txt.startswith("-") and txt.strip("-0.") == "":  # "-0.0" -> "0.0"
                txt = txt[1:]
            if rid == 5 and v is not None and v > 0 and txt.strip("0.") != "":
                txt = "+" + txt   # battery current: charging in green with a + sign, discharging as normal
                if a < STALE_S:
                    color = GOOD_FG
            val.config(text=f"{txt} {unit}", fg=color)
            if raw is not None:
                raw.config(text=str(r), fg=color)
                age.config(text=f"{a:4.1f}", fg=color)

        c = self.canvas
        if t.led:
            lo, hi, ts = t.led
            stale = now - ts > 10
            word = lo | hi << 8
            for name, (oval, on_col, bit) in self.leds.items():
                on = bit is not None and (word >> bit) & 1
                c.itemconfig(oval, fill=on_col if on and not stale else LED_OFF[on_col])
            if self.debug:
                unknown = [b for b in range(16) if word >> b & 1 and b not in KNOWN_LED_BITS | LED_ALWAYS_ON]
                extra = f"  UNKNOWN bit {unknown}" if unknown else ""
                c.itemconfig(self.led_raw, text=f"0x81: lo={lo:08b} hi={hi:08b}  ({now - ts:.1f} s){extra}")
        elif self.debug:
            c.itemconfig(self.led_raw, text="LED: no data (open the DMC panel in the monitored program)")

        self._draw_knob(now)
        sent = t.switch[0] if t.switch else None
        dev = None
        master = getattr(t, "master", None)
        if master and "switch_reg" in master:
            reg = master["switch_reg"]
            dev = {(1, 1): 3, (1, 0): 1, (0, 1): 2, (0, 0): 4}[(reg >> 4 & 1, reg >> 5 & 1)]
        shown = dev if dev is not None else sent
        # The device does not report the switch position. If it has not been sent yet, it is inferred from the LEDs:
        # the candidate buttons get a dashed outline, and no button is shown as selected.
        guess = None
        if shown is None and t.led and now - t.led[2] <= 10:
            um = t.values.get(0)
            um = um[1] if um and now - um[2] <= 10 else None
            import mk2_link
            guess = mk2_link.infer_switch(t.led[0] | t.led[1] << 8, um)
        cand = guess[0] if guess else set()
        # The LEDs do not distinguish e.g. on / charger only (mains on is lit in both). The last selected
        # state (state file) narrows the candidates to one if it fits the LEDs. Nothing is sent to the device:
        # one button is shown as selected with a dashed outline = assumed, not confirmed.
        assumed = None
        if cand:
            saved = self._saved_switch
            if saved in cand:
                assumed = saved
            elif len(cand) == 1:
                assumed = next(iter(cand))
        if assumed is not None:
            shown, cand = assumed, {assumed}
        for state, (rect, txt) in self.sw_items.items():
            active = state == shown
            # fill = state reported by the device; outline = last sent (if different)
            if state == assumed:
                outline = "white"              # assumed: selected + white dashed outline
            elif state == sent and sent != shown:
                outline = "#ffb000"
            else:
                outline = SWITCH_ON if active or state in cand else "#666"
            c.itemconfig(rect, fill=SWITCH_ON if active else DMC_BG, outline=outline,
                         width=2 if state == sent or state in cand else 1, dash=(4, 2) if state in cand else ())
            c.itemconfig(txt, fill="black" if active else "#888")
        sw = shown

        scales = "scales OK" if all(i in t.ram_info for i in range(10) if i != 3) else "waiting for scales (0x36)"
        mk2 = f"MK2 {t.mk2_version} mode '{t.mk2_mode}'" if t.mk2_version else "MK2 ?"
        fw = (t.fw.get(0x82, 0) | t.fw.get(0x83, 0) << 16) if 0x82 in t.fw and 0x83 in t.fw else None
        if t.switch:
            sw_txt = f"switch={SWITCH.get(sw, sw)} ({now - t.switch[1]:.0f} s ago)"
        elif assumed is not None:
            sw_txt = f"switch={SWITCH.get(assumed)} (assumed from LEDs + last choice, not sent)"
        else:
            sw_txt = "switch=?"
        src = self.logname
        if self.conn is not None:
            logf = getattr(t, "logf", None)
            log = os.path.basename(logf.name) if logf is not None and logf.name != os.devnull else ""
            src = f"{self.port or 'not connected'}" + (f" -> {log}" if log else "")
        if self.debug:
            line = f"{src}  |  {scales}  |  {mk2}  |  device fw {fw or '?'}  |  {sw_txt}"
        else:
            line = f"{src}  |  {scales}  |  {sw_txt}"
        if master and "switch_reg" in master:
            reg = master["switch_reg"]
            front = {(1, 0): "up", (0, 1): "down", (0, 0): "middle", (1, 1): "?"}[(reg >> 2 & 1, reg >> 3 & 1)]
            line += (f"\nSwitch reg 0x{reg:02X} = {reg:08b}  (charge={reg >> 4 & 1} invert={reg >> 5 & 1}, "
                     f"front panel={front}, remote c/i={reg & 1}/{reg >> 1 & 1})  |  "
                     f"AC-in limit {master['act_a']:.1f} A (min {master['min_a']:.1f}, max {master['max_a']:.1f})  "
                     f"[{now - master['ts']:.0f} s]")
        elif master:
            line += f"\nMasterMultiLED: {master.get('error')} {master.get('raw')}"
        if self.debug and getattr(t, "cycle_s", None):
            line += f"\npoll cycle {t.cycle_s:.2f} s"
        if getattr(t, "error", None):
            line += f"\nERROR: {t.error}"
        self.info.config(text=line, fg=ERR_FG if getattr(t, "error", None) else MUTED)
        self._update_conn(now)
        if hasattr(self, "settings_status"):
            self.settings_status.config(text=self._progress)


def main():
    """
    python mk2_panel.py [--mqtt] [--config mk2.toml]
                                           direct mode without a connection: the port (or 'emulator') is chosen
                                           in the settings area, Connect opens the connection. The switch is NOT restored.
    python mk2_panel.py --log [capture_X.log]
                                           passive: the newest/given capture_*.log (written by mk2_proxy.py)
    python mk2_panel.py capture_X.log      passive, given log
    Option --debug (in all modes): shows developer numbers (RAM raw/age, LED word,
    knob 0x04 value, MK2 version/mode, device fw, polling cycle).
    python mk2_panel.py --direct [PORT] [--mqtt] [--config mk2.toml]
                                           connects to the MK2 right at start-up. Monitor only unless
                                           mk2.toml [control] enabled = true; then the saved switch and
                                           knob are restored (as before). PORT can be 'emulator'.
    Option --allow-unknown-firmware (RISKY): allow writing, reset and control on device firmware
    that has not been verified (default: read-only).
                                           Settings from mk2.toml (port, limits, MQTT, alarms).
                                           --mqtt: values to Venus (bridge in the same process)
                                           (the old form --mqtt file.json still works for the MQTT part)
    """
    here = os.path.dirname(os.path.abspath(__file__))
    debug = "--debug" in sys.argv
    if debug:
        sys.argv.remove("--debug")
    unknown_fw = "--allow-unknown-firmware" in sys.argv
    if unknown_fw:
        sys.argv.remove("--allow-unknown-firmware")
        print("WARNING: --allow-unknown-firmware: writing and control are allowed on unverified firmware",
              file=sys.stderr)
    args = sys.argv[1:]
    passive = bool(args) and (args[0] == "--log" or args[0].lower().endswith(".log"))
    if not passive:
        # direct mode. Without --direct the panel opens without a connection (the port is chosen in the settings area).
        import mk2_config
        import mk2_mqtt
        direct = bool(args) and args[0] == "--direct"
        rest = args[1:] if direct else args
        cfg_path = rest[rest.index("--config") + 1] if "--config" in rest and rest.index("--config") + 1 < len(rest) else None
        try:
            app = mk2_config.load(cfg_path)          # a settings error stops the program before the COM port is opened
        except mk2_config.ConfigError as e:
            sys.exit(f"Config error: {e}")
        mqtt_cfg = None
        want_mqtt = "--mqtt" in rest
        if want_mqtt:
            i = rest.index("--mqtt")
            legacy = rest[i + 1] if i + 1 < len(rest) and rest[i + 1].lower().endswith(".json") else None
            mqtt_cfg = mk2_mqtt.load_config(legacy) if legacy else None   # None -> mk2.toml [mqtt]
        global LIFEPO4_MAX_ABS_V, LIFEPO4_MAX_FLOAT_V
        LIFEPO4_MAX_ABS_V, LIFEPO4_MAX_FLOAT_V = app.battery.max_absorption_v, app.battery.max_float_v
        conn = LinkManager(app, here, mqtt_cfg, allow_unknown_firmware=unknown_fw)
        if want_mqtt:
            save_panel_state({"mqtt": True})        # the MQTT box is ticked
        port = None
        if direct:
            port = rest[0] if rest and not rest[0].startswith("--") else app.serial.port
            try:
                # --direct: with [control] enabled the saved switch is restored too (Mode known on Venus);
                # monitor only (default) sends nothing
                conn.connect(port, mqtt=want_mqtt, restore_switch=True)
            except Exception as e:  # noqa: BLE001 - the panel opens anyway, the port can be chosen again
                print(f"connect {port}: {e}", file=sys.stderr)
        title = f"direct [{os.path.basename(app.path) if app.path else 'default settings'}]"
        _windows_app_id()
        root = tk.Tk()
        panel = Panel(root, None, title, debug=debug, conn=conn)
        if conn.error:
            panel._progress = f"ERROR: {conn.error}"

        def on_close():
            panel.save_window_size()
            conn.disconnect()    # first connected:0 to Venus, then the port is closed
            root.destroy()
        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
        return
    if args[0] == "--log":
        args = args[1:]
    if args:
        path = args[0]
    else:
        logs = sorted(glob.glob(os.path.join(here, "capture_*.log")), key=os.path.getmtime)
        logs = [p for p in logs if "direct" not in os.path.basename(p)]
        if not logs:
            sys.exit("no capture_*.log found - start mk2_proxy.py first")
        path = logs[-1]
    tail = LogTail(path)
    tail.poll()  # history: scales, version, latest values
    _windows_app_id()
    root = tk.Tk()
    panel = Panel(root, tail, path, debug=debug)

    def on_close():
        panel.save_window_size()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
