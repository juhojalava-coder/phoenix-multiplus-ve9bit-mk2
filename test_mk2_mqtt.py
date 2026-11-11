"""Offline tests for mk2_mqtt (no network, no serial port)."""
import json

import mk2_mqtt as m

NOW = 1000.0


def snap(now=NOW, age=0.0, led=(0x60, 0x80), switch=None, fw=1842144, **over):
    """Example situation: on the mains, bulk charging (LED mains + bulk)."""
    ts = now - age
    vals = {
        0: (12194, 122.0, ts), 1: (520, 5.2, ts), 8: (71, 60.0, ts),
        2: (11800, 118.0, ts), 9: (0, 0.0, ts), 7: (71, 60.0, ts),
        4: (2620, 26.2, ts), 5: (21, 21.0, ts), 6: (11, 0.1, ts),
    }
    for rid, conv in over.items():
        vals[int(rid[1:])] = (0, conv, ts)
    return m.Snapshot(values=vals, led=(led[0], led[1], ts) if led else None, switch=switch, now=now, fw=fw)


# ---------------------------------------------------------------- conversion
def test_build_values_charging_from_grid():
    v = m.build_values(snap())
    assert v["State"] == 3 and v["Ac/ActiveIn/Connected"] == 1 and v["Ac/ActiveIn/ActiveInput"] == 0
    assert v["Ac/ActiveIn/L1/V"] == 122.0 and v["Ac/ActiveIn/L1/I"] == 5.2
    assert v["Ac/ActiveIn/L1/P"] == round(122.0 * 5.2) and v["Ac/ActiveIn/P"] == v["Ac/ActiveIn/L1/P"]
    assert v["Ac/ActiveIn/L1/F"] == 60.0
    assert v["Ac/Out/L1/V"] == 118.0 and v["Ac/Out/L1/I"] == 0.0 and v["Ac/Out/L1/F"] == 60.0
    assert v["Dc/0/Voltage"] == 26.2 and v["Dc/0/Current"] == 21.0 and v["Dc/0/Power"] == round(26.2 * 21.0)
    assert v["Alarms/L1/Overload"] == 0 and v["Alarms/L1/LowBattery"] == 0 and v["Alarms/L1/HighTemperature"] == 0
    assert "Mode" not in v          # the switch has not been sent -> nothing is claimed


def test_build_values_inverter_only_on_battery():
    # inverter on LED, mains on off; load 2.6 A from the battery, Idc negative (discharging)
    v = m.build_values(snap(led=(0x10, 0x80), switch=(2, NOW), r9=2.6, r5=-13.0, r0=122.0))
    assert v["State"] == 9 and v["Mode"] == 2
    assert v["Ac/ActiveIn/Connected"] == 0 and v["Ac/ActiveIn/ActiveInput"] == 240
    # there is no active input -> input values are unmeasured (None -> null)
    assert v["Ac/ActiveIn/L1/V"] is None and v["Ac/ActiveIn/L1/I"] is None and v["Ac/ActiveIn/L1/F"] is None
    assert v["Ac/Out/L1/I"] == 2.6 and v["Ac/Out/L1/P"] == round(118.0 * 2.6)
    assert v["Dc/0/Current"] == -13.0 and v["Dc/0/Power"] == round(26.2 * -13.0)


def test_power_factor_estimate():
    # AC power is U * I * power_factor (estimate; U * I alone = VA). DC power is real and not scaled.
    v = m.build_values(snap(r9=2.6), power_factor=0.8)
    assert v["Ac/ActiveIn/L1/P"] == round(122.0 * 5.2 * 0.8) and v["Ac/ActiveIn/P"] == v["Ac/ActiveIn/L1/P"]
    assert v["Ac/Out/L1/P"] == round(118.0 * 2.6 * 0.8) and v["Ac/Out/P"] == v["Ac/Out/L1/P"]
    assert v["Dc/0/Power"] == round(26.2 * 21.0)
    assert m.build_values(snap())["Ac/ActiveIn/L1/P"] == round(122.0 * 5.2)   # default 1.0 = VA as before
    assert m.Config().power_factor == 1.0
    for bad in (0.0, 1.5, True, "0.9"):
        try:
            m.validate_config(m.Config(power_factor=bad))
        except ValueError as e:
            assert "power_factor" in str(e)
        else:
            raise AssertionError(f"power_factor {bad!r} should be rejected")
    # the publisher uses the configured factor
    pub, cl, _clock = make({"snap": snap()}, m.Config(power_factor=0.5, announce_every_s=1e9))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    p = [x["value"] for tp, x, _q in cl.published if tp.endswith("/Ac/ActiveIn/L1/P")]
    assert p and p[-1] == round(122.0 * 5.2 * 0.5), p


