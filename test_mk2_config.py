"""Offline tests: mk2.toml settings (mk2_config) and software alarms (mk2_mqtt.AlarmRule)."""
import os
import tempfile

import mk2_config as c
import mk2_link
import mk2_mqtt as m

HERE = os.path.dirname(os.path.abspath(__file__))


def _write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mk2.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _err(text):
    try:
        c.load(_write(text))
    except c.ConfigError as e:
        return str(e)
    raise AssertionError("ConfigError missing")


def test_example_file_loads_and_matches_defaults():
    app = c.load(os.path.join(HERE, "mk2.example.toml"))
    assert app.serial.port == "COM1" and app.device.nominal_ac_voltage == 120
    assert app.link_limits() == {"knob_min": 20, "knob_max": 159, "repeat_s": 10.0,
                                 "max_charge_current_a": 45.0, "nominal_ac_voltage": 120}
    assert set(app.alarms) == {"HighDcCurrent", "HighDcVoltage"}
    cfg = app.mqtt_config(env={"MK2_MQTT_PASSWORD": "x"})
    assert cfg.password == "x" and cfg.host == "venus.local" and cfg.alarms["HighDcCurrent"]["alarm"] == 45


def test_missing_file_gives_defaults():
    app = c.load(os.path.join(tempfile.mkdtemp(), "ei_ole.toml"))
    assert app.path is None and app.knob.min_a == 2.0 and app.alarms == {}


def test_rejects_bad_values():
    assert "unknown sections" in _err("[seriall]\nport='COM1'\n")
    assert "unknown keys" in _err("[knob]\nminimi = 2.0\n")
    assert "wrong type" in _err("[knob]\nmin_a = 'kaksi'\n")
    assert "not been defined" in _err("[device]\nnominal_ac_voltage = 230\n")      # project restriction
    assert "unknown device profile" in _err("[device]\nnominal_ac_voltage = 110\n")
    assert "min_a" in _err("[knob]\nmin_a = 1.0\n")                                  # cannot be widened
    assert "max_charge_current_a" in _err("[device]\nmax_charge_current_a = 80\n")
    assert "not allowed" in _err("[alarms.Overload]\nsource='udc'\nalarm=1\n")      # LED alarm is protected
    assert "source" in _err("[alarms.HighDcVoltage]\nsource='lampo'\nalarm=1\n")
    assert "warn > alarm" in _err("[alarms.HighDcVoltage]\nsource='udc'\nwarn=30\nalarm=29\n")
    assert "TOML error" in _err("[knob\n")


def test_link_uses_config_limits():
    lim = c.from_dict({"knob": {"min_a": 3.0, "max_a": 10.0, "repeat_s": 5},
                       "device": {"max_charge_current_a": 40}}).link_limits()
    assert lim["knob_min"] == 30 and lim["knob_max"] == 100 and lim["repeat_s"] == 5.0
    try:
        mk2_link.device_profile(230)
    except mk2_link.NotAllowed as e:
        assert "tested" in str(e)
    else:
        raise AssertionError("230 V should be rejected")
    assert mk2_link.device_profile(120) == mk2_link.HARD_LIMITS


def test_alarm_rule_delay_and_hysteresis():
    r = m.AlarmRule("HighDcCurrent", {"source": "idc_abs", "warn": 40, "alarm": 45, "hyst": 2, "delay_s": 5})
    assert r.update(30, 0) == 0
    assert r.update(-46, 1) == 0          # discharge 46 A (abs) -> waits for the delay
    assert r.update(-46, 5) == 0
    assert r.update(-46, 6.1) == 2        # 5 s continuously -> alarm
    assert r.update(44, 7) == 2           # 44 > 45-2 -> stays
    assert r.update(42.9, 8) == 1         # below 43 -> warning (40 <= 42.9)
    assert r.update(38.5, 9) == 1         # 38.5 > 40-2 -> stays as a warning
    assert r.update(37.9, 10) == 0
    assert r.update(None, 11) == 0        # no information -> unchanged
    r2 = m.AlarmRule("HighDcVoltage", {"source": "udc", "alarm": 29.2})
    assert r2.update(29.0, 0) == 0 and r2.update(29.3, 0) == 2 and r2.update(29.1, 1) == 0


