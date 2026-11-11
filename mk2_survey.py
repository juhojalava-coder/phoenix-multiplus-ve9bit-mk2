"""Read-only survey recorder: finds out which signals depend on something you do to the device, e.g. the
physical front switch (on / charger only / off).

It polls everything that can be read, keeps running when the device stops answering (the front switch passes
through "off"), reconnects by itself, and labels the recording with phases that you set while it runs.
It sends NOTHING that changes the device: no switch, no knob, no settings. Only whitelisted read commands
(V, L, F 05, Winmon 0x02, 0x30, 0x31) are used. Close PhoenixConfig and every other program first.

Usage:   python mk2_survey.py PORT [seconds] [first_phase]
         PORT = COM port of the MK2, or "emulator"
While it runs, type a label and press Enter to start a new phase (for example: charger_only), q + Enter to stop.
After every phase change the settings (IDs 0-65) are read once, and again at the end.

Files (in the current folder):
  survey_<time>.jsonl           every sample / event with time and phase (one JSON object per line)
  survey_<time>_summary.txt     per phase: which values were seen, and which signals differ between phases
  capture_direct_<time>.log     the raw traffic (also the frames the device sends on its own), for the analysis tools

What to look at in the summary: signals marked DIFF, replies of L / F 05 that change, RAM IDs 10-63 that start to
answer, LED bits that were never set before, and what the device does after the reconnect.
"""
import json
import os
import queue
import sys
import threading
import time

import mk2_link as m

RAM_MAIN = tuple(range(10))             # 0..9 (3 is supported but unused)
RAM_EXTRA = tuple(range(10, 64))        # normally not supported; checked now and then
SETTING_IDS = tuple(range(66))          # PDF: 0..65; this device supports 0..48
EXTRA_EVERY_S = 60.0
PROBE_EVERY_S = 10.0
RETRY_S = 10.0                          # reconnect attempt interval while the device does not answer
DOWN_AFTER = 2                          # empty cycles before the link is considered down
MAX_DISTINCT = 8                        # more distinct values than this = a measurement, shown as a range


def hexs(b):
    return " ".join(f"{x:02X}" for x in b) if b else None


def read_ram(link, rid):
    """Raw value as int, 'noreply', or 'rcXX:val' for an unsupported / unexpected reply code."""
    res = link.winmon(0x30, bytes([rid]))
    for rc, val in res:
        if rc == 0x85:
            return val
    if not res:
        return "noreply"
    return f"rc{res[0][0]:02X}:{res[0][1]}"


def read_setting(link, sid):
    res = link.winmon(0x31, bytes([sid & 0xFF, sid >> 8]))
    for rc, val in res:
        if rc == 0x86:
            return val
    if not res:
        return "noreply"
    return f"rc{res[0][0]:02X}:{res[0][1]}"


