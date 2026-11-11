"""Offline tests for the mk2_link driver (no serial port)."""
import mk2_link as m


def test_build_matches_captured_frames():
    # Frames from a capture of the official configuration software
    assert m.build(b"V").hex(" ") == "02 ff 56 a9"
    assert m.build(b"R").hex(" ") == "02 ff 52 ad"
    assert m.build(bytes([0x58, 0x30, 0x02])).hex(" ") == "04 ff 58 30 02 73"
    assert m.build(bytes([0x59, 0x03, 0x04])).hex(" ") == "04 ff 59 03 04 9d"
    assert m.build(bytes([0x58, 0x03, 0x03])).hex(" ") == "04 ff 58 03 03 9f"
    assert m.build(bytes([0x59, 0x36, 0x00, 0x00])).hex(" ") == "05 ff 59 36 00 00 6d"
    assert m.build(bytes.fromhex("53 04 00 ff 01 10 00 04")).hex(" ") == "09 ff 53 04 00 ff 01 10 00 04 8d"


def test_whitelist_blocks_writes():
    for cmd in (0x32, 0x33, 0x34, 0x37, 0x0E):
        assert cmd not in m.ALLOWED_WINMON
    # 1/3/4 verified with the official software, 2 = inverter only (PDF), must be tested
    assert m.ALLOWED_WINMON[0x03] == {b"\x01", b"\x02", b"\x03", b"\x04"}
    assert bytes([0]) not in m.ALLOWED_WINMON[0x03] and bytes([5]) not in m.ALLOWED_WINMON[0x03]
    # 'S' only in exactly the captured NO-SEND forms (0x10 connect, 0x50 before the reset)
    s_ok = {bytes.fromhex("53 04 00 ff 01 10 00 04"), bytes.fromhex("53 04 00 ff 01 50 00 04")}
    assert {p for p in m.ALLOWED_MK2 if p[:1] == b"S"} == s_ok
    # 'F' only 05 (MasterMultiLED); 08 = reset, 06 = snapshot, 09 = BOL must not be allowed
    assert [p for p in m.ALLOWED_MK2 if p[:1] == b"F"] == [b"F\x05"]
    # knob 0x04: only 2.0..15.9 A (raw 20..159); 0 A would switch the device to the inverter/bypass mode
    assert min(m.ALLOWED_WINMON[0x04])[0] == 20 and max(m.ALLOWED_WINMON[0x04])[0] == 159
    assert b"\x00" not in m.ALLOWED_WINMON[0x04] and bytes([160]) not in m.ALLOWED_WINMON[0x04]


def test_knob_frame_matches_capture():
    # official software: "04 FF 5A 04 18 87" = Z 0x04 0x18 (2.4 A)
    assert m.build(bytes([0x5A, 0x04, 0x18])).hex(" ") == "04 ff 5a 04 18 87"
    assert m.build(bytes([0x58, 0x04, 0x63])).hex(" ") == "04 ff 58 04 63 3e"


# ---------------------------------------------------------------- writing
import threading  # noqa: E402

import mk2_decode as d  # noqa: E402


def test_write_frames_match_capture():
    # official software: "05 FF 59 33 2C 00 44" (WriteSetting 44), "05 FF 5A 34 60 00 0E" (data 96)
    assert m.build(bytes([0x59, 0x33, 0x2C, 0x00])).hex(" ") == "05 ff 59 33 2c 00 44"
    assert m.build(bytes([0x5A, 0x34, 0x60, 0x00])).hex(" ") == "05 ff 5a 34 60 00 0e"
    assert m.build(m.S_RESET).hex(" ") == "09 ff 53 04 00 ff 01 50 00 04 4d"
    assert d.RESET_FRAME.hex(" ") == "06 21 00 00 00 00 03 c4"


def test_write_order_is_ve_order_and_complete():
    assert len(m.WRITE_ORDER) == 49 and sorted(m.WRITE_ORDER) == list(range(49))
    assert m.WRITE_ORDER[-1] == 12 and m.WRITE_ORDER[:2] == (0, 1)


