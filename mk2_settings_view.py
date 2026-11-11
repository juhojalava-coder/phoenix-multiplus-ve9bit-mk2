"""Settings view with tabs (read-only, or with editable=True: bound fields can be edited).

Tabs and fields follow the layout of the official configuration software (reference screenshots,
29.10.2026); the values are taken from the device
settings. All mappings were verified with write tests unless noted otherwise.

layout(model) returns the pages as data -> testable without a GUI.
build_tabs(notebook, model) builds the ttk tabs.

Conversion rules (verified):
  - current as W: A * 120 V (nominal device voltage, not UInvSetpoint)
  - virtual switch (VS) times in seconds: raw - 1  (raw 0 = -1 = condition disabled)
  - VS times in minutes (ID 36, 37): raw - 1
  - hysteresis: low connect = 44 + 45, high connect = 46 - 47, DC restart = 11 + 12
  - inverted checkboxes: UPS function (b3), Stop after bulk (b4), Enable charger (b6),
    enable AES (b8), Ground relay (b13)
"""
import tkinter as tk
from tkinter import ttk

import mk2_decode as d

NOMINAL_V = 120.0  # W <-> A conversion uses the fixed nominal voltage

CHARGE_CURVES = {1: "Fixed", 2: "Adaptive", 3: "Adaptive + BatterySafe"}

# Battery type presets (collected from the official configuration software, 29.10.2026):
# (curve, absorption V, float V, rep. abs. time min, rep. abs. interval min, (max) abs. time min)
BATTERY_PRESETS = [
    ((1, 28.40, 27.00, 60, 10080, 60), "Lithium Iron Phosphate (LiFePo4) batteries"),
    ((3, 28.20, 27.60, 60, 10080, 480), "Gel MK Battery"),
    ((3, 28.40, 27.60, 60, 10080, 480), "Gel Exide A400"),
    ((3, 28.80, 27.60, 60, 10080, 480), "Gel/AGM Victron Energy, Gel Exide A200"),
    ((3, 28.80, 27.60, 60, 10080, 300), "Flooded deep discharge flat plate lead antimony"),
    ((3, 29.40, 27.60, 60, 10080, 300), "AGM Victron Deep Discharge (fastest recharge), "
                                        "Tubular plate batteries (semi float use), AGM spiral cell"),
    ((3, 30.00, 27.60, 60, 10080, 360), "Flooded tubular plate"),
    ((1, 28.50, 28.50, 60, 10080, 60), "Redflow ZCell"),
]

# Light palette (shared with mk2_panel.py). FONT: if the font is missing, Tk picks its default font.
FONT = "Segoe UI"
BG = "#f3f5f8"          # page background
CARD = "#ffffff"        # cards
BORDER = "#e2e8f0"      # borders of cards and fields
FG = "#0f172a"
FG2 = "#475569"
MUTED = "#64748b"
ACCENT = "#2563eb"
CHANGE = "#ea580c"      # change marker (orange border)
DISABLED_FG = "#94a3b8"
CHANGED_BG = "#fff7ed"  # changed field (light orange background)


def _bg(parent):
    """Background colour of the parent (ttk widgets have no bg option -> page background)."""
    try:
        return parent.cget("bg")
    except tk.TclError:
        return BG


def apply_style(master=None):
    """ttk styles (clam): flat tabs, white combobox, buttons. Idempotent."""
    st = ttk.Style(master)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    try:
        from tkinter import font as tkfont
        tkfont.nametofont("TkDefaultFont").configure(family=FONT)
        tkfont.nametofont("TkTextFont").configure(family=FONT)
    except tk.TclError:
        pass
    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0), lightcolor=BG,
                 darkcolor=BG, bordercolor=BG)
    st.configure("TNotebook.Tab", background=BG, foreground=FG2, borderwidth=0, padding=(14, 7),
                 lightcolor=BG, darkcolor=BG, bordercolor=BG, focuscolor=BG, font=(FONT, 10))
    st.map("TNotebook.Tab", foreground=[("selected", ACCENT), ("active", FG)],
           background=[("selected", BG), ("active", BG)], bordercolor=[("selected", BG)],
           lightcolor=[("selected", BG)], darkcolor=[("selected", BG)],
           # the clam theme changes the padding of the selected tab and expands it, which moves the text
           # down; bold would widen the tab. Selected = blue text only.
           padding=[("selected", (14, 7))], expand=[("selected", (0, 0, 0, 0))],
           font=[("selected", (FONT, 10))])
    st.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG, bordercolor=BORDER,
                 lightcolor=CARD, darkcolor=CARD, arrowcolor=FG2, padding=3)
    st.map("TCombobox", fieldbackground=[("readonly", CARD), ("disabled", "#f1f5f9")],
           foreground=[("disabled", DISABLED_FG)], bordercolor=[("focus", ACCENT)],
           selectbackground=[("readonly", CARD)], selectforeground=[("readonly", FG)])
    st.configure("TButton", background=CARD, foreground=FG, bordercolor=BORDER, lightcolor=CARD,
                 darkcolor=CARD, focuscolor=CARD, padding=(10, 5), relief="flat", font=(FONT, 9))
    st.map("TButton", background=[("active", "#eef2f7"), ("disabled", "#f8fafc")],
           foreground=[("disabled", DISABLED_FG)])
    st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                 lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT, padding=(10, 5), font=(FONT, 9, "bold"))
    st.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#93b4f0")],
           foreground=[("disabled", "#ffffff")], bordercolor=[("disabled", "#93b4f0")])
    st.configure("Change.TButton", background=CHANGE, foreground="#ffffff", bordercolor=CHANGE,
                 lightcolor=CHANGE, darkcolor=CHANGE, focuscolor=CHANGE, padding=(10, 3), font=(FONT, 9, "bold"))
    st.map("Change.TButton", background=[("active", "#c2410c")])
    st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, bordercolor=BORDER,
                 rowheight=20)
    st.configure("Treeview.Heading", background="#e9eef5", foreground=FG2, bordercolor=BORDER, relief="flat")
    st.configure("Vertical.TScrollbar", background=BORDER, troughcolor=BG, bordercolor=BG, arrowcolor=FG2)
    return st


