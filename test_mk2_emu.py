"""Emulator replies vs. frames captured from the real device (no serial port)."""
import mk2_emu as e


def frames(letter, cmd, args=b""):
    return [e.build(bytes([letter]) + r).hex(" ") for r in e.winmon_replies(cmd, args)]


def test_version():
    assert frames(0x58, 0x05) == ["05 ff 58 82 e0 1b 27"]   # captured: 05 FF 58 82 E0 1B 27
    assert frames(0x5A, 0x06) == ["05 ff 5a 83 1c 00 03"]   # captured: 05 FF 5A 83 1C 00 03


def test_ram_info_and_values():
    assert frames(0x5A, 0x36, bytes([0, 0])) == ["05 ff 5a 8e 9c 7f f9", "05 ff 5a 8f 00 00 13"]
    assert frames(0x58, 0x36, bytes([7, 0]))[0] == "05 ff 58 8e 57 78 47"      # captured in direct mode
    assert frames(0x58, 0x36, bytes([0x19, 0])) == ["05 ff 58 8e 00 00 16"]    # not supported
    assert frames(0x58, 0x30, bytes([4]))[0].startswith("05 ff 58 85")


def test_settings():
    f = frames(0x58, 0x35, bytes([0, 0]))
    assert f[0] == "05 ff 58 89 01 00 1a" and f[2] == "05 ff 58 8b a4 09 6c" and f[4] == "05 ff 58 8d ff 7f 99"
    assert frames(0x58, 0x35, bytes([0x80, 0])) == ["05 ff 58 89 00 00 1b"]   # not supported (captured)
    assert frames(0x5A, 0x31, bytes([0])) == ["05 ff 5a 86 a4 01 77"]          # Flags0 = 0x01A4 (captured)


def test_led_and_silent_commands():
    e.state["led"] = 0x8120
    assert frames(0x59, 0x02) == ["05 ff 59 81 20 81 " + format((-(0x05 + 0xFF + 0x59 + 0x81 + 0x20 + 0x81)) & 0xFF, "02x")]
    for cmd, args in ((0x03, b"\x03"), (0x04, b"\x63"), (0x44, b"\xfe"), (0x3B, b"")):
        assert frames(0x58, cmd, args) == [], cmd


def test_write_ack():
    assert frames(0x58, 0x33, bytes([5, 0])) == []
    out = frames(0x59, 0x34, bytes([117, 0]))
    assert out and out[0].startswith("05 ff 59 88") and e.values[5] == 117
    e.values[5] = 120


def test_import_ignores_the_command_line():
    """The panel / survey tool import mk2_emu: their arguments (--direct emulator, --config x.toml) must not be
    read as the emulator's port and source log."""
    import os
    import subprocess
    import sys
    code = ("import sys; sys.argv = ['mk2_panel.py', '--config', 'mk2.toml', '20']; import mk2_emu; "
            "assert mk2_emu.SRC == mk2_emu.DEFAULT_SRC and mk2_emu.PORT == 'COM21', (mk2_emu.SRC, mk2_emu.PORT); "
            "assert mk2_emu.ram_info and mk2_emu.settings")
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, "-c", code], cwd=here, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK: emulator replies match the captured ones")