def test_merge_flags_preserves_unknown_bits():
    dev = 0x01A4 | (1 << 9) | (1 << 10) | (1 << 12) | (1 << 15)
    # try to clear everything -> bits 9, 10, 12, 15 and 7 are kept (bit 3 does not change)
    assert m.MK2.merge_flags(0, dev, 0x0000) == (1 << 7) | (1 << 9) | (1 << 10) | (1 << 12) | (1 << 15)
    # try to set the NotPromoted bits -> they do not reach the device
    assert m.MK2.merge_flags(0, 0x01A4, 0x01A4 | 0x1600) == 0x01A4
    # Storage mode (bit 11) on
    assert m.MK2.merge_flags(0, 0x01A4, 0x01A4 | 0x0800) == 0x09A4


def test_merge_flags_ups_pair():
    # 0x01A4: bit 3 = 0, bit 7 = 1. Bit 3 on -> bit 7 off automatically
    assert m.MK2.merge_flags(0, 0x01A4, 0x01A4 | 0x08) == 0x012C
    # back
    assert m.MK2.merge_flags(0, 0x012C, 0x0124) == 0x01A4
    # bit 7 cannot be changed on its own
    assert m.MK2.merge_flags(0, 0x01A4, 0x0124) == 0x01A4


def test_merge_flags1_mask():
    assert m.MK2.merge_flags(1, 0xE800, 0x0001) == 0xE001  # bits 13..15 are kept


class FakeLink(m.MK2):
    """MK2 without a serial port: _winmon simulates the device (like mk2_emu)."""

    def __init__(self, settings, values, fw=1842144):
        self.lock = threading.RLock()
        self.settings = {k: dict(v, raw=values.get(k)) for k, v in settings.items()}
        self.dev = dict(values)
        self.sent = []
        self.target = None
        self.fw = {} if fw is None else {0x82: fw & 0xFFFF, 0x83: fw >> 16}
        self.knob = self.switch = None
        self.desired_knob = self.desired_switch = None

    def _winmon(self, cmd, args, replies, timeout):
        with self.lock:
            self.sent.append((cmd, bytes(args)))
            sid = int.from_bytes(args[:2], "little") if args else 0
            if cmd == 0x31:
                return [(0x86, self.dev[sid])]
            if cmd == 0x35:
                return [(0x89, self.settings[sid]["sc"])]
            if cmd == 0x33:
                self.target = sid
                return []
            if cmd == 0x34:
                self.dev[self.target] = sid
                return [(0x88, 0x1BE0)]
            return []


def _fake():
    st = d.decode("data/reference_settings_read.log")
    return FakeLink(st.setting_info, st.setting_val)


def test_desired_knob_is_reasserted():
    f = _fake()
    f.enable_control(True)
    f.switch = f.knob = None
    f.desired_switch, f.desired_knob = None, 66
    f.reassert_controls()
    assert (0x04, bytes([66])) in f.sent and f.knob[0] == 66       # unknown -> sent at once
    f.sent.clear()
    f.reassert_controls(now=f.knob[1] + 1)
    assert not f.sent                                                # fresh -> no repeat
    f.reassert_controls(now=f.knob[1] + m.SWITCH_REPEAT_S + 1)
    assert f.sent == [(0x04, bytes([66]))]                          # repeat, same value
    f.set_knob(40)
    assert f.desired_knob == 40                                     # the user's value stays in force
    f.sent.clear()
    f.desired_switch = 4                                            # saved state at start-up
    f.reassert_controls(now=f.knob[1] + 1)
    assert f.sent == [(0x03, bytes([4]))] and f.switch[0] == 4