class Card(tk.Frame):
    """Card: white frame with a thin border and a bold title inside the card.
    self is the INNER frame (children are created here, like in a LabelFrame); grid/pack/place are forwarded
    to the outer frame, so the card can be placed like any frame."""

    def __init__(self, parent, title=None):
        self._outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        super().__init__(self._outer, bg=CARD)
        if title:
            tk.Label(self._outer, text=title, bg=CARD, fg=FG, font=(FONT, 10, "bold"), anchor="w").pack(
                fill="x", padx=12, pady=(8, 0))
        tk.Frame.pack(self, fill="both", expand=True, padx=12, pady=(4, 8))

    def grid(self, *a, **kw):
        self._outer.grid(*a, **kw)

    grid_configure = grid

    def pack(self, *a, **kw):
        self._outer.pack(*a, **kw)

    pack_configure = pack

    def place(self, *a, **kw):
        self._outer.place(*a, **kw)

    place_configure = place

    def grid_remove(self):
        self._outer.grid_remove()

    def grid_forget(self):
        self._outer.grid_forget()

    def pack_forget(self):
        self._outer.pack_forget()

# ---- editable fields (step 1: direct fields, checkboxes, choices) --------------------
# (tab, title) -> setting ID. The field text is in display units (scale*(raw+offset)).
# Derived fields (hysteresis, Hr/Days, VS times/W) are not here: see EDIT_DERIVED.
EDIT_VALUES = {
    ("General", "AC input current limit"): 6,
    ("Grid", "AC low disconnect"): 44,
    ("Grid", "AC high disconnect"): 46,
    ("Inverter", "Inverter output voltage"): 5,
    ("Inverter", "DC input low shut-down"): 11,
    ("Inverter", "Assist current boost factor"): 48,
    ("Charger", "Absorption voltage"): 2,
    ("Charger", "Float voltage"): 3,
    ("Charger", "Charge current"): 4,
}
# ---- step 2: derived fields -> raw (inverse conversions) ---------------------------
#   ("hyst", base, hyst, sign)   hyst = (field - base) * sign   (base may be edited at the same time)
#   ("scaled", id, factor)       display value = field * factor  (Hr -> min 60, Days -> min 1440)
#   ("watts", id)                A = W / 120
#   ("vs_s", id) / ("vs_min", id)     raw = field + 1  (-1 = off)
#   ("direct", id)                    display value = field
EDIT_DERIVED = {
    ("Grid", "AC low connect"): ("hyst", 44, 45, +1),
    ("Grid", "AC high connect"): ("hyst", 46, 47, -1),
    ("Inverter", "DC input low restart"): ("hyst", 11, 12, +1),
    ("Charger", "Repeated absorption time"): ("scaled", 7, 60),
    ("Charger", "Repeated absorption interval"): ("scaled", 8, 1440),
    ("Charger", "Absorption time"): ("scaled", 9, 60),
    ("Charger", "Maximum absorption time"): ("scaled", 9, 60),
    ("VS: options", "Do not switch off within"): ("direct", 43),
}
# Condition rows of the VS pages: (tab, title) -> (limit ID or None, limit is in watts, time ID, time in minutes)
VS_TIMED = {
    ("VS: A Set VS ON", "when load higher than"): (16, True, 19, False),
    ("VS: A Set VS ON", "when Udc lower than"): (18, False, 21, False),
    ("VS: A Set VS ON", "when Udc higher than"): (17, False, 20, False),
    ("VS: A Set VS ON", "when not charging for"): (None, False, 22, False),
    ("VS: A Set VS ON", "when fan on for"): (None, False, 23, False),
    ("VS: B Set VS OFF", "when load lower than"): (28, True, 31, False),
    ("VS: B Set VS OFF", "when Udc lower than"): (30, False, 33, False),
    ("VS: B Set VS OFF", "when Udc higher than"): (29, False, 32, False),
    ("VS: B Set VS OFF", "when charging for"): (None, False, 34, False),
    ("VS: B Set VS OFF", "when fan off for"): (None, False, 35, False),
    ("VS: B Set VS OFF", "when bulk charge finished for"): (None, False, 36, True),
    ("VS: B Set VS OFF", "when no VS ON condition for"): (None, False, 37, True),
    ("VS: B Set VS OFF", "when no AC input for"): (None, False, 38, False),
}
for (_tab, _label), (_thr, _w, _t, _min) in VS_TIMED.items():
    if _thr is not None:
        EDIT_DERIVED[(_tab, _label, "thr")] = ("watts", _thr) if _w else ("direct", _thr)
    EDIT_DERIVED[(_tab, _label, "t")] = ("vs_min", _t) if _min else ("vs_s", _t)
# LED alarm rows: (tab, base name) -> (pre-alarm flag bit, time ID)
VS_ALARMS = {
    ("VS: A Set VS ON", "Temperature"): (17, 24), ("VS: A Set VS ON", "Low-battery"): (18, 25),
    ("VS: A Set VS ON", "Overload"): (19, 26), ("VS: A Set VS ON", "Udc ripple"): (20, 27),
    ("VS: B Set VS OFF", "Temperature"): (21, 39), ("VS: B Set VS OFF", "Low-battery"): (22, 40),
    ("VS: B Set VS OFF", "Overload"): (23, 41), ("VS: B Set VS OFF", "Udc ripple"): (24, 42),
}
for (_tab, _name), (_bit, _t) in VS_ALARMS.items():
    EDIT_DERIVED[(_tab, _name, "t")] = ("vs_s", _t)

# (tab, title) -> (flag bit, inverted). Inverted: checked = bit 0.
EDIT_FLAGS = {
    ("General", "Dynamic current limiter"): (28, False),
    ("General", "Enable 2-3 phase"): (0, False),
    ("General", "Leader"): (1, False),
    ("Grid", "Accept wide input frequency range (45-65 Hz)"): (27, False),
    ("Grid", "UPS function"): (3, True),
    ("Inverter", "Ground relay"): (13, True),
    ("Inverter", "enable AES"): (8, True),
    ("Inverter", "PowerAssist"): (5, False),
    ("Charger", "Enable charger"): (6, True),
    ("Charger", "Weak AC input"): (14, False),
    ("Charger", "Stop after excessive bulk"): (4, True),
    ("Charger", "Storage mode"): (11, False),
    ("VS: Usage", "Invert virtual switch usage"): (26, False),
    ("VS: A Set VS ON", "set VS on when bulk protection is activated (charger stopped after 10Hr bulk)"): (16, False),
    ("VS: A Set VS ON", "set VS on when general system failure occurs"): (25, False),
}


