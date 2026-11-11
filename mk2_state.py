"""State file (last sent knob and switch) without GUI dependencies.

The device does not report the knob value or the switch position, so the program (panel or mk2_gx)
saves the values it last sent and applies them at startup (mk2_link.desired_knob/desired_switch).
Used by: mk2_panel.py (default path mk2_panel_state.json) and mk2_gx.py (path from the settings).
"""
import json
import os

PANEL_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mk2_panel_state.json")


def load_panel_state(path=PANEL_STATE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_panel_state(updates, path=PANEL_STATE_FILE):
    data = load_panel_state(path)
    data.update(updates)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def startup_knob(state, lo=None, hi=None):
    """Knob value at startup: the saved one if it is within the allowed range (mk2.toml [knob]), otherwise the minimum."""
    import mk2_link
    lo = mk2_link.KNOB_MIN if lo is None else lo
    hi = mk2_link.KNOB_MAX if hi is None else hi
    v = state.get("knob")
    if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
        return v
    return lo


def startup_switch(state):
    """Switch state at startup: the saved one (1-4) or None = send nothing (the device keeps its state)."""
    import mk2_link
    v = state.get("switch")
    return v if isinstance(v, int) and not isinstance(v, bool) and v in mk2_link.SWITCH_STATES else None