def test_alarm_published_via_publisher():
    import test_mk2_mqtt as t
    holder = {"snap": t.snap(r4=29.5)}    # Udc 29.5 V
    cfg = m.Config(alarms={"HighDcVoltage": {"source": "udc", "warn": 28.8, "alarm": 29.2}})
    pub, cl, _ = t.make(holder, cfg)
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    t.dbus_reply(pub)
    pub.tick()
    vals = [p["value"] for tp, p, _q in cl.published if tp.endswith("/Alarms/HighDcVoltage")]
    assert vals == [2]
    assert not [tp for tp, _p, _q in cl.published if tp.endswith("/Alarms/HighDcCurrent")]   # no rule


def test_venus_limit_control():
    import json
    import knob_cal
    import test_mk2_mqtt as t

    class Link:
        knob_min, knob_max = 20, 159

        def __init__(self):
            self.knob, self.sent = (20, 0.0), []

        def set_knob(self, v):
            self.sent.append(v)
            self.knob = (v, 0.0)

    link, saved = Link(), []
    cal = knob_cal.KnobCal.load()
    handler = m.make_limit_handler(link, cal, on_knob=saved.append)
    holder = {"snap": t.snap()}
    holder["snap"].knob = link.knob
    cfg = m.Config(allow_current_limit_control=True, announce_every_s=1e9)
    pub, cl, clock = t.make(holder, cfg)
    pub.limit_handler = handler
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    t.dbus_reply(pub)                                     # portal abc123, instance 7
    assert "N/abc123/vebus/7/Ac/In/1/CurrentLimit" in cl.subscribed
    topic = "N/abc123/vebus/7/Ac/In/1/CurrentLimit"

    def n(v):
        pub._on_message(cl, None, m._Msg(topic, json.dumps({"value": v})))
    n(9.9)                                                # right after registration -> ignored
    pub.tick()
    assert link.sent == []
    clock.t += m.LIMIT_SETTLE_S + 1
    pub._sent.pop("Ac/In/1/CurrentLimit", None)           # simulate: own value not sent yet
    n(9.9)                                                # before own publication -> ignored
    pub.tick()
    assert link.sent == []
    adj = [p["value"] for tp, p, _q in cl.published if tp.endswith("/Ac/In/1/CurrentLimitIsAdjustable")]
    assert adj == [1]                                     # control allowed -> Venus shows it as adjustable
    assert any(tp == "R/abc123/keepalive" for tp, _p, _q in cl.published)
    n(3.4)                                                # own echo (knob 2.0 -> 3.4 A) -> ignored
    pub.tick()
    assert link.sent == []
    n(6.0)                                                # user: 6 A real
    holder["snap"].knob = None                            # the snapshot is read from the link in the tick
    pub.source = lambda: (setattr(holder["snap"], "knob", link.knob) or holder["snap"])
    pub.tick()
    want = cal.to_knob(6.0)[0]
    assert link.sent == [want] and saved == [want]
    limits = [p["value"] for tp, p, _q in cl.published if tp.endswith("/Ac/In/1/CurrentLimit")]
    assert limits[-1] == round(cal.to_amps(want)[0], 1)   # the real value goes back to Venus
    n(63)                                                 # above the range -> limited to the maximum
    pub.tick()
    assert link.sent[-1] == 159


def test_limit_control_disabled_by_default():
    import test_mk2_mqtt as t
    pub, cl, _ = t.make({"snap": t.snap()})
    pub.limit_handler = None
    assert pub.static_values()["Ac/In/1/CurrentLimitIsAdjustable"] == 0
    assert c.load(os.path.join(HERE, "mk2.example.toml")).venus.allow_current_limit_control is False


class _SwitchLink:
    def __init__(self, state=4):
        self.sent = []
        self.switch = (state, 0.0)

    def set_switch(self, s):
        if s not in mk2_link.SWITCH_STATES:
            raise mk2_link.NotAllowed(f"switch state {s}")
        self.sent.append(s)
        self.switch = (s, 0.0)