class Model:
    """Settings {id: {sc, offset, raw, ...}} -> scaled values and flags."""

    def __init__(self, settings):
        self.s = settings

    def raw(self, sid):
        return self.s.get(sid, {}).get("raw")

    def val(self, sid):
        info = self.s.get(sid, {})
        raw, sc = info.get("raw"), info.get("sc")
        if raw is None or not sc:
            return None
        return d.setting_scale(sc) * (raw + d.s16(info.get("offset", 0)))

    def flag(self, bit):
        """Flag bit 0..31 (Flags0 = bits 0-15, Flags1 = 16-31)."""
        raw = self.raw(0 if bit < 16 else 1)
        return None if raw is None else bool(raw >> (bit % 16) & 1)

    def seconds(self, sid):
        """VS time in seconds: raw - 1 (-1 = disabled)."""
        raw = self.raw(sid)
        return None if raw is None else raw - 1

    def minutes(self, sid):
        raw = self.raw(sid)
        return None if raw is None else raw - 1

    def watts(self, sid):
        v = self.val(sid)
        return None if v is None else v * NOMINAL_V

    def battery_type(self):
        try:
            key = (int(self.val(10)), round(self.val(2), 2), round(self.val(3), 2),
                   round(self.val(7)), round(self.val(8)), round(self.val(9)))
        except TypeError:
            return "?"
        for preset, name in BATTERY_PRESETS:
            if preset == key:
                return name
        return "No corresponding default"


def _f(x, fmt):
    return "?" if x is None else format(x, fmt)


# Row types:
#   ("value", title, value, unit, active)
#   ("timed", title, limit, unit, time, time unit)   - VS condition; grey when time = -1
#   ("check", title, checked)
#   ("radio", title, [options], selected index)
#   ("combo", title, text)
#   ("text",  text)
#   ("group", title)   - subheading

def layout(m: Model):
    curve = m.raw(10)
    invert = m.flag(26)
    vs_relay = ("drive multifunctional (aux.) relay: VS on=open; VS off=close" if invert
                else "drive multifunctional (aux.) relay: VS on=close; VS off=open")
    vs_ac = ("ignore AC input: VS on=ignore; VS off=do not ignore" if invert
             else "ignore AC input: VS on=do not ignore; VS off=ignore")

    def alarm(name, bit):
        return f"{name} pre-alarm" if m.flag(bit) else f"{name} alarm"

    def timed(label, thr_sid, thr_fmt, unit, t_sid, watts=False, tunit="seconds", minutes=False):
        thr = m.watts(thr_sid) if watts else m.val(thr_sid) if thr_sid is not None else None
        t = m.minutes(t_sid) if minutes else m.seconds(t_sid)
        return ("timed", label, None if thr_sid is None else _f(thr, thr_fmt), unit, _f(t, "d"), tunit)

    return [
        ("General", [
            ("group", "System frequency"),
            ("radio", "", ["50Hz", "60Hz"], 1 if m.flag(2) else 0),
            ("group", "Shore limit"),
            ("value", "AC input current limit", _f(m.val(6), ".1f"), "A", True),
            ("check", "Dynamic current limiter", m.flag(28)),
            ("group", "Parallel systems"),
            ("value", "Number of slaves", _f(m.val(13), ".0f"), "", True),
            ("check", "Enable 2-3 phase", m.flag(0)),
            ("check", "Leader", m.flag(1)),
            ("radio", "", ["3 phase", "Split phase 180°", "Two leg 3 phase 120°"], m.raw(14) or 0),
        ]),
        ("Grid", [
            ("group", "Transfer switch"),
            ("check", "Accept wide input frequency range (45-65 Hz)", m.flag(27)),
            ("value", "AC low disconnect", _f(m.val(44), ".0f"), "V", True),
            ("value", "AC low connect", _f(None if m.val(44) is None else m.val(44) + m.val(45), ".0f"), "V", True),
            ("value", "AC high connect", _f(None if m.val(46) is None else m.val(46) - m.val(47), ".0f"), "V", True),
            ("value", "AC high disconnect", _f(m.val(46), ".0f"), "V", True),
            ("check", "UPS function", None if m.flag(3) is None else not m.flag(3)),
        ]),
        ("Inverter", [
            ("value", "Inverter output voltage", _f(m.val(5), ".0f"), "V", True),
            ("check", "Ground relay", None if m.flag(13) is None else not m.flag(13)),
            ("value", "DC input low shut-down", _f(m.val(11), ".2f"), "V", True),
            ("value", "DC input low restart",
             _f(None if m.val(11) is None else m.val(11) + m.val(12), ".2f"), "V", True),
            ("check", "enable AES", None if m.flag(8) is None else not m.flag(8)),
            ("check", "PowerAssist", m.flag(5)),
            ("value", "Assist current boost factor", _f(m.val(48), ".1f"), "", bool(m.flag(5))),
        ]),
        ("Charger", [
            ("check", "Enable charger", None if m.flag(6) is None else not m.flag(6)),
            ("check", "Weak AC input", m.flag(14)),
            ("check", "Stop after excessive bulk", None if m.flag(4) is None else not m.flag(4)),
            ("check", "Storage mode", m.flag(11)),
            ("value", "Battery type", m.battery_type(), "", True),
            ("combo", "Charge curve", CHARGE_CURVES.get(curve, f"? ({curve})")),
            ("value", "Absorption voltage", _f(m.val(2), ".2f"), "V", True),
            ("value", "Float voltage", _f(m.val(3), ".2f"), "V", True),
            ("value", "Charge current", _f(m.val(4), ".0f"), "A", True),
            ("value", "Repeated absorption time", _f(None if m.val(7) is None else m.val(7) / 60, ".2f"), "Hr", True),
            ("value", "Repeated absorption interval",
             _f(None if m.val(8) is None else m.val(8) / 1440, ".2f"), "Days", True),
            ("value", "Absorption time" if curve == 1 else "Maximum absorption time",
             _f(None if m.val(9) is None else m.val(9) / 60, ".0f"), "Hr", True),
        ]),
        ("VS: Usage", [
            ("group", "Specify virtual switch usage"),
            ("radio", "", ["Do not use VS", vs_relay, vs_ac], m.raw(15) or 0),
            ("check", "Invert virtual switch usage", invert),
        ]),
        ("VS: A Set VS ON", [
            timed("when load higher than", 16, ".0f", "W", 19, watts=True),
            timed("when Udc lower than", 18, ".2f", "V", 21),
            timed("when Udc higher than", 17, ".2f", "V", 20),
            timed("when not charging for", None, "", "", 22),
            timed("when fan on for", None, "", "", 23),
            ("check", "set VS on when bulk protection is activated (charger stopped after 10Hr bulk)", m.flag(16)),
            ("check", "set VS on when general system failure occurs", m.flag(25)),
            ("group", "when the following LED alarms are active"),
            ("timed", alarm("Temperature", 17), None, "", _f(m.seconds(24), "d"), "seconds"),
            ("timed", alarm("Low-battery", 18), None, "", _f(m.seconds(25), "d"), "seconds"),
            ("timed", alarm("Overload", 19), None, "", _f(m.seconds(26), "d"), "seconds"),
            ("timed", alarm("Udc ripple", 20), None, "", _f(m.seconds(27), "d"), "seconds"),
        ]),
        ("VS: B Set VS OFF", [
            timed("when load lower than", 28, ".0f", "W", 31, watts=True),
            timed("when Udc lower than", 30, ".2f", "V", 33),
            timed("when Udc higher than", 29, ".2f", "V", 32),
            timed("when charging for", None, "", "", 34),
            timed("when fan off for", None, "", "", 35),
            timed("when bulk charge finished for", None, "", "", 36, tunit="minutes", minutes=True),
            timed("when no VS ON condition for", None, "", "", 37, tunit="minutes", minutes=True),
            timed("when no AC input for", None, "", "", 38),
            ("group", "when the following LED alarms are IN-active"),
            ("timed", alarm("Temperature", 21), None, "", _f(m.seconds(39), "d"), "seconds"),
            ("timed", alarm("Low-battery", 22), None, "", _f(m.seconds(40), "d"), "seconds"),
            ("timed", alarm("Overload", 23), None, "", _f(m.seconds(41), "d"), "seconds"),
            ("timed", alarm("Udc ripple", 24), None, "", _f(m.seconds(42), "d"), "seconds"),
        ]),
        ("VS: options", [
            ("value", "Do not switch off within", _f(m.val(43), ".0f"), "minutes from switch on", True),
            ("text", "Important Note: VS conditions on page B with a delay of 0 ignore this setting!"),
        ]),
    ]