def test_state_mapping():
    st = m.vebus_state
    assert st(None) == 0 and st(0x8000) == 0
    assert st(1 << 6 | 1 << 5) == 3 and st(1 << 7) == 4 and st(1 << 8) == 5
    assert st(1 << 4) == 9 and st(1 << 5) == 8
    assert st(1 << 6 | 1 << 4) == 3          # the charging stage wins over the inverter


def test_freq_in_rule_and_iout_clamp():
    v = m.build_values(snap(r0=30.0, r9=-1.5))
    assert v["Ac/ActiveIn/L1/F"] is None     # below 60 V: no period
    assert v["Ac/Out/L1/I"] == 0.0           # negative IOut as zero


def test_alarms_from_led():
    v = m.build_values(snap(led=(0b111, 0x80)))
    assert v["Alarms/L1/Overload"] == 2 and v["Alarms/L1/HighTemperature"] == 2
    assert v["Alarms/L1/LowBattery"] == 2 and v["Alarms/LowBattery"] == 2      # no inverter and no mains
    v = m.build_values(snap(led=(0x12, 0x80)))                                  # low battery + inverter on
    assert v["Alarms/L1/LowBattery"] == 1 and v["Alarms/LowBattery"] == 1      # pre-warning
    v = m.build_values(snap(led=(0x02, 0x80)))                                  # test 4: shut-down 0x8002
    assert v["Alarms/L1/LowBattery"] == 2 and v["State"] == 0
    v = m.build_values(snap(led=(0x22, 0x80)))                                  # low battery + mains on
    assert v["Alarms/L1/LowBattery"] == 1


def test_stale_data():
    assert m.has_fresh_data(snap(age=2.0), 10.0)
    assert not m.has_fresh_data(snap(age=30.0), 10.0)
    assert not m.has_fresh_data(m.Snapshot(now=NOW), 10.0)
    v = m.build_values(snap(age=30.0), 10.0)  # everything stale
    assert v["Dc/0/Voltage"] is None and v["State"] == 0


# ---------------------------------------------------------------- protocol
class FakeClient:
    def __init__(self):
        self.published, self.subscribed, self.will = [], [], None
        self.on_connect = self.on_message = self.on_disconnect = None

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.will = (topic, json.loads(payload), qos)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos))


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def make(snapshot_holder, cfg=None):
    cfg = cfg or m.Config()
    clock = Clock()
    cl = FakeClient()
    pub = m.VebusPublisher(cl, cfg, lambda: snapshot_holder["snap"], clock=clock)
    pub.attach()
    return pub, cl, clock


def drain(cl):
    out, cl.published = cl.published, []
    return out


def dbus_reply(pub, instance=7):
    pub._on_message(None, None, m._Msg(pub.dbus_topic, json.dumps(
        {"portalId": "abc123", "deviceInstance": {pub.cfg.label: instance}})))


def test_last_will_and_subscription():
    pub, cl, _ = make({"snap": snap()})
    assert cl.will == ("device/mk2pc/Status",
                       {"clientId": "mk2pc", "connected": 0, "version": "mk2-pc 1.0", "services": {"mp": "vebus"}}, 1)
    pub._on_connect(cl, None, {}, 0)
    assert cl.subscribed == ["device/mk2pc/DBus"]


