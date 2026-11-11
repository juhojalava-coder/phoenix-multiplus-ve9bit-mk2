"""Settings view values vs. the reference screenshots of the official configuration software (29.10.2026)."""
import mk2_decode as d
import mk2_settings_view as vv

ORIG = "data/reference_settings_read.log"      # original settings (Get settings)
SESSION = "data/reference_settings_write.log"   # write experiments


def settings_from(st, values):
    out = {}
    for sid, info in st.setting_info.items():
        i = dict(info)
        if sid in values:
            i["raw"] = values[sid]
        out[sid] = i
    return out


def state_at(t_end):
    """Settings at time t_end: original values + writes <= t_end."""
    orig = d.decode(ORIG)
    sess = d.decode(SESSION)
    vals = dict(orig.setting_val)
    for t, sid, _old, new in sess.setting_writes:
        if t <= t_end:
            vals[sid] = new
    return vv.Model(settings_from(orig, vals))


def check(pages, tab, label, expected):
    row = vv.find(pages, tab, label)
    kind = row[0]
    got = {"value": lambda r: r[2], "check": lambda r: r[2], "combo": lambda r: r[2],
           "timed": lambda r: (r[2], r[4]) if r[2] is not None else r[4]}[kind](row)
    assert got == expected, f"{tab} / {label}: got {got!r}, expected {expected!r}"


def test_original_settings():
    orig = d.decode(ORIG)
    p = vv.layout(vv.Model(settings_from(orig, orig.setting_val)))
    # General
    assert vv.find(p, "General", "")[3] == 1  # first radio = 60Hz
    check(p, "General", "AC input current limit", "30.0")
    check(p, "General", "Dynamic current limiter", False)
    check(p, "General", "Number of slaves", "0")
    check(p, "General", "Enable 2-3 phase", False)
    # Grid
    check(p, "Grid", "Accept wide input frequency range (45-65 Hz)", True)
    check(p, "Grid", "AC low disconnect", "94")
    check(p, "Grid", "AC low connect", "101")
    check(p, "Grid", "AC high connect", "138")
    check(p, "Grid", "AC high disconnect", "143")
    check(p, "Grid", "UPS function", True)
    # Inverter
    check(p, "Inverter", "Inverter output voltage", "120")
    check(p, "Inverter", "Ground relay", True)
    check(p, "Inverter", "DC input low shut-down", "18.60")
    check(p, "Inverter", "DC input low restart", "21.80")
    check(p, "Inverter", "enable AES", False)
    check(p, "Inverter", "PowerAssist", True)
    check(p, "Inverter", "Assist current boost factor", "2.0")
    # Charger
    check(p, "Charger", "Enable charger", True)
    check(p, "Charger", "Weak AC input", False)
    check(p, "Charger", "Stop after excessive bulk", True)
    check(p, "Charger", "Storage mode", False)
    check(p, "Charger", "Battery type", "Lithium Iron Phosphate (LiFePo4) batteries")
    check(p, "Charger", "Charge curve", "Fixed")
    check(p, "Charger", "Absorption voltage", "28.40")
    check(p, "Charger", "Float voltage", "27.00")
    check(p, "Charger", "Charge current", "52")
    check(p, "Charger", "Repeated absorption time", "1.00")
    check(p, "Charger", "Repeated absorption interval", "7.00")
    check(p, "Charger", "Absorption time", "1")
    # Virtual switch (original: not in use, all times -1)
    assert vv.find(p, "VS: Usage", "")[3] == 0
    check(p, "VS: Usage", "Invert virtual switch usage", False)
    check(p, "VS: A Set VS ON", "when load higher than", ("2550", "-1"))
    check(p, "VS: A Set VS ON", "when Udc lower than", ("23.50", "-1"))
    check(p, "VS: A Set VS ON", "when Udc higher than", ("32.00", "-1"))
    check(p, "VS: B Set VS OFF", "when load lower than", ("637", "-1"))
    check(p, "VS: options", "Do not switch off within", "0")