def find(pages, tab, label):
    """For tests: returns the row (tab, title) -> row tuple."""
    for name, rows in pages:
        if name != tab:
            continue
        for r in rows:
            if len(r) > 1 and r[1] == label:
                return r
    raise KeyError((tab, label))


# ---------------------------------------------------------------- user interface
def _entry(parent, text, enabled=True, width=8):
    e = tk.Entry(parent, width=width, justify="left", relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=BORDER,
                 readonlybackground="white" if enabled else "#f1f5f9",
                 fg=FG if enabled else DISABLED_FG)
    e.insert(0, text)
    e.config(state="readonly")
    return e


class _UI:
    """Small helper functions for settings-view style components.
    editable=False: everything read-only (as before). editable=True: components bound via EDIT_* are
    editable; the bindings are stored in self.bindings (collect() reads them)."""

    def __init__(self, pages, editable=False):
        self.p = pages
        self.editable = editable
        self.bindings = []   # (kind, target, variable, original)

    def row(self, tab, label):
        return find(self.p, tab, label)

    # -- bound (editable) components ---------------------------------
    def ventry(self, parent, key, text, enabled=True, width=7):
        """Value field; editable if key is in EDIT_VALUES and the view is editable."""
        sid = EDIT_VALUES.get(key)
        spec = EDIT_DERIVED.get(key)
        if not (self.editable and (sid is not None or spec is not None) and text not in ("?", None)):
            return _entry(parent, text, enabled, width)
        var = tk.StringVar(value=text)
        # an editable field always uses dark text: a grey number would look locked (the title tells whether the condition is in use)
        e = tk.Entry(parent, width=width, textvariable=var, relief="flat", bd=0, bg="white", fg=FG,
                     highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)

        def mark(*_a):
            ch = var.get().strip() != text
            e.config(bg=CHANGED_BG if ch else "white", highlightbackground=CHANGE if ch else BORDER,
                     highlightcolor=CHANGE if ch else ACCENT)
        var.trace_add("write", mark)
        e.var = var
        if sid is not None:
            self.bindings.append(("value", sid, var, text))
        else:
            self.bindings.append(("derived", spec, var, text))
        return e

    def vflagcombo(self, parent, values, current, bit, width=22):
        """Choice list bound to a flag bit: '... pre-alarm' = bit 1, '... alarm' = bit 0."""
        if not self.editable:
            return self.combo(parent, values, current, width)
        var = tk.StringVar(value=current)
        frame, cb = combo_box(parent, values, var, width)
        var.trace_add("write", lambda *_a: mark_combo(frame, var.get() != current))
        self.bindings.append(("flag_combo", bit, var, current))
        return frame

    def vcheck(self, parent, key, text, checked, enabled=True):
        spec = EDIT_FLAGS.get(key)
        if not (self.editable and spec is not None and checked is not None):
            return self.check(parent, text, checked, enabled)
        var = tk.IntVar(value=1 if checked else 0)
        base = _bg(parent)
        w = tk.Checkbutton(parent, text=text, variable=var, bg=base, activebackground=base, anchor="w",
                           justify="left", fg=FG if enabled else DISABLED_FG, highlightthickness=1,
                           highlightbackground=base, bd=0)

        def mark(*_a):
            ch = bool(var.get()) != bool(checked)
            col = CHANGED_BG if ch else base
            w.config(bg=col, activebackground=col, highlightbackground=CHANGE if ch else base)
        var.trace_add("write", mark)
        w.var = var
        bit, inv = spec
        self.bindings.append(("flag", (bit, inv), var, bool(checked)))
        return w

    def vradios(self, parent, options, selected, bind, enabled=True):
        """bind = ("flag", bit) -> choice = bit, or ("raw", sid) -> choice = raw."""
        if not self.editable:
            return self.radios(parent, options, selected, enabled)
        var = tk.IntVar(value=selected)
        ws = []
        base = _bg(parent)
        for i, opt in enumerate(options):
            w = tk.Radiobutton(parent, text=opt, variable=var, value=i, bg=base, activebackground=base, anchor="w",
                               fg=FG if enabled else DISABLED_FG, highlightthickness=1,
                               highlightbackground=base, bd=0)
            w.var = var
            ws.append(w)

        def mark(*_a):
            ch = var.get() != selected
            col = CHANGED_BG if ch else base
            for w in ws:
                w.config(bg=col, activebackground=col, highlightbackground=CHANGE if ch else base)
        var.trace_add("write", mark)
        self.bindings.append((bind[0] + "_radio", bind[1], var, selected))
        return ws

    def vcombo(self, parent, values, current, sid, mapping, width=24, enabled=True):
        """Choice list bound to a raw value: mapping = {text: raw}."""
        if not (self.editable and current in mapping):
            return self.combo(parent, values, current, width, enabled)
        var = tk.StringVar(value=current)
        frame, cb = combo_box(parent, values, var, width)
        var.trace_add("write", lambda *_a: mark_combo(frame, var.get() != current))
        self.bindings.append(("combo", (sid, mapping), var, current))
        return frame

    # -- components -------------------------------------------------------
    @staticmethod
    def frame(parent, text=None):
        """text=None: plain frame (on the parent background). text='' or a title: card
        (white, thin border, bold title inside the card). All cards are created here."""
        if text is None:
            return tk.Frame(parent, bg=_bg(parent))
        return Card(parent, text)

    @staticmethod
    def label(parent, text, enabled=True, **kw):
        return tk.Label(parent, text=text, bg=_bg(parent), fg=FG if enabled else DISABLED_FG, **kw)

    @staticmethod
    def entry(parent, text, enabled=True, width=7):
        return _entry(parent, text, enabled, width)

    @staticmethod
    def check(parent, text, checked, enabled=True):
        var = tk.IntVar(value=1 if checked else 0)
        base = _bg(parent)
        w = tk.Checkbutton(parent, text=text if checked is not None else f"{text} (?)", variable=var, bg=base,
                           activebackground=base, state="disabled", anchor="w", justify="left", bd=0,
                           disabledforeground=FG if enabled else DISABLED_FG)
        w.var = var  # keep a reference, otherwise the garbage collector clears it
        return w

    @staticmethod
    def radios(parent, options, selected, enabled=True):
        var = tk.IntVar(value=selected)
        ws = []
        for i, opt in enumerate(options):
            base = _bg(parent)
            w = tk.Radiobutton(parent, text=opt, variable=var, value=i, bg=base, activebackground=base,
                               state="disabled", anchor="w", bd=0,
                               disabledforeground=FG if enabled else DISABLED_FG)
            w.var = var
            ws.append(w)
        return ws

    @staticmethod
    def combo(parent, values, current, width=24, enabled=True):
        var = tk.StringVar(value=current)
        cb = ttk.Combobox(parent, values=values, textvariable=var, width=width,
                          state="readonly" if enabled else "disabled")
        cb.bind("<<ComboboxSelected>>", lambda e: var.set(current))  # read-only: restore
        cb.var = var
        return cb