def test_knob_limit_text_by_switch_state():
    import mk2_panel as p
    assert p.knob_limit_text(3.4, "≈", 1) == "≈ 3.4 A\ngrid limit (charger only)"   # charger only: directly correct
    assert p.knob_limit_text(3.4, "≈", 1, imains=1.0) == "≈ 3.4 A\ngrid limit (charger only)\ncharger limiting"
    assert p.knob_limit_text(5.4, "≈", 3, imains=5.5) == "≈ 5.4 A\ngrid limit (load + charge)"   # on: the limit applies to the total current
    assert p.knob_limit_text(12.2, "≈", 3, imains=10.3) == "≈ 12.2 A\ngrid limit (load + charge)\ncharger limiting"
    assert p.knob_limit_text(3.4, "≈", 2) == "≈ 3.4 A\napplies in: on / charger only"   # the estimate is always shown
    assert p.knob_limit_text(3.4, "≈", 4) == "≈ 3.4 A\napplies in: on / charger only"
    assert p.knob_inactive(2) and p.knob_inactive(4) and not p.knob_inactive(3) and not p.knob_inactive(1)
    assert not p.knob_inactive(None)
    assert "charger only" in p.knob_limit_text(3.4, "≈", None)


def test_panel_startup_knob_and_state_file():
    import os
    import tempfile
    import mk2_panel as p
    assert p.startup_knob({}) == m.KNOB_MIN
    assert p.startup_knob({"knob": 66}) == 66
    assert p.startup_knob({"knob": 500}) == m.KNOB_MIN and p.startup_knob({"knob": "x"}) == m.KNOB_MIN
    assert p.startup_switch({}) is None and p.startup_switch({"switch": 2}) == 2
    assert p.startup_switch({"switch": 9}) is None
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    assert p.load_panel_state(path) == {}
    p.save_panel_state({"knob": 55}, path)
    assert p.load_panel_state(path) == {"knob": 55}


def test_validate_rejects_out_of_range_and_230v():
    f = _fake()
    assert f.validate_write({5: 117}) == []
    assert f.validate_write({5: 230})  # above the device maximum 128
    assert f.validate_write({5: 93})
    assert f.validate_write({6: 1})  # below 2.0 A
    assert f.validate_write({60: 0})  # not supported
    f.settings[5]["max"] = 240  # even if the device reported a larger maximum
    assert f.validate_write({5: 230})


def test_write_sequence_and_readback():
    f = _fake()
    res = f.write_settings({5: 117, 0: 0x01A4 | 0x0800, 12: f.dev[12]})
    seq = [c for c, _ in f.sent]
    # flags first (0x35 + 0x31 + 0x33 + 0x34 + 0x31), then 5, hysteresis last 12
    assert seq[:5] == [0x35, 0x31, 0x33, 0x34, 0x31]
    writes = [a for c, a in f.sent if c == 0x33]
    assert writes == [b"\x00\x00", b"\x05\x00", b"\x0c\x00"]
    assert f.dev[5] == 117 and f.dev[0] == 0x09A4
    assert all(ok for *_x, ok in res)
    assert f.settings[5]["raw"] == 117


def _raises(fn, exc=m.NotAllowed, text=None):
    try:
        fn()
    except exc as e:
        assert text is None or text in str(e), e
        return e
    raise AssertionError(f"{fn} did not raise {exc.__name__}")


# ---------------------------------------------------------------- B: monitor only
def test_monitor_only_sends_nothing():
    f = _fake()
    assert f.control is False and m.MK2.control is False             # default: monitor only
    f.desired_knob, f.desired_switch = 66, 3                          # even if something was requested
    f.reassert_controls()
    f.reassert_controls(now=1e12)
    assert f.sent == []                                               # no 0x04 / 0x03 at all
    _raises(lambda: f.set_knob(40), text="monitor only")
    _raises(lambda: f.set_switch(3), text="monitor only")
    assert f.sent == [] and f.knob is None and f.switch is None
    f.desired_knob = f.desired_switch = None
    f.enable_control(True)                    # enabling: conservative minimum limit, switch untouched
    assert f.desired_knob == m.KNOB_MIN == 20 and f.desired_switch is None and f.sent == []
    f.reassert_controls()                                             # the poll loop sends it
    assert f.sent == [(0x04, bytes([20]))]
    f.set_knob(40)
    assert f.sent[-1] == (0x04, bytes([40]))
    f.enable_control(False)                                           # off: the wish is forgotten
    assert f.desired_knob is None and f.desired_switch is None
    f.sent.clear()
    f.reassert_controls(now=1e12)
    assert f.sent == []