def test_venus_mode_control():
    import json
    import test_mk2_mqtt as t
    assert set(m.MODE_STATES) == set(mk2_link.SWITCH_STATES)
    link, saved = _SwitchLink(4), []
    holder = {"snap": t.snap(switch=link.switch)}
    cfg = m.Config(allow_mode_control=True, announce_every_s=1e9)
    clock = t.Clock()
    cl = t.FakeClient()

    def source():
        holder["snap"].switch = link.switch               # the snapshot is read from the link in the tick
        return holder["snap"]
    pub = m.VebusPublisher(cl, cfg, source, clock=clock,
                           mode_handler=m.make_mode_handler(link, on_switch=saved.append))
    pub.attach()
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    t.dbus_reply(pub)
    topic = "N/abc123/vebus/7/Mode"
    assert topic in cl.subscribed
    assert not [s for s in cl.subscribed if "CurrentLimit" in s]   # current limit control not on

    def n(v):
        pub._on_message(cl, None, m._Msg(topic, json.dumps({"value": v})))

    def modes():
        return [p["value"] for tp, p, _q in cl.published if tp.endswith("/vebus/7/Mode")]
    n(3)                                                  # right after registration -> ignored
    pub.tick()
    assert link.sent == [] and modes() == [4]
    adj = [p["value"] for tp, p, _q in cl.published if tp.endswith("/ModeIsAdjustable")]
    assert adj == [1]
    assert any(tp == "R/abc123/keepalive" for tp, _p, _q in cl.published)
    clock.t += m.LIMIT_SETTLE_S + 1
    pub._sent.pop("Mode", None)                           # simulate: own value not published yet
    n(3)
    pub.tick()                                            # before own publication -> ignored (and 4 is published)
    assert link.sent == [] and modes()[-1] == 4
    n(4)                                                  # own echo -> ignored
    pub.tick()
    assert link.sent == []
    for want in (2, 3, 1, 4):
        n(want)
        pub.tick()
        assert link.sent[-1] == want and saved[-1] == want and modes()[-1] == want
    assert link.sent == [2, 3, 1, 4]
    for bad in (9, 0, 5, 2.5, "3", None, True):           # only 1-4 is valid
        before = len(cl.published)
        n(bad)
        pub.tick()
        assert link.sent == [2, 3, 1, 4], bad
        assert modes()[-1] == 4 and len(cl.published) > before   # the real state goes back to Venus
    n(3.0)                                                # a float that converts to an integer is valid
    pub.tick()
    assert link.sent[-1] == 3


def test_mode_control_disabled_by_default():
    import json
    import test_mk2_mqtt as t
    link = _SwitchLink(4)
    holder = {"snap": t.snap(switch=link.switch)}
    pub = m.VebusPublisher(t.FakeClient(), m.Config(), lambda: holder["snap"], clock=t.Clock(),
                           mode_handler=m.make_mode_handler(link))
    assert pub.mode_handler is None and pub.static_values()["ModeIsAdjustable"] == 0
    pub.attach()
    cl = pub.client
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    t.dbus_reply(pub)
    assert not [s for s in cl.subscribed if s.startswith("N/")]
    pub.clock.t += 100
    pub._on_message(cl, None, m._Msg("N/abc123/vebus/7/Mode", json.dumps({"value": 3})))
    pub.tick()
    assert link.sent == [] and not any(tp.startswith("R/") for tp, _p, _q in cl.published)
    assert c.load(os.path.join(HERE, "mk2.example.toml")).venus.allow_mode_control is False
    assert m._mode_handler_for(link, m.Config()) is None


def test_json_config_same_sections():
    import json
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mk2gx.json")
    data = {"serial": {"port": "/dev/serial/by-id/x"}, "mqtt": {"host": "localhost", "client_id": "mk2gx"},
            "control": {"enabled": True},
            "venus": {"label": "mp", "allow_current_limit_control": True, "allow_mode_control": True},
            "knob": {"min_a": 2.0, "max_a": 15.9, "repeat_s": 10},
            "state": {"path": "/data/mk2gx/state.json"},
            "alarms": {"HighDcVoltage": {"source": "udc", "warn": 28.8, "alarm": 29.2, "hyst": 0.2, "delay_s": 5}}}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    app = c.load(p)
    assert app.path == p and app.serial.port == "/dev/serial/by-id/x" and app.state.path == "/data/mk2gx/state.json"
    cfg = app.mqtt_config(env={})
    assert cfg.host == "localhost" and cfg.allow_mode_control and cfg.allow_current_limit_control
    assert cfg.alarms["HighDcVoltage"]["alarm"] == 29.2
    # mk2_mqtt.py --config recognises sectioned JSON (and does not mistake it for the old flat one)
    cfg2, app2 = m.config_from_file(p)
    assert app2 is not None and cfg2.client_id == "mk2gx" and cfg2.allow_mode_control
    # same checks as for TOML
    for bad, msg in (({"venus": {"allow_mode_control": 1}}, "wrong type"), ({"seriall": {}}, "unknown sections"),
                     ({"knob": {"min_a": 1.0}}, "min_a")):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        try:
            c.load(p)
        except c.ConfigError as e:
            assert msg in str(e), (bad, e)
        else:
            raise AssertionError(f"should have been rejected: {bad}")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    try:
        c.load(p)
    except c.ConfigError as e:
        assert "JSON error" in str(e)
    else:
        raise AssertionError("JSON error missing")
    # the old flat JSON still works with mk2_mqtt.py
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"host": "localhost", "client_id": "vanha"}, f)
    cfg3, app3 = m.config_from_file(p)
    assert app3 is None and cfg3.client_id == "vanha"