def _tab_general(nb, ui):
    T = "General"
    f = ui.frame(nb)
    radio = next(r for r in dict(ui.p)[T] if r[0] == "radio" and r[2][0] == "50Hz")
    sf = ui.frame(f, "System frequency")
    sf.grid(row=0, column=0, sticky="nw", padx=6, pady=6)
    for i, w in enumerate(ui.vradios(sf, radio[2], radio[3], ("flag", 2))):
        w.grid(row=0, column=i, padx=8)

    sl = ui.frame(f, "Shore limit")
    sl.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
    _, _, val, unit, _ = ui.row(T, "AC input current limit")
    ui.label(sl, "AC input current limit").grid(row=0, column=0, sticky="w")
    ui.ventry(sl, (T, "AC input current limit"), val).grid(row=0, column=1, sticky="w", padx=4)
    ui.label(sl, unit).grid(row=0, column=2, sticky="w")
    ui.vcheck(sl, (T, "Dynamic current limiter"), "Dynamic current limiter", ui.row(T, "Dynamic current limiter")[2]).grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(16, 4))

    ps = ui.frame(f, "Parallel systems")
    ps.grid(row=2, column=0, sticky="nw", padx=6, pady=6)
    ui.label(ps, "Number of slaves").grid(row=0, column=0, sticky="w")
    slaves = ui.row(T, "Number of slaves")[2]
    ui.vcombo(ps, [str(i) for i in range(5)], slaves, 13, {str(i): i for i in range(5)}, width=3).grid(
        row=0, column=1, padx=6)

    three = ui.row(T, "Enable 2-3 phase")[2]
    ph = ui.frame(f, "")
    ph.grid(row=2, column=1, sticky="nw", padx=6, pady=6)
    ui.vcheck(ph, (T, "Enable 2-3 phase"), "Enable 2-3 phase", three).grid(row=0, column=0, sticky="w")
    ui.vcheck(ph, (T, "Leader"), "Leader", ui.row(T, "Leader")[2], enabled=bool(three)).grid(
        row=1, column=0, sticky="w", padx=12)
    mode = next(r for r in dict(ui.p)[T] if r[0] == "radio" and r[2][0] == "3 phase")
    for i, w in enumerate(ui.vradios(ph, mode[2], mode[3], ("raw", 14), enabled=bool(three))):
        w.grid(row=2 + i, column=0, sticky="w", padx=12)
    return f