def test_registration_then_values():
    holder = {"snap": snap()}
    pub, cl, clock = make(holder)
    pub.tick()
    assert drain(cl) == []                       # no connection -> nothing
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    sent = drain(cl)
    assert len(sent) == 1 and sent[0][0] == "device/mk2pc/Status"
    assert sent[0][1]["connected"] == 1 and sent[0][1]["services"] == {"mp": "vebus"}
    pub.tick()
    assert drain(cl) == []                       # the driver has not replied -> no values
    dbus_reply(pub, 7)
    pub.tick()
    sent = {t: p for t, p, _ in drain(cl)}
    assert sent["W/abc123/vebus/7/Dc/0/Voltage"] == {"value": 26.2}
    assert sent["W/abc123/vebus/7/State"] == {"value": 3}
    assert sent["W/abc123/vebus/7/CustomName"] == {"value": "MultiPlus 24/3000/70-30 120V"}
    assert sent["W/abc123/vebus/7/ModeIsAdjustable"] == {"value": 0}
    assert sent["W/abc123/vebus/7/Ac/ActiveIn/L1/V"] == {"value": 122.0}
    # unchanged ones are not sent again
    pub.tick()
    assert drain(cl) == []
    # a changed value is sent (and the power derived from it), the others are not
    holder["snap"] = snap(r4=26.4)
    pub.tick()
    sent = {t: p for t, p, _ in drain(cl)}
    assert sent == {"W/abc123/vebus/7/Dc/0/Voltage": {"value": 26.4},
                    "W/abc123/vebus/7/Dc/0/Power": {"value": round(26.4 * 21.0)}}


def test_refresh_and_once_paths():
    holder = {"snap": snap()}
    pub, cl, clock = make(holder, m.Config(refresh_s=30.0, announce_every_s=1e9))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    drain(cl)
    clock.t += 31.0
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    paths = [t.split("/vebus/7/")[1] for t, _, _ in drain(cl)]
    assert "Dc/0/Voltage" in paths                     # refresh after 30 s
    assert "ModeIsAdjustable" in paths                 # not persisted -> refreshed
    assert "CustomName" not in paths                   # persisted -> one-off


def test_null_published_for_missing_values():
    holder = {"snap": snap(led=(0x10, 0x80))}          # on the battery: input values unmeasured
    pub, cl, _ = make(holder)
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    sent = {t: p for t, p, _ in drain(cl)}
    # default: a missing value as zero (null is undocumented in W writes)
    assert sent["W/abc123/vebus/7/Ac/ActiveIn/L1/V"] == {"value": 0}
    assert sent["W/abc123/vebus/7/Ac/ActiveIn/ActiveInput"] == {"value": 240}


def test_null_mode_publishes_null():
    holder = {"snap": snap(led=(0x10, 0x80))}
    pub, cl, _ = make(holder, m.Config(null_for_missing=True))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    sent = {t: p for t, p, _ in drain(cl)}
    assert sent["W/abc123/vebus/7/Ac/ActiveIn/L1/V"] == {"value": None}


def test_stale_marks_device_offline_and_recovers():
    holder = {"snap": snap()}
    pub, cl, clock = make(holder)
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    drain(cl)
    holder["snap"] = snap(age=60.0)                    # the MK2 link went quiet
    pub.tick()
    sent = drain(cl)
    assert len(sent) == 1 and sent[0][0] == "device/mk2pc/Status" and sent[0][1]["connected"] == 0
    pub.tick()
    assert drain(cl) == []                             # not repeated
    holder["snap"] = snap()                            # data returns
    pub.tick()
    sent = drain(cl)
    assert len(sent) == 1 and sent[0][1]["connected"] == 1   # registers again
    assert not pub.registered                          # waiting for the driver's reply


def test_reconnect_resets_registration():
    pub, cl, _ = make({"snap": snap()})
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    assert pub.registered
    pub._on_disconnect(cl, None, 0)
    assert not pub.connected and not pub.registered and not pub.announced
    pub._on_connect(cl, None, {}, 0)
    assert pub.connected and not pub.registered and len(cl.subscribed) == 2


def test_announce_retried_until_driver_answers():
    # boot: the driver starts after the bridge and does not see the first Status message
    holder = {"snap": snap()}
    pub, cl, clock = make(holder, m.Config(announce_every_s=300.0, register_retry_s=10.0))
    pub._on_connect(cl, None, {}, 0)

    def statuses():
        return [p for t, p, _q in cl.published if t.endswith("/Status") and p["connected"] == 1]
    pub.tick()
    assert len(statuses()) == 1
    clock.t += 5
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    assert len(statuses()) == 1                 # under 10 s -> no repeat
    clock.t += 6
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    assert len(statuses()) == 2                 # no reply in 10 s -> repeat
    dbus_reply(pub)
    clock.t += 60
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    assert len(statuses()) == 2                 # registered -> rare repeat (300 s)


