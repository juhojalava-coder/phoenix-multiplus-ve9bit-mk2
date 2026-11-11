"""Smoke test: the settings window is built from a capture without errors (hidden Tk)."""
import time
import tkinter as tk
from tkinter import ttk

import mk2_panel as p

LOG = "data/reference_settings_read.log"

root = tk.Tk()
root.withdraw()
tail = p.LogTail(LOG)
tail.poll()
panel = p.Panel(root, tail, LOG)
panel._read_settings(root)
root.update()

def walk(w):
    for c in w.winfo_children():
        yield c
        yield from walk(c)


# the tabs and the DMC are in the same main window (the DMC is below the tabs)
assert isinstance(panel.dmc_win, tk.Frame) and panel.dmc_win.winfo_toplevel() is root
# the DMC is always shown: there is no hide button
assert not hasattr(panel, "_toggle_dmc")
assert not any(isinstance(w, ttk.Button) and w.cget("text") == "Digital Multi Control" for w in walk(root))
tree = next(c for c in walk(panel.settings_notebook) if isinstance(c, ttk.Treeview))
tabs = [panel.settings_notebook.tab(t, "text") for t in panel.settings_notebook.tabs()]
assert tabs == ["General", "Grid", "Inverter", "Charger", "Virtual switch", "All settings"], tabs
print("Tabs:", ", ".join(tabs))
rows = {tree.set(i, "id"): (tree.set(i, "name"), tree.set(i, "value"), tree.set(i, "unit")) for i in tree.get_children()}
print(panel._progress)
for sid in ("0", "2", "3", "4", "6", "9", "11"):
    print(sid, rows[sid])
flags0 = [tree.set(c, "name").strip() + "=" + tree.set(c, "value") for c in tree.get_children(
    next(i for i in tree.get_children() if tree.set(i, "id") == "0"))]
print("Flags0:", ", ".join(flags0))
assert rows["2"][1] == "28.4" and rows["6"][1] == "30", rows
assert rows["0"][1] == "0x01A4", rows["0"]

# tab values vs. the reference screenshots of the official configuration software 29.10.2026
ui_values = {tree.set(i, "id"): tree.set(i, "tabvalue") for i in tree.get_children()}
expected = {"45": "101 V", "47": "138 V", "12": "21.80 V", "7": "1.00 Hr", "8": "7.00 Days",
            "9": "1 Hr", "10": "Fixed", "48": "2.0", "6": "30.0 A", "2": "28.40 V"}
for k, v in expected.items():
    assert ui_values[k] == v, (k, ui_values[k], v)
flag_rows = {}
for top in tree.get_children():
    for c in tree.get_children(top):
        if tree.set(c, "tab"):
            flag_rows[tree.set(c, "tab").split("  (")[0]] = tree.set(c, "tabvalue")
checks = {"Grid: UPS function": "☑", "Inverter: Ground relay": "☑", "Inverter: PowerAssist": "☑",
          "Inverter: enable AES": "☐", "Charger: Enable charger": "☑", "Charger: Weak AC input": "☐",
          "Charger: Stop after excessive bulk": "☑", "Grid: Accept wide input frequency": "☑",
          "General: Dynamic current limiter": "☐", "General: Enable 2-3 phase": "☐"}
for k, v in checks.items():
    assert flag_rows[k] == v, (k, flag_rows.get(k), v)
print("Settings tab values OK:", len(expected), "values,", len(checks), "checkboxes")

# a second read replaces the tabs (they do not accumulate)
panel._read_settings(root)
root.update()
assert len(panel.settings_notebook.tabs()) == 6, panel.settings_notebook.tabs()

# knob: the last 0x04 of the first capture = 0x63 = 99 -> display " 9.9" (same as the reference screenshot)
assert tail.knob is not None and tail.knob[0] == 99, tail.knob
panel.refresh()
assert panel.canvas.itemcget(panel.knob_disp, "text") == " 9.9", panel.canvas.itemcget(panel.knob_disp, "text")
tail.knob = (24, tail.knob[1])
panel.refresh()
assert panel.canvas.itemcget(panel.knob_disp, "text") == " 2.4"
# calibrated real current: knob 3.6 -> 6.7 A (measured point, no extrapolation)
tail.knob = (36, tail.knob[1])
panel.refresh()
info = panel.canvas.itemcget(panel.knob_real, "text") + " | " + panel.canvas.itemcget(panel.knob_info, "text")
assert "≈ 6.7 A" in panel.canvas.itemcget(panel.knob_real, "text"), info
assert "grid limit (" in panel.canvas.itemcget(panel.knob_info, "text"), info
assert "charger limiting" not in info, info  # IMains 0 in this log -> the charger is not running
# absorption situation: IMains 10.3 A, limit 12.2 A -> "charger limiting"
tail.knob = (66, tail.knob[1])
tail.values[1] = (1030, 10.3, time.time())
panel.refresh()
assert "charger limiting" in panel.canvas.itemcget(panel.knob_info, "text")
tail.knob = (100, tail.knob[1])  # beyond the measured range -> "~"
panel.refresh()
assert "~" in panel.canvas.itemcget(panel.knob_real, "text")
print("Knob OK:", info)