def _tab_grid(nb, ui):
    T = "Grid"
    f = ui.frame(nb)
    ts = ui.frame(f, "Transfer switch")
    ts.grid(row=0, column=0, sticky="nw", padx=6, pady=6)
    wide = "Accept wide input frequency range (45-65 Hz)"
    ui.vcheck(ts, (T, wide), wide, ui.row(T, wide)[2]).grid(row=0, column=0, columnspan=6, sticky="w")
    for r, c, label in ((1, 0, "AC low disconnect"), (1, 3, "AC high connect"),
                        (2, 0, "AC low connect"), (2, 3, "AC high disconnect")):
        _, _, val, unit, _ = ui.row(T, label)
        ui.label(ts, label).grid(row=r, column=c, sticky="w", padx=(0 if c == 0 else 16, 4), pady=2)
        ui.ventry(ts, (T, label), val, width=5).grid(row=r, column=c + 1, sticky="w")
        ui.label(ts, unit).grid(row=r, column=c + 2, sticky="w", padx=2)
    ui.vcheck(ts, (T, "UPS function"), "UPS function", ui.row(T, "UPS function")[2]).grid(
        row=3, column=0, columnspan=6, sticky="w")
    return f


def _tab_inverter(nb, ui):
    T = "Inverter"
    f = ui.frame(nb)
    left = ui.frame(f, "")
    left.grid(row=0, column=0, sticky="nw", padx=6, pady=6)

    def value_row(parent, r, label):
        _, _, val, unit, enabled = ui.row(T, label)
        ui.label(parent, label, enabled).grid(row=r, column=0, sticky="w", pady=2)
        ui.ventry(parent, (T, label), val, enabled).grid(row=r, column=1, sticky="w", padx=4)
        ui.label(parent, unit, enabled).grid(row=r, column=2, sticky="w")

    value_row(left, 0, "Inverter output voltage")
    ui.vcheck(left, (T, "Ground relay"), "Ground relay", ui.row(T, "Ground relay")[2]).grid(
        row=1, column=0, columnspan=3, sticky="w", padx=12)
    ui.label(left, "").grid(row=2, column=0)
    value_row(left, 3, "DC input low shut-down")
    value_row(left, 4, "DC input low restart")
    ui.label(left, "").grid(row=5, column=0, pady=12)
    ui.vcheck(left, (T, "enable AES"), "enable AES", ui.row(T, "enable AES")[2]).grid(
        row=6, column=0, columnspan=3, sticky="w")

    pa_on = ui.row(T, "PowerAssist")[2]
    pa = ui.frame(f, "")
    pa.grid(row=0, column=1, sticky="nw", padx=6, pady=6)
    ui.vcheck(pa, (T, "PowerAssist"), "PowerAssist", pa_on).grid(row=0, column=0, columnspan=2, sticky="w")
    _, _, val, _, _ = ui.row(T, "Assist current boost factor")
    ui.label(pa, "Assist current boost factor", bool(pa_on)).grid(row=1, column=0, sticky="w")
    ui.ventry(pa, (T, "Assist current boost factor"), val, bool(pa_on), width=5).grid(row=1, column=1, padx=4)
    return f


def _tab_charger(nb, ui):
    T = "Charger"
    f = ui.frame(nb)
    on = bool(ui.row(T, "Enable charger")[2])  # the other fields are dimmed when the charger is off
    checks = ui.frame(f, "")
    checks.grid(row=0, column=0, sticky="nw", padx=6, pady=6)
    for r, label in ((0, "Enable charger"), (2, "Weak AC input"), (3, "Stop after excessive bulk"),
                     (5, "Storage mode")):
        ui.vcheck(checks, (T, label), label, ui.row(T, label)[2], on or r == 0).grid(row=r, column=0, sticky="w")
    ui.label(checks, "").grid(row=1, column=0)
    ui.label(checks, "").grid(row=4, column=0)

    bt = ui.frame(f, "Battery type")
    bt.grid(row=0, column=1, sticky="new", padx=6, pady=6)
    current_type = ui.row(T, "Battery type")[2]
    if ui.editable:
        _battery_type_combo(bt, ui, current_type)
    else:
        ui.label(bt, current_type, on, wraplength=260, justify="left").pack(anchor="w")

    vals = ui.frame(f, "")
    vals.grid(row=1, column=0, columnspan=2, sticky="nw", padx=6, pady=6)
    curve = ui.row(T, "Charge curve")[2]
    ui.label(vals, "Charge curve", on).grid(row=0, column=0, sticky="w", pady=(0, 6))
    ui.vcombo(vals, list(CHARGE_CURVES.values()), curve, 10, {v: k for k, v in CHARGE_CURVES.items()},
              width=22, enabled=on).grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 6))
    abs_label = "Absorption time" if curve == "Fixed" else "Maximum absorption time"
    grid_pos = [("Absorption voltage", 1, 0), ("Float voltage", 2, 0), ("Charge current", 3, 0),
                ("Repeated absorption time", 1, 3), ("Repeated absorption interval", 2, 3), (abs_label, 3, 3)]
    for label, r, c in grid_pos:
        _, _, val, unit, _ = ui.row(T, label)
        ui.label(vals, label, on).grid(row=r, column=c, sticky="w", padx=(0 if c == 0 else 20, 4), pady=2)
        ui.ventry(vals, (T, label), val, on, width=6).grid(row=r, column=c + 1, sticky="w")
        ui.label(vals, unit, on).grid(row=r, column=c + 2, sticky="w", padx=2)
    return f


NO_DEFAULT = "No corresponding default"


def combo_box(parent, values, var, width):
    """ttk.Combobox inside a frame: the Windows theme does not show the background colour of a readonly combobox,
    so a change is shown with the colour of the frame (mark_combo). Returns (frame, combobox)."""
    base = _bg(parent)
    frame = tk.Frame(parent, bg=base, highlightthickness=2, highlightbackground=base, highlightcolor=base)
    frame.base = base
    cb = ttk.Combobox(frame, values=values, textvariable=var, width=width, state="readonly")
    cb.pack(fill="x")
    cb.var = var
    frame.var = var
    frame.combo = cb
    return frame, cb


