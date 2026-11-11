"""Offline tests for the settings write dialog (no serial port, hidden Tk)."""
import tkinter as tk

import mk2_link as m
import mk2_panel as p
from test_mk2_link import _fake


def _edits(f, **override):
    s = f.settings
    e = {sid: d_fmt(f, sid) for sid in p.WRITE_IDS if sid not in (0, 1) and s[sid].get("sc")}
    e.update({int(k[1:]): v for k, v in override.items()})
    return e


def d_fmt(f, sid):
    info = f.settings[sid]
    return p.d.fmt(p.dlg_display(f, info, info["raw"]))


def test_vs_delay_in_seconds():
    f = _fake()
    info = f.settings[19]
    assert p.is_vs_seconds(info) and not p.is_vs_seconds(f.settings[36]) and not p.is_vs_seconds(f.settings[5])
    assert p.dlg_display(f, info, 0) == -1 and p.dlg_unit(19, info) == "s"
    assert p.dlg_display(f, info, info["max"]) == info["max"] - 1
    ch, errs = p.compute_changes(f, f.settings, _edits(f, i19="30", i20="-1"), {})
    assert errs == [] and ch == {19: 31}, (ch, errs)
    assert f.validate_write(ch) == []


def test_ids_match_link():
    assert p.WRITE_IDS == m.WRITE_ORDER
    assert p.FLAG_EDIT_MASK[0] == m.FLAG_WRITE_MASK[0] & ~m.FLAG0_UPS_INV
    assert p.FLAG_EDIT_MASK[1] == m.FLAG_WRITE_MASK[1]


def test_unchanged_gives_nothing():
    f = _fake()
    ch, errs = p.compute_changes(f, f.settings, _edits(f), {0: f.settings[0]["raw"], 1: f.settings[1]["raw"]})
    assert errs == [] and ch == {}, (ch, errs)


def test_modified_only():
    f = _fake()
    ch, errs = p.compute_changes(f, f.settings, _edits(f, i5="117", i6="25,0"), {})
    assert errs == [] and ch == {5: 117, 6: 125}, (ch, errs)


def test_step_and_text_errors():
    f = _fake()
    _ch, errs = p.compute_changes(f, f.settings, _edits(f, i6="2.1"), {})
    assert errs and "step" in errs[0]
    _ch, errs = p.compute_changes(f, f.settings, _edits(f, i5="abc"), {})
    assert errs and "not a number" in errs[0]


def test_send_all():
    f = _fake()
    ch, errs = p.compute_changes(f, f.settings, _edits(f), {0: f.settings[0]["raw"], 1: f.settings[1]["raw"]},
                                 send_all=True)
    assert errs == [] and sorted(ch) == list(range(49)), (sorted(ch), errs)
    assert f.validate_write(ch) == []


def test_dialog_builds():
    f = _fake()
    for a, v in dict(values={}, ram_info={}, led=None, switch=None, mk2_version=None, mk2_mode=None,
                     error=None, cycle_s=None, master=None, knob=None).items():
        setattr(f, a, v)
    f.set_switch = lambda s: None
    root = tk.Tk()
    root.withdraw()
    panel = p.Panel(root, f, "fake.log")
    assert str(panel.btn_write["state"]) == "disabled"
    panel._open_write_dialog()
    root.update()
    assert panel._write_win.winfo_exists()
    root.destroy()


def _panel_with_tabs():
    f = _fake()
    for a, v in dict(values={}, ram_info={}, led=None, switch=None, mk2_version=None, mk2_mode=None,
                     error=None, cycle_s=None, master=None, knob=None).items():
        setattr(f, a, v)
    f.set_switch = lambda s: None
    root = tk.Tk()
    root.withdraw()
    panel = p.Panel(root, f, "fake.log")
    panel._show_settings(f.settings)
    root.update()
    return root, panel, f


def _binding(panel, kind, target):
    for k, t, var, orig in panel.settings_notebook.settings_ui.bindings:
        if k == kind and t == target:
            return var
    raise KeyError((kind, target))


def test_tabs_unchanged_gives_nothing():
    root, panel, f = _panel_with_tabs()
    ch, errs = panel.tab_changes()
    root.destroy()
    assert ch == {} and errs == [], (ch, errs)