def test_link_constructor_control_flag():
    import mk2_emu  # noqa: F401 - the emulator port
    for control in (False, True):
        link = m.MK2(m.EMULATOR_PORT, log_dir=None, control=control)
        try:
            assert link.control is control and link.desired_switch is None
            assert link.desired_knob == (link.knob_min if control else None)   # control: conservative minimum
        finally:
            link.close()


def test_switch_never_before_a_known_limit():
    f = _fake()
    f.enable_control(True)
    f.desired_knob = None                     # e.g. nothing chosen
    f.set_switch(1)                           # charger only
    assert f.sent == [(0x04, bytes([m.KNOB_MIN])), (0x03, bytes([1]))]   # the limit goes first
    f.sent.clear()
    f.set_switch(3)                           # limit already sent on this connection -> only the switch
    assert f.sent == [(0x03, bytes([3]))]
    g = _fake()
    g.enable_control(True)
    g.desired_knob, g.desired_switch = 66, 1  # restored state: knob before switch
    g.reassert_controls()
    assert g.sent == [(0x04, bytes([66])), (0x03, bytes([1]))]


def test_monitor_only_connect_sends_no_control_frames_on_emulator():
    """Real link + emulator: connect and poll in monitor only -> no 0x03/0x04 frame in the TX log."""
    import glob
    import os
    import shutil
    import tempfile
    import time
    tmp = tempfile.mkdtemp()

    def tx_frames():
        frames = []
        for path in glob.glob(os.path.join(tmp, "capture_direct_*.log")):
            with open(path, encoding="utf-8") as fh:
                frames += [ln.split("TX", 1)[1].split() for ln in fh if "  TX  " in ln]
        return frames

    def winmon_cmds():
        return [fr[3] for fr in tx_frames() if len(fr) > 3 and fr[1] == "FF" and fr[2] in ("58", "59", "5A")]

    link = m.MK2(m.EMULATOR_PORT, log_dir=tmp)
    link.desired_knob = 66                      # would be sent at once with control on
    link.start()
    try:
        end = time.time() + 20
        while time.time() < end and len(link.values) < 3:
            time.sleep(0.1)
        assert link.values, "no measurements from the emulator"
        assert m.device_fw(link.fw) in m.WRITE_ALLOWED_FW         # the emulator reports the verified firmware
        cmds = winmon_cmds()
        assert "30" in cmds and "03" not in cmds and "04" not in cmds, cmds
        link.enable_control(True)
        link.set_knob(30)
        assert "04" in winmon_cmds()
    finally:
        link.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_panel_connect_monitor_only_and_control():
    """LinkManager: the saved knob/switch are restored only with [control] enabled; set_control sends nothing."""
    import time
    import mk2_config
    import mk2_panel as p
    saved_load, saved_save = p.load_panel_state, p.save_panel_state
    p.load_panel_state = lambda *a, **k: {"knob": 1, "switch": 3, "ports": {m.EMULATOR_PORT: {"knob": 66}}}
    p.save_panel_state = lambda *a, **k: None
    try:
        for enabled in (False, True):
            conn = p.LinkManager(mk2_config.from_dict({"control": {"enabled": enabled}}), None)
            conn.connect(m.EMULATOR_PORT, restore_switch=True)
            try:
                link = conn.link
                assert link.control is enabled
                if enabled:
                    assert link.desired_knob == 66 and link.desired_switch == 3
                else:
                    assert link.desired_knob is None and link.desired_switch is None
            finally:
                conn.disconnect()
        conn = p.LinkManager(mk2_config.from_dict({}), None)
        conn.connect(m.EMULATOR_PORT)
        try:
            end = time.time() + 20
            while time.time() < end and m.device_fw(conn.link.fw) is None:
                time.sleep(0.1)
            conn.set_control(True)
            assert conn.link.control and conn.link.desired_knob == 66     # continues from the saved limit
            assert conn.link.desired_switch is None                       # the switch is untouched
            end = time.time() + 10
            while time.time() < end and conn.link.knob is None:
                time.sleep(0.1)
            assert conn.link.knob and conn.link.knob[0] == 66             # sent by the poll loop
            conn.set_control(False)
            assert not conn.link.control
        finally:
            conn.disconnect()
        _raises(lambda: conn.set_control(True), RuntimeError, "not connected")
        # nothing saved yet: the conservative minimum 2.0 A
        p.load_panel_state = lambda *a, **k: {}
        conn = p.LinkManager(mk2_config.from_dict({}), None)
        conn.connect(m.EMULATOR_PORT)
        try:
            end = time.time() + 20
            while time.time() < end and m.device_fw(conn.link.fw) is None:
                time.sleep(0.1)
            conn.set_control(True)
            assert conn.link.desired_knob == conn.link.knob_min == 20
        finally:
            conn.disconnect()
        # remembered control: the next Connect starts with control on and the saved limit
        p.load_panel_state = lambda *a, **k: {"ports": {m.EMULATOR_PORT: {"control": True, "knob": 55}}}
        conn = p.LinkManager(mk2_config.from_dict({}), None)
        conn.connect(m.EMULATOR_PORT)
        try:
            assert conn.link.control is True and conn.link.desired_knob == 55 and conn.link.desired_switch is None
        finally:
            conn.disconnect()
    finally:
        p.load_panel_state, p.save_panel_state = saved_load, saved_save