def mark_combo(widget, changed):
    frame = widget if isinstance(widget, tk.Frame) else widget.master
    col = CHANGE if changed else getattr(frame, "base", BG)
    frame.config(highlightbackground=col, highlightcolor=col)


def preset_texts(preset):
    """Preset -> field texts in the same form as layout() (no needless yellow markers)."""
    curve, absorption, float_v, rep_t, rep_i, abs_t = preset
    return {
        ("combo", 10): CHARGE_CURVES[curve],
        ("value", 2): f"{absorption:.2f}",
        ("value", 3): f"{float_v:.2f}",
        ("derived", ("scaled", 7, 60)): f"{rep_t / 60:.2f}",
        ("derived", ("scaled", 8, 1440)): f"{rep_i / 1440:.2f}",
        ("derived", ("scaled", 9, 60)): f"{abs_t / 60:.0f}",
    }


def apply_preset(ui, name):
    """Sets the charge curve, voltages and times in the fields (battery type preset).
    Sending happens as usual through the write dialog (the preview shows the changes)."""
    preset = next(p for p, n in BATTERY_PRESETS if n == name)
    texts = preset_texts(preset)
    for kind, tgt, var, _orig in ui.bindings:
        key = (kind, tgt[0] if kind == "combo" else tgt)
        if key in texts:
            var.set(texts[key])
    return preset


def _battery_type_combo(parent, ui, current):
    names = [n for _p, n in BATTERY_PRESETS]
    values = names + ([current] if current not in names else [])
    var = tk.StringVar(value=current)
    frame, cb = combo_box(parent, values, var, 40)
    frame.pack(anchor="w", fill="x")

    def selected(_e=None):
        name = var.get()
        if name in names:
            apply_preset(ui, name)
        mark_combo(cb, name != current)
    cb.bind("<<ComboboxSelected>>", selected)
    ui.battery_combo = cb
    tk.Label(parent, text="selecting a type sets the curve, voltages and times",
             bg=_bg(parent), fg=MUTED, font=(FONT, 8)).pack(anchor="w")
    return cb


def _follow_delay(entry, labels):
    """Titles grey when the delay is -1 (condition off), black otherwise; updates while typing."""
    var = getattr(entry, "var", None)
    if var is None:
        return

    def update(*_a):
        active = var.get().strip() not in ("-1", "?", "")
        for lbl in labels:
            lbl.config(fg=FG if active else DISABLED_FG)
    var.trace_add("write", update)


def _vs_timed_rows(parent, ui, tab, labels, start_row=0):
    r = start_row
    for label in labels:
        _, _, thr, unit, t, tunit = ui.row(tab, label)
        active = t not in ("-1", "?")
        labels = [ui.label(parent, label, active)]
        labels[0].grid(row=r, column=0, sticky="w", pady=1)
        if thr is not None:
            ui.ventry(parent, (tab, label, "thr"), thr, active).grid(row=r, column=1, sticky="w", padx=4)
            labels.append(ui.label(parent, f"{unit}  for", active))
            labels[-1].grid(row=r, column=2, sticky="w")
        c = 3
        te = ui.ventry(parent, (tab, label, "t"), t, True, width=6)
        te.grid(row=r, column=c, sticky="w", padx=4)
        labels.append(ui.label(parent, tunit, active))
        labels[-1].grid(row=r, column=c + 1, sticky="w")
        _follow_delay(te, labels)
        r += 1
    return r


def _vs_alarm_box(parent, ui, tab, title, names):
    box = ui.frame(parent, title)
    rows = [r for r in dict(ui.p)[tab] if r[0] == "timed" and r[1].split(" ")[0] in names and r[2] is None
            and ("alarm" in r[1])]
    for i, (_, label, _, _, t, tunit) in enumerate(rows):
        base = label.replace(" pre-alarm", "").replace(" alarm", "")
        bit, _t_sid = VS_ALARMS[(tab, base)]
        ui.vflagcombo(box, [f"{base} alarm", f"{base} pre-alarm"], label, bit).grid(
            row=i, column=0, sticky="w", pady=1)
        ui.label(box, "for").grid(row=i, column=1, padx=4)
        te = ui.ventry(box, (tab, base, "t"), t, True, width=6)
        te.grid(row=i, column=2, sticky="w")
        unit_lbl = ui.label(box, tunit, t not in ("-1", "?"))
        unit_lbl.grid(row=i, column=3, sticky="w", padx=4)
        _follow_delay(te, [unit_lbl])
    return box