def test_toml_without_tomllib_gives_clear_error():
    saved = c.tomllib
    try:
        c.tomllib = None
        assert "use a .json" in _err("[knob]\nmin_a = 2.0\n")
    finally:
        c.tomllib = saved


def test_mk2_state_module():
    import mk2_state as s
    import mk2_panel
    assert mk2_panel.load_panel_state is s.load_panel_state and mk2_panel.startup_switch is s.startup_switch
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    assert s.load_panel_state(path) == {}
    s.save_panel_state({"knob": 29}, path)
    s.save_panel_state({"switch": 4}, path)
    st = s.load_panel_state(path)
    assert st == {"knob": 29, "switch": 4}
    assert s.startup_knob(st, 20, 159) == 29 and s.startup_switch(st) == 4
    assert s.startup_knob({"knob": 29}, 30, 100) == 30            # outside the range -> minimum
    assert s.startup_knob({"knob": True}) == mk2_link.KNOB_MIN and s.startup_switch({"switch": True}) is None
    with open(path, "w", encoding="utf-8") as f:
        f.write("[1, 2]")
    assert s.load_panel_state(path) == {}


def test_mk2_gx_helpers():
    import mk2_gx as g
    app = c.from_dict({"state": {"path": "/x/state.json"}})
    assert g.state_path(app) == "/x/state.json" and g.state_path(app, "/y.json") == "/y.json"
    import mk2_state
    assert g.state_path(c.from_dict({})) == mk2_state.PANEL_STATE_FILE

    class L:
        error = None
        values = {}
    lk = L()
    assert g.link_alive(lk, 10, 0)[0]                              # start-up in progress
    assert not g.link_alive(lk, 100, 0)[0]                         # no data after the grace period
    lk.values = {4: (2670, 26.7, 95.0)}
    assert g.link_alive(lk, 100, 0)[0]
    assert not g.link_alive(lk, 200, 0)[0]                         # stale
    lk.error = "connection: x"
    assert not g.link_alive(lk, 1, 0)[0]


def test_control_off_by_default_and_required_for_remote_control():
    assert c.from_dict({}).control.enabled is False
    assert c.load(os.path.join(HERE, "mk2.example.toml")).control.enabled is False
    for key in ("allow_current_limit_control", "allow_mode_control"):
        try:
            c.from_dict({"venus": {key: True}})
        except c.ConfigError as e:
            assert "[control] enabled" in str(e) and key in str(e), e
        else:
            raise AssertionError(f"{key} without [control] enabled should be rejected")
        app = c.from_dict({"control": {"enabled": True}, "venus": {key: True}})
        assert getattr(app.mqtt_config(env={}), key) is True
    try:
        c.from_dict({"control": {"enabled": "yes"}})
    except c.ConfigError as e:
        assert "wrong type" in str(e)
    else:
        raise AssertionError("a non-boolean [control] enabled should be rejected")


def test_power_factor_setting():
    assert c.from_dict({}).mqtt_config(env={}).power_factor == 1.0
    assert c.from_dict({"venus": {"power_factor": 0.8}}).mqtt_config(env={}).power_factor == 0.8
    for bad in (0.05, 1.2, 0):
        try:
            c.from_dict({"venus": {"power_factor": bad}})
        except c.ConfigError as e:
            assert "power_factor" in str(e)
        else:
            raise AssertionError(f"power_factor {bad} should be rejected")


def test_venus_install_defaults_monitor_only():
    with open(os.path.join(HERE, "venus", "install.sh"), encoding="utf-8") as f:
        text = f.read()
    assert "\nLIMIT=false\n" in text and "\nMODE=false\n" in text and "\nCONTROL=false\n" in text
    assert '"control": {"enabled": $CONTROL}' in text and "--allow-limit) LIMIT=true" in text
    assert "LIMIT=true\n" not in text.split("while [ $# -gt 0 ]")[0]      # no remote control by default


if __name__ == "__main__":
    import sys
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("OK")
    sys.exit(0)