def test_periodic_reannounce():
    holder = {"snap": snap()}
    pub, cl, clock = make(holder, m.Config(announce_every_s=300.0))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    drain(cl)
    clock.t += 301.0
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    sent = drain(cl)
    assert sent[0][0] == "device/mk2pc/Status" and sent[0][1]["connected"] == 1
    # the driver's reply to the repeat: same registration -> persisted paths are not written again
    dbus_reply(pub)
    clock.t += 1.0
    holder["snap"] = snap(now=clock.t)
    pub.tick()
    paths = [t.split("/vebus/7/")[1] for t, _, _ in drain(cl)]
    assert "CustomName" not in paths and "Ac/In/1/CurrentLimitIsAdjustable" not in paths


def test_bad_dbus_reply_is_ignored():
    pub, cl, _ = make({"snap": snap()})
    for payload in (b"not json", b"{}", b'{"portalId": "x"}', b'{"portalId": "x", "deviceInstance": {"other": 1}}'):
        pub._on_message(None, None, m._Msg(pub.dbus_topic, payload))
    assert not pub.registered
    pub._on_message(None, None, m._Msg("device/other/DBus", b'{"portalId":"x","deviceInstance":{"mp":1}}'))
    assert not pub.registered                          # foreign topic


def test_stop_announces_offline():
    pub, cl, _ = make({"snap": snap()})
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    drain(cl)
    pub.stop()
    sent = drain(cl)
    assert sent[0][1]["connected"] == 0


def test_run_loop_with_print_client_registers_end_to_end(capsys=None):
    # PrintClient answers the registration like the driver -> the whole chain without a network
    import io
    import contextlib
    holder = {"snap": snap()}
    clock = Clock()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        def sleep(s):
            clock.t += s
            holder["snap"] = snap(now=clock.t)
        m.run(lambda: holder["snap"], m.PrintClient(), m.Config(), seconds=6.0, sleep=sleep, clock=clock)
    out = buf.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert "W/dryrun/vebus/0/Dc/0/Voltage" in out                 # registration worked and the values were sent
    assert lines[-1].startswith("PUB   device/mk2pc/Status") and '"connected":0' in lines[-1]   # clean shutdown


def test_start_background_with_panel_link_publishes_mode_and_stops_cleanly():
    # the panel's case: the bridge in a background thread against the same link, sending the switch shows up as the Mode value
    import io
    import contextlib
    import time as _t

    class Link:
        pass
    link = Link()
    s = snap(now=_t.time(), switch=(2, _t.time()))
    link.values, link.led, link.switch = s.values, s.led, s.switch
    link.fw = {0x82: 1842144 & 0xFFFF, 0x83: 1842144 >> 16}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        stop = m.start_background(link, m.Config(interval_s=0.05), client=m.PrintClient())
        _t.sleep(0.4)
        stop()
    out = buf.getvalue()
    assert 'W/dryrun/vebus/0/Mode {"value":2}' in out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert '"connected":0' in lines[-1]


def test_status_version_is_device_firmware():
    pub, cl, _ = make({"snap": snap()})
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    st = [p for t, p, _q in cl.published if t.endswith("/Status")]
    assert st and st[-1]["version"] == "1842144"


def test_announce_waits_for_firmware_then_falls_back():
    holder = {"snap": snap(fw=None)}
    pub, cl, clock = make(holder, m.Config(stale_s=10.0))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    assert not [t for t, _p, _q in cl.published if t.endswith("/Status")]     # waiting for the firmware
    clock.t += 11.0
    holder["snap"] = snap(now=clock.t, fw=None)
    pub.tick()
    st = [p for t, p, _q in cl.published if t.endswith("/Status")]
    assert st and st[-1]["version"] == "mk2-pc 1.0"                     # timeout -> own version


def test_product_id_from_firmware():
    assert m.product_id_from_fw(1842144) == 0x1842 == 6210
    assert m.product_id_from_fw(None) is None and m.product_id_from_fw(12345) is None
    holder = {"snap": snap()}
    pub, cl, _ = make(holder)
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()
    pids = [p["value"] for t, p, _q in cl.published if t.endswith("/ProductId")]
    assert pids == [6210]


def test_current_limit_from_knob_and_setting():
    s = snap()
    assert m.current_limit(s) is None and "Ac/In/1/CurrentLimit" not in m.build_values(s)   # no information -> not published
    s.knob = (66, NOW)
    assert m.current_limit(s) == 6.6
    s.ac_limit = 30.0
    assert m.current_limit(s) == 6.6                      # the knob limits (ID 6 is larger)
    s.ac_limit = 4.0
    assert m.current_limit(s) == 4.0                      # the smaller one wins
    v = m.build_values(s)
    assert v["Ac/ActiveIn/CurrentLimit"] == v["Ac/In/1/CurrentLimit"] == 4.0