def _tab_virtual_switch(nb, ui, model):
    f = tk.Frame(nb, bg=BG)
    sub = ttk.Notebook(f)
    sub.pack(fill="both", expand=True, padx=4, pady=4)

    # Usage
    T = "VS: Usage"
    u = ui.frame(sub)
    box = ui.frame(u, "Specify virtual switch usage :")
    box.pack(anchor="nw", fill="x", padx=8, pady=8)
    usage = next(r for r in dict(ui.p)[T] if r[0] == "radio")
    for i, w in enumerate(ui.vradios(box, usage[2], usage[3], ("raw", 15))):
        w.grid(row=i + 1, column=0, sticky="w")
    used = (usage[3] or 0) != 0
    ui.vcheck(box, (T, "Invert virtual switch usage"), "Invert virtual switch usage",
              ui.row(T, "Invert virtual switch usage")[2], used).grid(row=0, column=1, sticky="ne", padx=(30, 0))
    sub.add(u, text="Usage")

    # the A/B/options pages are shown only when the virtual switch is in use
    if not used:
        return f

    T = "VS: A Set VS ON"
    a = ui.frame(sub)
    inner = ui.frame(a, "")
    inner.pack(anchor="nw", padx=10, pady=8)
    r = _vs_timed_rows(inner, ui, T, ["when load higher than", "when Udc lower than", "when Udc higher than"])
    ui.label(inner, "").grid(row=r, column=0)
    r = _vs_timed_rows(inner, ui, T, ["when not charging for", "when fan on for"], r + 1)
    for label in ("set VS on when bulk protection is activated (charger stopped after 10Hr bulk)",
                  "set VS on when general system failure occurs"):
        ui.vcheck(inner, (T, label), label, ui.row(T, label)[2]).grid(row=r, column=0, columnspan=5, sticky="w")
        r += 1
    _vs_alarm_box(inner, ui, T, "when the following LED alarms are active:",
                  ("Temperature", "Low-battery", "Overload", "Udc")).grid(
        row=r, column=0, columnspan=5, sticky="w", pady=(8, 0))
    sub.add(a, text="A:  Set VS ON")

    T = "VS: B Set VS OFF"
    b = ui.frame(sub)
    inner = ui.frame(b, "")
    inner.pack(anchor="nw", padx=10, pady=8)
    r = _vs_timed_rows(inner, ui, T, ["when load lower than", "when Udc lower than", "when Udc higher than"])
    ui.label(inner, "").grid(row=r, column=0)
    r = _vs_timed_rows(inner, ui, T, ["when charging for", "when fan off for"], r + 1)
    ui.label(inner, "").grid(row=r, column=0)
    r = _vs_timed_rows(inner, ui, T, ["when bulk charge finished for", "when no VS ON condition for",
                                      "when no AC input for"], r + 1)
    _vs_alarm_box(inner, ui, T, "when the following LED alarms are IN-active:",
                  ("Temperature", "Low-battery", "Overload", "Udc")).grid(
        row=r, column=0, columnspan=5, sticky="w", pady=(8, 0))
    sub.add(b, text="B:  Set VS OFF")

    T = "VS: options"
    o = ui.frame(sub)
    box = ui.frame(o, "")
    box.pack(anchor="nw", fill="x", padx=8, pady=8)
    ui.label(box, "(For virtual switch inversion see Usage page!)").grid(row=0, column=0, columnspan=3, sticky="w")
    _, _, val, unit, _ = ui.row(T, "Do not switch off within")
    ui.label(box, "Do not switch off within").grid(row=1, column=0, sticky="w")
    ui.ventry(box, (T, "Do not switch off within"), val, width=6).grid(row=1, column=1, padx=4)
    ui.label(box, unit).grid(row=1, column=2, sticky="w")
    ui.label(box, "Important Note: VS conditions on page B with a delay of 0 ignore this setting!").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
    sub.add(o, text="VS options")
    return f


TOP_TABS = ["General", "Grid", "Inverter", "Charger", "Virtual switch"]


def _to_raw(settings, sid, display):
    info = settings[sid]
    return int(round(display / d.setting_scale(info["sc"]) - d.s16(info.get("offset", 0))))


def _num(text):
    return float(text.strip().replace(",", "."))


def collect(ui, settings):
    """Reads the changes of the editable tabs.
    Returns (edits {id: text in display units}, raws {id: raw}, flags {0|1: wanted word}, errors).
    Only changed items are included; flags are computed from the current device word (merge_flags protects the other bits).
    Derived fields are converted directly to raw (rounded to the nearest step, the preview shows the result).
    Hysteresis is recomputed if the connect level OR its base value changed: the visible connect level stays."""
    edits, raws, errs = {}, {}, []
    m = Model(settings)
    words = {sid: settings[sid]["raw"] for sid in (0, 1) if settings.get(sid, {}).get("raw") is not None}

    def set_bit(bit, on):
        sid = 0 if bit < 16 else 1
        if sid in words:
            words[sid] = (words[sid] & ~(1 << bit % 16)) | (int(bool(on)) << bit % 16)

    for kind, tgt, var, orig in ui.bindings:
        v = var.get()
        if kind == "value":
            if v.strip() != orig:
                edits[tgt] = v
        elif kind == "flag":
            bit, inv = tgt
            if bool(v) != orig:
                set_bit(bit, (not v) if inv else v)
        elif kind == "flag_radio":
            if v != orig:
                set_bit(tgt, v)
        elif kind == "raw_radio":
            if v != orig:
                raws[tgt] = v
        elif kind == "combo":
            sid, mapping = tgt
            if v != orig:
                raws[sid] = mapping[v]
        elif kind == "flag_combo":
            if v != orig:
                set_bit(tgt, "pre-alarm" in v)

    def base_display(sid):
        if sid in edits:
            return _num(edits[sid])
        return m.val(sid)

    for kind, spec, var, orig in ui.bindings:
        if kind != "derived":
            continue
        text = var.get()
        changed = text.strip() != orig
        sk = spec[0]
        if not changed and not (sk == "hyst" and spec[1] in edits):
            continue
        try:
            val = _num(text)
            if sk == "hyst":
                _, base, hyst, sign = spec
                raw, sid = _to_raw(settings, hyst, (val - base_display(base)) * sign), hyst
            elif sk == "scaled":
                sid = spec[1]
                raw = _to_raw(settings, sid, val * spec[2])
            elif sk == "watts":
                sid = spec[1]
                raw = _to_raw(settings, sid, val / NOMINAL_V)
            elif sk in ("vs_s", "vs_min"):
                sid = spec[1]
                raw = int(round(val)) + 1
            else:  # direct
                sid = spec[1]
                raw = _to_raw(settings, sid, val)
        except (ValueError, KeyError, TypeError) as e:
            errs.append(f"'{text}': {e if not isinstance(e, ValueError) else 'not a number'}")
            continue
        if raw != settings.get(sid, {}).get("raw"):
            raws[sid] = raw
    flags = {sid: w for sid, w in words.items() if w != settings[sid]["raw"]}
    return edits, raws, flags, errs


def build_tabs(notebook, model, editable=False):
    """Builds the settings tabs. Returns the layout() data (tests use the same values).
    editable=True: bound fields are editable; _UI is stored in notebook.settings_ui (collect())."""
    apply_style(notebook)
    pages = layout(model)
    ui = _UI(pages, editable)
    notebook.settings_ui = ui
    builders = [_tab_general, _tab_grid, _tab_inverter, _tab_charger,
                lambda nb, ui: _tab_virtual_switch(nb, ui, model)]
    for name, build in zip(TOP_TABS, builders):
        page = build(notebook, ui)
        page.configure(width=600, height=400)
        notebook.add(page, text=name)
    return pages