# IOut: negative -> 0.0 (emulator test -1.5 A and -10 A)
fmt_iout = next(r for r in p.ROWS if r and r[0] == "IOut")[3]
assert fmt_iout(-10.0, 64536) == "0.0" and fmt_iout(-0.46, 65490) == "0.0" and fmt_iout(2.44, 244) == "2.4"

# Freq. In: '---' when UMains < 60 V even if the period value stayed in memory (GFCI case)
fmt_in = next(r for r in p.ROWS if r and r[0] == "Freq. In")[3]
p._STATE["umains"] = 0.42
assert fmt_in(60.9, 66) == "- - -"
# display limit measured with the emulator: 59.99 V -> '---', 60.00 V -> shown
p._STATE["umains"] = 59.99
assert fmt_in(59.97, 71) == "- - -"
p._STATE["umains"] = 60.0
assert fmt_in(59.97, 71) == "60.0"
p._STATE["umains"] = 120.7
assert fmt_in(59.97, 71) == "60.0"
# default view without developer numbers
tail.knob = (36, tail.knob[1])
panel.refresh()
assert "0x04=" not in panel.canvas.itemcget(panel.knob_info, "text")
assert panel.canvas.itemcget(panel.led_raw, "text") == ""
assert all(cell[1] is None and cell[2] is None for cell in panel.cells.values())
assert "device fw" not in panel.info.cget("text") and "MK2 " not in panel.info.cget("text")
assert "88.8" not in [panel.canvas.itemcget(i, "text") for i in panel.canvas.find_all()
                      if panel.canvas.type(i) == "text"]

# --debug shows them
dbg_win = tk.Toplevel(root)
dbg = p.Panel(dbg_win, tail, LOG, debug=True)
dbg.refresh()
assert "0x04=36" in dbg.canvas.itemcget(dbg.knob_info, "text") and "≈ 6.7 A" in dbg.canvas.itemcget(dbg.knob_real, "text")
assert dbg.canvas.itemcget(dbg.led_raw, "text").startswith("0x81: lo=")
assert all(cell[1] is not None and cell[2] is not None for cell in dbg.cells.values())
assert "device fw" in dbg.info.cget("text")

# DMC layout: physical (default) <-> classic, redrawn; the choice is saved in the state file
saved = []
p.save_panel_state = lambda d: saved.append(d)   # the real state file is not written
panel.set_dmc_layout("physical")


def dmc_texts(pn):
    c = pn.canvas
    return {c.itemcget(i, "text"): c.coords(i) for i in c.find_all() if c.type(i) == "text"}


tx = dmc_texts(panel)
assert "charger" in tx and "inverter" in tx
assert tx["mains on"][0] < 100 < tx["inverter on"][0], tx          # mains on the left, inverter on the right
sw = [tx[n][0] for n in ("charger only", "off", "on", "inverter only")]
assert sw == sorted(sw) and sw[3] - sw[2] > sw[2] - sw[1], sw      # inverter only last, apart from the others
assert panel.leds["mains on"][1] == "#22c55e" and panel.leds["inverter on"][1] == "#22c55e"
assert panel.leds["bulk"][1] == "#f5e03a" and panel.leds["overload"][1] == "#ff3030"
panel.set_dmc_layout("classic")
tx = dmc_texts(panel)
assert tx["inverter"][0] < 100 < tx["charger"][0], tx                # titles in both layouts
assert tx["inverter on"][0] < 100 < tx["mains on"][0], tx
sw = [tx[n][0] for n in ("on", "off", "charger only", "inverter only")]
assert sw == sorted(sw), sw
assert panel.leds["inverter on"][1] == "#3fe0ff" and panel.dmc_classic_var.get() == 1
assert saved == [{"dmc_layout": "physical"}, {"dmc_layout": "classic"}], saved
panel.refresh()   # LED/switch/knob work after the redraw
assert panel.canvas.itemcget(panel.knob_disp, "text") == " 3.6"
print("DMC layout OK")
root.destroy()
print("OK")