def test_tabs_values_flags_and_choices():
    root, panel, f = _panel_with_tabs()
    _binding(panel, "value", 5).set("117")            # Inverter output voltage
    _binding(panel, "value", 4).set("40")             # Charge current
    _binding(panel, "flag", (11, False)).set(1)       # Storage mode
    _binding(panel, "flag", (8, True)).set(1)         # enable AES (inverted: box checked -> bit 0)
    import mk2_settings_view as vv
    _binding(panel, "combo", (10, {v: k for k, v in vv.CHARGE_CURVES.items()})).set("Adaptive")
    ch, errs = panel.tab_changes()
    assert errs == [], errs
    raw0 = f.settings[0]["raw"]
    want0 = (raw0 | 1 << 11) & ~(1 << 8)
    assert ch == {5: 117, 4: 40, 0: want0, 10: 2}, ch
    assert f.validate_write(ch) == []
    lines = panel.preview_lines(ch)
    assert any("Storage mode ☑" in x and "enable AES ☑" in x for x in lines), lines
    root.destroy()


def test_tabs_bad_value_rejected():
    root, panel, f = _panel_with_tabs()
    _binding(panel, "value", 5).set("230")
    ch, errs = panel.tab_changes()
    assert not errs and f.validate_write(ch), (ch, errs)  # device max 128 + hard limit
    root.destroy()


def test_derived_fields_not_direct():
    import mk2_settings_view as vv
    for key in (("Grid", "AC low connect"), ("Inverter", "DC input low restart"),
                ("Charger", "Repeated absorption time")):
        assert key not in vv.EDIT_VALUES and key in vv.EDIT_DERIVED


def _derived(panel, spec):
    return _binding(panel, "derived", spec)


def test_hysteresis_fields():
    root, panel, f = _panel_with_tabs()
    s = f.settings
    # alkuarvot: 44=94, 45=7 (connect 101), 46=143, 47=5 (connect 138), 11=18.6, 12=3.2 (restart 21.8)
    _derived(panel, ("hyst", 44, 45, +1)).set("104")      # AC low connect 101 -> 104 => hyst 10
    _derived(panel, ("hyst", 46, 47, -1)).set("135")      # AC high connect 138 -> 135 => hyst 8
    _derived(panel, ("hyst", 11, 12, +1)).set("22.4")     # DC restart 21.8 -> 22.4 => hyst 3.8
    ch, errs = panel.tab_changes()
    assert errs == [], errs
    assert ch == {45: 10, 47: 8, 12: _to(s, 12, 3.8)}, ch
    assert f.validate_write(ch) == []
    root.destroy()


def test_hysteresis_follows_base_change():
    # change of the base value: the visible connect level stays -> the hysteresis is recomputed
    root, panel, f = _panel_with_tabs()
    _binding(panel, "value", 44).set("96")                 # AC low disconnect 94 -> 96, connect 101 stays
    ch, errs = panel.tab_changes()
    assert errs == [] and ch == {44: 96, 45: 5}, (ch, errs)
    root.destroy()


def test_hours_and_days():
    root, panel, f = _panel_with_tabs()
    s = f.settings
    _derived(panel, ("scaled", 7, 60)).set("2")            # 2 Hr = 120 min
    _derived(panel, ("scaled", 8, 1440)).set("14")         # 14 Days
    _derived(panel, ("scaled", 9, 60)).set("8")            # 8 Hr
    ch, errs = panel.tab_changes()
    assert errs == [], errs
    assert ch == {7: _to(s, 7, 120), 8: _to(s, 8, 14 * 1440), 9: _to(s, 9, 480)}, ch
    assert f.validate_write(ch) == []
    root.destroy()


def _to(s, sid, display):
    info = s[sid]
    return int(round(display / p.d.setting_scale(info["sc"]) - p.d.s16(info.get("offset", 0))))