def test_port_state_is_per_port():
    import mk2_panel as p
    st = {"knob": 51, "ports": {"emulator": {"control": True, "knob": 159, "confirmed": True}}}
    assert p.port_state(st, "emulator") == {"control": True, "knob": 159, "confirmed": True}
    assert p.port_state(st, "COM10") == {"knob": 51}             # real port: old global knob, no control permission
    assert p.port_state({"knob": 51}, "emulator") == {}           # the emulator never takes the global value
    assert p.port_state({"ports": "x"}, "COM1") == {}
    saved_load, saved_save = p.load_panel_state, p.save_panel_state
    state = {"ports": {"emulator": {"control": True}}}
    p.load_panel_state = lambda *a, **k: dict(state)
    p.save_panel_state = lambda upd, *a, **k: state.update(upd)
    try:
        p.save_port_state("COM10", knob=40)
        p.save_port_state("COM10", control=False)
        p.save_port_state(None, knob=99)                          # no port: nothing saved
    finally:
        p.load_panel_state, p.save_panel_state = saved_load, saved_save
    assert state["ports"] == {"emulator": {"control": True}, "COM10": {"knob": 40, "control": False}}


# ---------------------------------------------------------------- D: firmware lock
def test_unknown_firmware_is_read_only():
    f = FakeLink(_fake().settings, dict(_fake().dev), fw=1842999)
    f.enable_control(True)
    errs = f.validate_write({5: 117})
    assert len(errs) == 1 and "1842999" in errs[0] and "read-only" in errs[0], errs
    _raises(lambda: f.write_settings({5: 117}), text="read-only")
    _raises(lambda: f.set_knob(40), text="read-only")
    _raises(lambda: f.set_switch(3), text="read-only")
    _raises(lambda: f.reset(), text="read-only")
    f.desired_knob = 66
    f.reassert_controls(now=1e12)
    assert f.sent == []                                               # nothing at all was sent
    f.allow_unknown_firmware = True                                   # deliberate override (command line)
    assert f.validate_write({5: 117}) == []
    nofw = FakeLink(_fake().settings, dict(_fake().dev), fw=None)
    assert "not read yet" in nofw.validate_write({5: 117})[0]
    assert m.WRITE_ALLOWED_FW == {1842144}


