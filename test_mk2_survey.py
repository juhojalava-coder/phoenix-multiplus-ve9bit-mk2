"""Offline tests for mk2_survey (summary logic and a short run against the built-in emulator)."""
import json
import os
import shutil
import tempfile

import mk2_survey as s


def rec(t, phase, kind, **kw):
    return {"t": t, "phase": phase, "kind": kind, **kw}


def ram(**over):
    base = {rid: 0 for rid in range(10)}
    base.update({int(k[3:]): v for k, v in over.items()})
    return base


def test_summary_flags_differences():
    r = []
    for i in range(3):
        r.append(rec(i, "on", "sample", ram=ram(), led=0x8030, rx=1, dropped=0, fresh=False))
    r.append(rec(5, "on", "link", up=True))
    r.append(rec(6, "off", "link", up=False))
    for i in range(3):
        r.append(rec(7 + i, "off", "sample", ram={rid: "noreply" for rid in range(10)}, led=None, rx=1, dropped=0,
                     fresh=False))
    for i in range(3):
        r.append(rec(12 + i, "charger", "sample", ram=ram(ram2=11), led=0x8020, rx=1, dropped=0, fresh=False))
    r.append(rec(2, "on", "probe", V="02 FF 56 A9", L="08 FF 4C 00", F05="noreply"))
    r.append(rec(13, "charger", "probe", V="02 FF 56 A9", L="08 FF 4C 00", F05="0A FF 41 01"))
    r.append(rec(3, "on", "settings", reason="x", values={0: 420, 1: 7}))
    r.append(rec(14, "charger", "settings", reason="x", values={0: 420, 1: 9}))
    text = s.summarize(r)
    flagged = text.split("Signals that differ between phases:")[1]
    assert "led" in flagged and "probe_F05" in flagged and "set1" in flagged, text
    assert "probe_V" not in flagged and "set0" not in flagged, text
    assert "0x8030" in text and "0x8020" in text, text
    assert "link" in text.lower() and "DOWN" in text, text
    assert "identical in all phases" in text, text        # unchanged settings are collapsed
    assert any(line.strip().startswith("set0") and "identical" in line and "420" in line
               for line in text.split("\n")), text
    # the samples taken while the device did not answer ("off") must not make every measurement differ
    assert "ram2" in flagged and "ram0" not in flagged and "ram4" not in flagged, text


def test_summary_measurements_use_ranges():
    r = []
    for i in range(12):
        r.append(rec(i, "a", "sample", ram=ram(ram4=2600 + i), led=1, rx=0, dropped=0, fresh=False))
        r.append(rec(20 + i, "b", "sample", ram=ram(ram4=2640 + i), led=1, rx=0, dropped=0, fresh=False))
    text = s.summarize(r)
    row = next(line for line in text.split("\n") if line.strip().startswith(("DIFF ram4", "ram4")))
    assert "2600..2611" in row and "2640..2651" in row and row.startswith("DIFF"), row


def test_run_on_emulator():
    d = tempfile.mkdtemp()
    try:
        import mk2_link as m
        link = m.MK2("emulator", log_dir=None)
        lines = []
        sv = s.Survey(link, d, "first", echo=lines.append)
        sv.settings_ids = (0, 1, 2)
        sv.extra_every_s = 10 ** 6
        sv.markers.put("second")
        try:
            sv.run(seconds=12)
        finally:
            link.close()
        kinds = [r["kind"] for r in sv.records]
        assert "sample" in kinds and "probe" in kinds and "settings" in kinds and "marker" in kinds, kinds
        assert {r["phase"] for r in sv.records} == {"first", "second"}, {r["phase"] for r in sv.records}
        assert os.path.exists(sv.path) and os.path.exists(sv.summary_path)
        with open(sv.path, encoding="utf-8") as fh:
            assert all(json.loads(line) for line in fh)
        sent = [rec_ for rec_ in sv.records if rec_["kind"] == "sample"]
        assert sent and isinstance(sent[0]["ram"][4], int), sent[:1]
        # nothing that changes the device was sent: the link never had a desired switch / knob
        assert link.desired_switch is None and link.desired_knob is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_summary_flags_differences()
    test_summary_measurements_use_ranges()
    test_run_on_emulator()
    print("OK: survey recorder")