def test_battery_presets():
    import mk2_settings_view as vv
    root, panel, f = _panel_with_tabs()
    ui = panel.settings_notebook.settings_ui
    s = f.settings
    assert ui.battery_combo.var.get() == "Lithium Iron Phosphate (LiFePo4) batteries"
    # same preset again -> no changes
    vv.apply_preset(ui, "Lithium Iron Phosphate (LiFePo4) batteries")
    ch, errs = panel.tab_changes()
    assert ch == {} and errs == [], (ch, errs)
    # Gel Victron: Adaptive+BatterySafe, 28.80 / 27.60 V, max abs 8 h
    vv.apply_preset(ui, "Gel/AGM Victron Energy, Gel Exide A200")
    ch, errs = panel.tab_changes()
    assert errs == [], errs
    assert ch == {10: 3, 2: _to(s, 2, 28.8), 3: _to(s, 3, 27.6), 9: _to(s, 9, 480)}, ch
    assert f.validate_write(ch) == []
    root.destroy()


def test_reset_prompt_logic():
    f = _fake()
    s = f.settings
    assert p.untested_for_reset(f, s, {5: 117, 4: 40, 11: 2200}) == []
    assert p.untested_for_reset(f, s, {6: 100}) == ["6 IMainsLimit(AC1)"]
    raw0 = s[0]["raw"]
    # Storage mode (bit 11) -> the flag is listed by name; UPS (3) does not list the pair bit 7 separately
    assert p.untested_for_reset(f, s, {0: raw0 | 1 << 11}) == ["flag 11 Storage mode"]
    assert p.untested_for_reset(f, s, {0: raw0 | 1 << 3}) == ["flag 3 UPS function"]
    p.LIVE_VERIFIED_FLAGS.add(11)
    try:
        assert p.untested_for_reset(f, s, {0: raw0 | 1 << 11}) == []
    finally:
        p.LIVE_VERIFIED_FLAGS.discard(11)


def test_unverified_firmware_blocks_the_write_chain():
    root, panel, f = _panel_with_tabs()
    f.fw = {0x82: 1, 0x83: 2}                       # some other device
    _binding(panel, "value", 5).set("117")
    ch, errs = panel.tab_changes()
    assert errs == [] and ch == {5: 117}, (ch, errs)
    assert f.validate_write(ch) and "read-only" in f.validate_write(ch)[0]
    root.destroy()


def test_write_aborted_message_offers_no_reset():
    from test_mk2_link import FailingLink
    f = FailingLink(fail_id=2)
    try:
        f.write_settings({5: 117, 2: f.dev[2], 12: f.dev[12]})
    except m.WriteAborted as e:
        text = p.write_aborted_text(e)
    else:
        raise AssertionError("the write should have stopped")
    assert "Written and verified (1): 5" in text and "FAILED: 2" in text and "Not written (1): 12" in text
    assert "NOT reset" in text and f.settings_dirty


class _Ev:
    x = y = 50
    x_root = y_root = 100
    delta = 120


def _wait_emulator(conn):
    import time
    end = time.time() + 20
    while time.time() < end and not (m.device_fw(conn.link.fw) and len(conn.link.values) >= 3):
        time.sleep(0.1)