# ---------------------------------------------------------------- A: stop at the first failed write
class FailingLink(FakeLink):
    """The device stores a wrong value (fail='mismatch'), does not acknowledge (fail='noack') or does not
    answer the flag read (fail='flagread') for setting fail_id."""

    def __init__(self, fail_id, fail="mismatch"):
        base = _fake()
        super().__init__({k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in base.settings.items()},
                         dict(base.dev))
        self.fail_id, self.fail = fail_id, fail

    def _winmon(self, cmd, args, replies, timeout):
        sid = int.from_bytes(args[:2], "little") if args else 0
        if self.fail == "flagread" and cmd == 0x31 and sid == self.fail_id and replies == 1 and timeout == 0.5:
            self.sent.append((cmd, bytes(args)))
            return []
        if cmd == 0x34 and self.target == self.fail_id:
            self.sent.append((cmd, bytes(args)))
            if self.fail == "noack":
                return []
            self.dev[self.target] = sid + 1                           # wrong value stored
            return [(0x88, 0x1BE0)]
        return super()._winmon(cmd, args, replies, timeout)


def test_write_stops_at_first_readback_mismatch():
    f = FailingLink(fail_id=2)
    want = {5: 117, 2: f.dev[2], 12: f.dev[12]}                       # write order: 5, 2, ..., 12
    before12 = f.dev[12]
    e = _raises(lambda: f.write_settings(want), m.WriteAborted)
    assert e.failed_id == 2 and e.not_written == [12]
    assert [x[0] for x in e.results] == [5, 2] and e.results[0][4] and not e.results[1][4]
    assert [a for c, a in f.sent if c == 0x33] == [b"\x05\x00", b"\x02\x00"]   # 12 was NOT written
    assert f.dev[12] == before12 and f.settings_dirty is True
    import mk2_panel as p
    text = p.write_aborted_text(e)
    assert "STOPPED" in text and "NOT reset" in text and "12" in text.split("Not written")[1]
    ok = FakeLink({k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in f.settings.items()}, dict(f.dev))
    ok.settings_dirty = True                  # after a stopped write: no new write before Read settings
    assert "Read settings again" in ok.validate_write({5: 117})[0]
    _raises(lambda: ok.write_settings({5: 117}), text="Read settings again")
    ok.settings_dirty = False                 # what read_settings() does
    ok.write_settings({5: 117})
    assert ok.settings_dirty is False


def test_write_stops_on_link_error():
    """The adapter is unplugged in the middle of the chain: the device state is marked uncertain."""
    import serial

    class Unplugged(FailingLink):
        def _winmon(self, cmd, args, replies, timeout):
            if cmd == 0x34 and self.target == self.fail_id:
                raise serial.SerialException("device disconnected")
            return super()._winmon(cmd, args, replies, timeout)

    f = Unplugged(fail_id=2)
    e = _raises(lambda: f.write_settings({5: 117, 2: f.dev[2], 12: f.dev[12]}), m.WriteAborted, "link error")
    assert e.failed_id == 2 and e.not_written == [12] and f.settings_dirty is True
    assert e.results[-1] == (2, e.results[-1][1], f.dev[2], None, False)
    assert f.validate_write({12: f.dev[12]})                          # refused until the settings are read again


def test_write_stops_when_not_acknowledged():
    f = FailingLink(fail_id=5, fail="noack")
    e = _raises(lambda: f.write_settings({5: 117, 12: f.dev[12]}), m.WriteAborted, "acknowledgement")
    assert e.failed_id == 5 and e.not_written == [12] and f.settings_dirty is True
    assert [a for c, a in f.sent if c == 0x33] == [b"\x05\x00"]


def test_write_stops_when_flags_cannot_be_read():
    f = FailingLink(fail_id=0, fail="flagread")
    e = _raises(lambda: f.write_settings({0: f.dev[0] | 0x0800, 5: 117}), m.WriteAborted, "flags")
    assert e.failed_id == 0 and e.results == [] and e.not_written == [0, 5]
    assert not [c for c, _a in f.sent if c in (0x33, 0x34)]           # nothing was written
    assert f.settings_dirty is False                                  # the device was not changed


def test_no_unchecked_write_via_winmon():
    f = _fake()
    for cmd in (0x33, 0x34):
        try:
            f.winmon(cmd, b"\x05\x00")
        except m.NotAllowed:
            continue
        raise AssertionError("0x33/0x34 meni lapi winmon():sta")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