class Survey:
    settings_ids = SETTING_IDS
    extra_every_s = EXTRA_EVERY_S

    def __init__(self, link, outdir=".", phase="start", echo=print):
        self.link = link
        self.phase = phase
        self.echo = echo
        self.t0 = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(outdir, f"survey_{stamp}.jsonl")
        self.summary_path = os.path.join(outdir, f"survey_{stamp}_summary.txt")
        self.fh = open(self.path, "w", encoding="utf-8", buffering=1)
        self.records = []
        self.markers = queue.Queue()
        self.stop = threading.Event()
        self.up = None
        self._empty = 0
        self._last_led = object()
        self._last_probe = self._last_extra = self._last_retry = 0.0
        self._after_reconnect = 0

    # ------------------------------------------------------------------ logging
    def log(self, kind, **kw):
        rec = {"t": round(time.time() - self.t0, 2), "phase": self.phase, "kind": kind, **kw}
        self.records.append(rec)
        self.fh.write(json.dumps(rec) + "\n")
        return rec

    # ------------------------------------------------------------------ reading
    def sample(self):
        ram = {rid: read_ram(self.link, rid) for rid in RAM_MAIN}
        led = None
        for rc, val in self.link.winmon(0x02):
            if rc == 0x81:
                led = val
        alive = led is not None or any(isinstance(v, int) for v in ram.values())
        return ram, led, alive

    def probe(self):
        """MK2-level read commands: version, LED status (L) and MasterMultiLED (F 05)."""
        out = {}
        for name, payload, expect, vebus in (("V", b"V", 0x56, False), ("L", b"L", 0x4C, False),
                                             ("F05", b"F\x05", 0x41, True)):
            try:
                fr = self.link.mk2_cmd(payload, expect, timeout=1.0 if vebus else 0.6, vebus=vebus)
            except Exception as e:  # noqa: BLE001 - a failing probe must not stop the survey
                out[name] = f"error: {e}"
                continue
            out[name] = hexs(fr) or "noreply"
        self.log("probe", **out)

    def extra(self):
        self.log("extra", ram={rid: read_ram(self.link, rid) for rid in RAM_EXTRA})

    def settings(self, reason):
        values = {sid: read_setting(self.link, sid) for sid in self.settings_ids if not self.stop.is_set()}
        self.log("settings", reason=reason, values=values)

    def reconnect(self):
        try:
            self.link.connect()           # V, R, S: whitelisted
            ok, err = True, None
        except Exception as e:  # noqa: BLE001
            ok, err = False, str(e)
        self.log("reconnect", ok=ok, error=err)
        self.echo(f"[{self.elapsed():6.1f}s] reconnect {'OK' if ok else 'failed: ' + str(err)}")
        return ok

    def elapsed(self):
        return time.time() - self.t0

    # ------------------------------------------------------------------ main loop
    def run(self, seconds=None):
        self.echo(f"Survey started, phase '{self.phase}'. Type a label + Enter for a new phase, q + Enter to stop.")
        try:
            self.link.connect()
        except Exception as e:  # noqa: BLE001 - the loop keeps trying
            self.log("reconnect", ok=False, error=str(e))
            self.echo(f"first connect failed: {e} (will retry)")
        self.probe()
        self.settings("start")
        try:
            while not self.stop.is_set() and (seconds is None or self.elapsed() < seconds):
                self._handle_markers()
                ram, led, alive = self.sample()
                self._track_link(alive)
                self.log("sample", ram=ram, led=led, rx=self.link.rx_frames, dropped=self.link.rx_dropped,
                         fresh=self._after_reconnect > 0)
                self._after_reconnect = max(0, self._after_reconnect - 1)
                if led != self._last_led:
                    self._last_led = led
                    self.echo(f"[{self.elapsed():6.1f}s] [{self.phase}] LED "
                              f"{'-' if led is None else f'0x{led:04X} ' + format(led, '016b')}")
                now = time.time()
                if alive and now - self._last_probe > PROBE_EVERY_S:
                    self._last_probe = now
                    self.probe()
                if alive and now - self._last_extra > self.extra_every_s:
                    self._last_extra = now
                    self.extra()
                if not alive and self._empty >= DOWN_AFTER and now - self._last_retry > RETRY_S:
                    self._last_retry = now
                    self.reconnect()
        except KeyboardInterrupt:
            pass
        if self.up:
            self.settings("end")
        return self.finish()

    def _handle_markers(self):
        while True:
            try:
                label = self.markers.get_nowait()
            except queue.Empty:
                return
            self.phase = label
            self.log("marker", label=label)
            self.echo(f"[{self.elapsed():6.1f}s] phase -> {label} (reading the settings...)")
            self.settings(f"phase {label}")

    def _track_link(self, alive):
        self._empty = 0 if alive else self._empty + 1
        state = alive if alive or self._empty >= DOWN_AFTER else self.up
        if state != self.up:
            self.log("link", up=state)
            self.echo(f"[{self.elapsed():6.1f}s] [{self.phase}] link {'UP' if state else 'DOWN (no reply)'}")
            if state and self.up is False:
                self._after_reconnect = 5
            self.up = state

    def finish(self):
        self.fh.close()
        text = summarize(self.records)
        with open(self.summary_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.echo(text)
        self.echo(f"\nFiles: {self.path}\n       {self.summary_path}")
        return text


# ---------------------------------------------------------------------- analysis (pure, testable)
def _show(sig, v):
    return f"0x{v:04X}" if sig == "led" and isinstance(v, int) else str(v)


COLLAPSE = ("set", "extra")         # families that are listed only when they differ between phases


def _render_rows(rows):
    """Rows of (signal, differs, text). Unchanged settings and unsupported RAM IDs are collapsed into one line."""
    out, group = [], []

    def flush():
        if not group:
            return
        first, last = group[0][0], group[-1][0]
        label = first if first == last else f"{first}..{_sort_key(last)[1]}"
        n = len(group)
        text = f"     {label:10} identical in all phases ({n} signal{'s' if n != 1 else ''})"
        if len({t for _s, _f, t in group}) == 1:        # the same values everywhere: show them
            values = []
            for part in group[0][2].split(" | "):
                for v in part.split(": ", 1)[-1].split(", "):
                    if v not in values:
                        values.append(v)
            text += ": " + ", ".join(values)
        out.append(text)
        group.clear()

    for sig, flag, text in rows:
        if sig.rstrip("0123456789") in COLLAPSE and not flag:
            if group and group[-1][0].rstrip("0123456789") != sig.rstrip("0123456789"):
                flush()
            group.append((sig, flag, text))
            continue
        flush()
        out.append(f"{'DIFF ' if flag else '     '}{sig:10} {text}")
    flush()
    return out


def _sort_key(sig):
    digits = "".join(c for c in sig if c.isdigit())
    return sig.rstrip("0123456789"), int(digits) if digits else 0


def summarize(records):
    """Text report: per phase the values seen per signal, DIFF marks signals that differ between phases."""
    phases = []
    for r in records:
        if r["phase"] not in phases:
            phases.append(r["phase"])
    seen = {}                       # signal -> phase -> list of values
    uptime = {p: [0, 0] for p in phases}
    links = []

    def add(sig, phase, val):
        seen.setdefault(sig, {}).setdefault(phase, []).append(val)

    for r in records:
        p, k = r["phase"], r["kind"]
        if k == "sample":
            uptime[p][1] += 1
            if r["led"] is not None or any(isinstance(v, int) for v in r["ram"].values()):
                uptime[p][0] += 1
            for rid, v in r["ram"].items():
                add(f"ram{int(rid)}", p, v)
            add("led", p, r["led"] if r["led"] is not None else "noreply")
        elif k == "probe":
            for name in ("V", "L", "F05"):
                if name in r:
                    add(f"probe_{name}", p, r[name])
        elif k == "extra":
            for rid, v in r["ram"].items():
                add(f"extra{int(rid)}", p, v)
        elif k == "settings":
            for sid, v in r["values"].items():
                add(f"set{int(sid)}", p, v)
        elif k == "link":
            links.append((r["t"], p, r["up"]))
    lines = [f"Survey summary: {len(records)} records, phases: {', '.join(phases)}", ""]
    lines.append("Link: " + ("; ".join(f"{t:.0f}s {'UP' if up else 'DOWN'} ({p})" for t, p, up in links)
                             if links else "no changes"))
    for p in phases:
        up, n = uptime[p]
        lines.append(f"  phase {p}: {n} samples, answered in {up}")
    lines.append("")
    diffs, rows = [], []
    for sig in sorted(seen, key=_sort_key):
        per = seen[sig]
        distinct = {p: sorted({_show(sig, v) for v in vals}) for p, vals in per.items()}
        noisy = len({v for vs in distinct.values() for v in vs}) > MAX_DISTINCT     # a measurement
        ints = {p: [v for v in vals if isinstance(v, int)] for p, vals in per.items()}
        parts = []
        for p in phases:
            if p not in per:
                continue
            if noisy and ints[p]:
                parts.append(f"{p}: {min(ints[p])}..{max(ints[p])}")
            else:
                parts.append(f"{p}: {', '.join(distinct[p][:MAX_DISTINCT])}")
        flag = False
        if len(per) >= 2:
            if noisy:       # a measurement differs if the ranges of two phases do not overlap
                rng = {p: (min(v), max(v)) for p, v in ints.items() if v}
                flag = any(a[1] < b[0] for a in rng.values() for b in rng.values())
            else:
                # while the device is off (no reply) measurements, LED and settings say nothing: ignore those
                # samples. For the probes and the extra IDs "noreply" is an answer and counts.
                skip = {"noreply"} if sig.startswith(("ram", "led", "set")) else set()
                cmp = {p: tuple(v for v in vs if v not in skip) for p, vs in distinct.items()}
                cmp = {p: v for p, v in cmp.items() if v}
                flag = len(cmp) >= 2 and len(set(cmp.values())) > 1
        rows.append((sig, flag, " | ".join(parts)))
        if flag:
            diffs.append(sig)
    lines += _render_rows(rows)
    lines += ["", f"Signals that differ between phases: {', '.join(diffs) if diffs else 'none'}"]
    return "\n".join(lines) + "\n"


def stdin_reader(survey):
    """Labels from the keyboard: a label starts a new phase, q stops."""
    try:
        for line in sys.stdin:
            label = line.strip()
            if not label:
                continue
            if label.lower() == "q":
                survey.stop.set()
                return
            survey.markers.put(label)
    except (OSError, ValueError):
        pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    port = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else None
    phase = sys.argv[3] if len(sys.argv) > 3 else "start"
    link = m.MK2(port, log_dir=".")          # also writes capture_direct_*.log
    survey = Survey(link, ".", phase)
    threading.Thread(target=stdin_reader, args=(survey,), daemon=True).start()
    try:
        survey.run(seconds)
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