def test_current_limit_published_only_on_change():
    holder = {"snap": snap()}
    holder["snap"].knob = (20, NOW)
    pub, cl, clock = make(holder, m.Config(refresh_s=30.0, announce_every_s=1e9))
    pub._on_connect(cl, None, {}, 0)
    pub.tick()
    dbus_reply(pub)
    pub.tick()

    def limits():
        return [p["value"] for t, p, _q in cl.published if t.endswith("/Ac/In/1/CurrentLimit")]
    assert limits() == [3.4]                              # calibrated: knob 2.0 -> 3.4 A (as on the DMC)
    clock.t += 60.0                                       # beyond the refresh interval: no resend
    holder["snap"] = snap(now=clock.t)
    holder["snap"].knob = (20, NOW)
    pub.tick()
    assert limits() == [3.4]
    holder["snap"].knob = (55, clock.t)                   # knob turned -> sent (measured point 10.2 A)
    pub.tick()
    assert limits() == [3.4, 10.2]
    dbus_reply(pub)                                       # repeat of the registration (the driver may have restarted)
    pub.tick()
    assert limits() == [3.4, 10.2, 10.2]                  # the limit once more
    pub.tick()
    assert limits() == [3.4, 10.2, 10.2]


def test_current_limit_nominal_option():
    import knob_cal
    s = snap()
    s.knob = (30, NOW)
    assert m.current_limit(s) == 3.0                                  # nominal value
    assert m.current_limit(s, knob_cal.KnobCal.load()) == 5.4         # calibrated (DMC 'limit ≈ 5.4 A')
    pub, _cl, _ = make({"snap": s}, m.Config(calibrated_limit=False))
    assert pub.cal is None


def test_snapshot_from_link_reads_knob_and_setting6():
    import mk2_link

    class Link:
        values, led, switch, fw = {}, None, None, {}
        knob = (66, 1.0)
        settings = {6: {"raw": 300, "sc": 0, "offset": 0}}
        display_value = staticmethod(lambda info, raw: raw / 10.0)
    s = m.snapshot_from_link(Link())
    assert s.knob == (66, 1.0) and s.ac_limit == 30.0 and m.current_limit(s) == 6.6
    assert mk2_link.MK2.display_value  # exists in the real link


def test_snapshot_from_link_reads_fw():
    class Link:
        values, led, switch = {}, None, None
        fw = {0x82: 1842144 & 0xFFFF, 0x83: 1842144 >> 16}
    assert m.snapshot_from_link(Link()).fw == 1842144


def test_infer_switch_from_leds():
    import mk2_link as L
    assert L.infer_switch(None) is None
    assert L.infer_switch(0x8000, 121.0)[0] == {4}            # all dark, mains present -> off
    assert L.infer_switch(0x8000, 0.0)[0] == {4, 1}           # without mains also charger only
    assert L.infer_switch(0x8010, 122.0)[0] == {2}            # inverts although the mains is present
    assert L.infer_switch(0x8010, 0.0)[0] == {3, 2}
    assert L.infer_switch(0x8020, 121.0)[0] == {3, 1}         # mains on
    assert L.infer_switch(0x8060, 121.0)[0] == {3, 1}         # mains on + bulk
    assert L.infer_switch(0x8002, 122.0)[0] == {3, 2}         # test 4: shut-down
    assert L.device_fw({0x82: 1842144 & 0xFFFF, 0x83: 1842144 >> 16}) == 1842144 and L.device_fw({}) is None


# ---------------------------------------------------------------- settings
def test_config_defaults_and_env_password(tmp_path=None):
    cfg = m.load_config(None, env={"MK2_MQTT_PASSWORD": "secret"})
    assert cfg.password == "secret" and cfg.host == "venus.local"


def test_config_rejects_unknown_and_bad_ids():
    import os
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "c.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"hots": "x"}, f)
    for bad in ({"hots": "x"}, {"client_id": "a-b"}, {"label": "m.p"}, {"client_id": ""}, {"interval_s": 0},
                {"client_id": "mk2!"}, {"client_id": "mk 2"}):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        try:
            m.load_config(p, env={})
        except ValueError:
            continue
        raise AssertionError(f"should have been rejected: {bad}")
    os.remove(p)
    os.rmdir(d)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("OK")