def test_virtual_switch_mid_session():
    # State at the time of the screenshot: the second batch of page B sent (t~2460), B Temp pre-alarm not yet (t 2498)
    p = vv.layout(state_at(2470))
    assert vv.find(p, "VS: Usage", "")[3] == 2  # ignore AC input
    check(p, "VS: Usage", "Invert virtual switch usage", True)
    # page A
    check(p, "VS: A Set VS ON", "when load higher than", ("2550", "-1"))
    check(p, "VS: A Set VS ON", "when Udc lower than", ("23.60", "2"))
    check(p, "VS: A Set VS ON", "when Udc higher than", ("32.10", "3"))
    check(p, "VS: A Set VS ON", "when not charging for", "10")
    check(p, "VS: A Set VS ON", "when fan on for", "11")
    check(p, "VS: A Set VS ON", "set VS on when bulk protection is activated (charger stopped after 10Hr bulk)", True)
    check(p, "VS: A Set VS ON", "set VS on when general system failure occurs", True)
    check(p, "VS: A Set VS ON", "Temperature pre-alarm", "4")
    check(p, "VS: A Set VS ON", "Udc ripple pre-alarm", "7")
    # page B (screenshot: 635 W / 2, 23.40 / 3, 31.90 / 4, 4, 5, 6 min, 7 min, 8, alarms 10-13)
    check(p, "VS: B Set VS OFF", "when load lower than", ("635", "2"))
    check(p, "VS: B Set VS OFF", "when Udc lower than", ("23.40", "3"))
    check(p, "VS: B Set VS OFF", "when Udc higher than", ("31.90", "4"))
    check(p, "VS: B Set VS OFF", "when charging for", "4")
    check(p, "VS: B Set VS OFF", "when fan off for", "5")
    check(p, "VS: B Set VS OFF", "when bulk charge finished for", "6")
    check(p, "VS: B Set VS OFF", "when no VS ON condition for", "7")
    check(p, "VS: B Set VS OFF", "when no AC input for", "8")
    check(p, "VS: B Set VS OFF", "Temperature alarm", "10")
    check(p, "VS: B Set VS OFF", "Udc ripple alarm", "13")


def test_battery_type_presets():
    # Gel MK screenshot moment: preset sent at t~1602
    p = vv.layout(state_at(1610))
    check(p, "Charger", "Battery type", "Gel MK Battery")
    check(p, "Charger", "Charge curve", "Adaptive + BatterySafe")
    check(p, "Charger", "Maximum absorption time", "8")
    check(p, "Charger", "Storage mode", True)
    # Redflow ZCell t~1778
    p = vv.layout(state_at(1790))
    check(p, "Charger", "Battery type", "Redflow ZCell")
    check(p, "Charger", "Float voltage", "28.50")


def test_ui_builds():
    """The tabs are built; the VS subpages only when the VS is in use."""
    import tkinter as tk
    from tkinter import ttk

    def sub_tabs(model):
        root = tk.Tk()
        root.withdraw()
        nb = ttk.Notebook(root)
        vv.build_tabs(nb, model)
        root.update_idletasks()
        top = [nb.tab(t, "text") for t in nb.tabs()]
        vs_page = nb.nametowidget(nb.tabs()[top.index("Virtual switch")])
        sub = next(c for c in vs_page.winfo_children() if isinstance(c, ttk.Notebook))
        subs = [sub.tab(t, "text") for t in sub.tabs()]
        root.destroy()
        return top, subs

    orig = d.decode(ORIG)
    top, subs = sub_tabs(vv.Model(settings_from(orig, orig.setting_val)))
    assert top == vv.TOP_TABS, top
    assert subs == ["Usage"], subs
    _, subs = sub_tabs(state_at(2470))
    assert subs == ["Usage", "A:  Set VS ON", "B:  Set VS OFF", "VS options"], subs


if __name__ == "__main__":
    test_original_settings()
    test_virtual_switch_mid_session()
    test_battery_type_presets()
    test_ui_builds()
    print("OK: settings view matches the reference screenshots")