def test_panel_control_button_hint_and_memory():
    """Direct mode with the emulator: monitor only after the first Connect; the DMC state button; a tip on DMC
    clicks while monitor only; the choice and the current limit are remembered for the next Connect."""
    import time
    import mk2_config
    saved = p.load_panel_state, p.save_panel_state
    state = {}
    p.load_panel_state = lambda *a, **k: dict(state)
    p.save_panel_state = lambda upd, *a, **k: state.update(upd)
    conn = p.LinkManager(mk2_config.from_dict({}), None)
    root = tk.Tk()
    root.withdraw()
    try:
        panel = p.Panel(root, None, "direct", conn=conn)
        root.update()
        assert str(panel.control_btn["state"]) == "disabled" and "not connected" in panel.control_btn["text"]
        conn.connect(m.EMULATOR_PORT)
        _wait_emulator(conn)
        panel._sync_conn_controls()
        assert str(panel.control_btn["state"]) == "normal" and "click to enable control" in panel.control_btn["text"]
        panel._update_conn(time.time())
        assert "monitor only" in panel.conn_label["text"], panel.conn_label["text"]
        panel._draw_knob(time.time())
        assert panel.canvas.itemcget(panel.knob_disp, "text") == ""        # monitor only: unknown, nothing sent
        # clicking a DMC switch button in monitor only: a tip, nothing is sent
        panel._dmc_switch_clicked(_Ev(), 1)
        assert panel._hint_win is not None and "Enable control first" in panel.hint_text
        assert conn.link.switch is None and conn.link.knob is None
        # enable from the button: asked once (confirmation answered yes), remembered
        import tkinter.messagebox as mb
        asked = []
        orig_ask = mb.askyesno
        mb.askyesno = lambda *a, **k: asked.append(a) or True
        try:
            panel._control_clicked()
        finally:
            mb.askyesno = orig_ask
        emu = state["ports"][m.EMULATOR_PORT]
        assert len(asked) == 1 and conn.link.control and emu.get("control") is True
        assert emu.get("confirmed") is True and "control" not in state   # remembered for this port only
        panel._sync_conn_controls()
        assert "CONTROL ON" in panel.control_btn["text"]
        panel._draw_knob(time.time())
        assert panel.canvas.itemcget(panel.knob_disp, "text").strip() == "2.0"   # first time: conservative minimum
        panel._update_conn(time.time())
        assert "control ON" in panel.conn_label["text"], panel.conn_label["text"]
        conn.link.set_knob(66)                       # the user raises the limit (the panel saves it per port)
        conn.save_knob(66)
        assert state["ports"][m.EMULATOR_PORT]["knob"] == 66 and "knob" not in state
        conn.link.settings_dirty = True
        panel._update_conn(time.time())
        assert "SETTINGS PARTLY WRITTEN" in panel.conn_label["text"]
        # next start: control on without asking, the limit where it was left
        conn.disconnect()
        conn.connect(m.EMULATOR_PORT)
        assert conn.link.control is True and conn.link.desired_knob == 66
        panel._sync_conn_controls()
        assert "CONTROL ON" in panel.control_btn["text"]
        # back to monitor only from the button: remembered too, no question
        mb.askyesno = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not ask"))
        try:
            panel._control_clicked()
        finally:
            mb.askyesno = orig_ask
        assert not conn.link.control and state["ports"][m.EMULATOR_PORT]["control"] is False
        assert "monitor only" in panel._progress
        conn.disconnect()
        conn.connect(m.EMULATOR_PORT)
        assert conn.link.control is False and conn.link.desired_knob is None
        # enabling again: no question any more, continues from the saved limit
        _wait_emulator(conn)
        mb.askyesno = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not ask"))
        try:
            panel._control_clicked()
        finally:
            mb.askyesno = orig_ask
        assert conn.link.control and conn.link.desired_knob == 66
        # another port does not inherit the emulator's permission or limit
        assert p.port_state(state, "COM99") == {}
    finally:
        conn.disconnect()
        root.destroy()
        p.load_panel_state, p.save_panel_state = saved


def test_vs_pages():
    f = _fake()
    f.settings[15]["raw"] = 1  # VS in use -> the A/B/options pages become visible
    for a, v in dict(values={}, ram_info={}, led=None, switch=None, mk2_version=None, mk2_mode=None,
                     error=None, cycle_s=None, master=None, knob=None).items():
        setattr(f, a, v)
    f.set_switch = lambda s: None
    root = tk.Tk()
    root.withdraw()
    panel = p.Panel(root, f, "fake.log")
    panel._show_settings(f.settings)
    root.update()
    s = f.settings
    _derived(panel, ("watts", 16)).set("1800")              # load higher than 1800 W = 15 A
    _derived(panel, ("vs_s", 19)).set("30")                 # ... for 30 s
    _derived(panel, ("vs_min", 36)).set("10")               # bulk finished for 10 min
    _derived(panel, ("vs_s", 24)).set("5")                  # Temperature alarm for 5 s
    _derived(panel, ("direct", 43)).set("3")                # do not switch off within 3 min
    _binding(panel, "flag_combo", 17).set("Temperature pre-alarm")
    ch, errs = panel.tab_changes()
    assert errs == [], errs
    want1 = s[1]["raw"] | 1 << 1                            # bit 17 = Flags1 bit 1
    assert ch == {16: _to(s, 16, 15.0), 19: 31, 36: 11, 24: 6, 43: 3, 1: want1}, ch
    assert f.validate_write(ch) == []
    root.destroy()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("OK")
