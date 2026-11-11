"""MK2 -> Venus GX display bridge: publishes MultiPlus values over MQTT.

The Venus runs the community driver dbus-mqtt-devices (freakent, v0.9.0), which registers the device
on the Venus D-Bus (service 'vebus') and shows it in the VRM portal. By default this module only
DISPLAYS the values (ModeIsAdjustable = 0). Settings can allow the Venus to control the current limit
(knob) and the Mode (switch), both RAM-level commands (allow_current_limit_control, allow_mode_control).

Registration protocol (driver README):
  1. The PC subscribes to device/<id>/DBus and publishes device/<id>/Status
       {"clientId", "connected": 1, "version", "services": {"<label>": "vebus"}}
  2. The driver replies on device/<id>/DBus: {"portalId", "deviceInstance": {"<label>": n}, ...}
  3. The PC publishes values to W/<portalId>/vebus/<instance>/<path>  {"value": x}
  4. Last will: Status connected = 0 (the device disappears from VRM when the PC or link drops)

Usage:
  python mk2_mqtt.py --port COM1 --config mk2_mqtt.json            (real MQTT, close the panel first)
  python mk2_mqtt.py --port COM1 --dry-run --seconds 20            (no MQTT, prints the messages)

Password: environment variable MK2_MQTT_PASSWORD or the 'password' field of the config file.
Dependencies: pyserial, paho-mqtt (imported only when a real client is created).
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, fields

log = logging.getLogger("mk2_mqtt")

# dbus-mqtt-devices accepts only these characters in clientId and the service label (device_manager._status_is_valid)
_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

# LED word bits (verified on the Digital Multi Control view of the official software, see docs/protocol.md section 6)
BIT_OVERLOAD, BIT_LOWBAT, BIT_TEMP = 0, 1, 2
BIT_INVERTER, BIT_MAINS, BIT_BULK, BIT_ABSORPTION, BIT_FLOAT = 4, 5, 6, 7, 8
MAINS_MIN_V = 60.0   # same rule as the Freq. In row: no frequency below 60 V

_MISSING = object()


# ---------------------------------------------------------------- settings
@dataclass
class Config:
    host: str = "venus.local"        # Venus whose broker we publish to
    port: int = 1883
    username: str = ""
    password: str = ""               # preferably the environment variable MK2_MQTT_PASSWORD
    client_id: str = "mk2pc"         # only A-Z a-z 0-9 _
    label: str = "mp"                # service label (same character restriction)
    custom_name: str = "MultiPlus 24/3000/70-30 120V"
    version: str = "mk2-pc 1.0"
    interval_s: float = 2.0          # publish interval
    refresh_s: float = 30.0          # an unchanged value is re-sent after this long
    announce_every_s: float = 300.0  # registration repeat (in case the driver restarts)
    register_retry_s: float = 10.0   # retry until the driver replies (at boot the driver may start later)
    stale_s: float = 10.0            # a measurement is stale after this long -> device 'gone'
    # False: a missing value is published as zero (safe). True: {"value": null} = not measured.
    # null is documented only in Venus' own notifications (N/), not in writes (W/); needs testing.
    null_for_missing: bool = False
    # True: Input current limit = calibrated real mains current of the knob (knob_calibration.json),
    # False: nominal knob value (scale of the old 16 A panel, about half of the real value)
    calibrated_limit: bool = True
    # Software alarms from measured values. Limits ONLY from the config file (not in code),
    # empty = no alarms. Format: {"HighDcCurrent": {"source": "idc_abs", "warn": 40, "alarm": 45,
    # "hyst": 2, "delay_s": 5}, ...}. See ALARM_PATHS / ALARM_SOURCES and mk2_mqtt.example.json.
    alarms: dict = field(default_factory=dict)
    # True: the Venus (gui-v1/gui-v2/VRM) Input current limit control drives the knob. Value = real
    # mains current (A), converted to a knob value by the calibration and clamped to the [knob] range.
    allow_current_limit_control: bool = False
    # True: the Venus Mode control (1 charger only, 2 inverter only, 3 on, 4 off) drives the switch (Winmon 0x03,
    # RAM level). Default False: Mode is only displayed (ModeIsAdjustable = 0).
    allow_mode_control: bool = False
    keepalive_s: float = 30.0        # R/<portal>/keepalive interval (FlashMQ sends N/ changes only then)
    # The device does not report the power factor: AC power = U * I * power_factor is an ESTIMATE.
    # 1.0 = apparent power (VA), an upper bound of the real power (W). Range 0.1..1.0. DC power is real (U * I).
    power_factor: float = 1.0


LIMIT_PATHS = ("Ac/In/1/CurrentLimit", "Ac/ActiveIn/CurrentLimit")   # gui-v2 / gui-v1 write to these
LIMIT_SETTLE_S = 10.0   # after registration limit messages are ignored (Venus' own initial values, echoes)
KEEPALIVE_PAYLOAD = json.dumps({"keepalive-options": ["suppress-republish"]})


def make_limit_handler(link, cal=None, on_knob=None):
    """Real current (A) requested by the Venus -> knob value (calibrated), clamped to the link range.
    on_knob(raw) is called after a successful set (e.g. to save the panel state file)."""
    def handler(amps):
        lo = getattr(link, "knob_min", 20)
        hi = getattr(link, "knob_max", 159)
        raw = cal.to_knob(amps)[0] if cal else round(amps * 10)
        raw = max(lo, min(hi, raw))
        link.set_knob(raw)
        if on_knob:
            on_knob(raw)
        return raw
    return handler


MODE_PATH = "Mode"
MODE_STATES = (1, 2, 3, 4)   # = mk2_link.SWITCH_STATES (1 charger only, 2 inverter only, 3 on, 4 off)


def make_mode_handler(link, on_switch=None):
    """Mode (1-4) requested by the Venus -> switch (link.set_switch, which re-checks the state).
    on_switch(state) is called after a successful set (state file save)."""
    def handler(state):
        if state not in MODE_STATES:
            raise ValueError(f"Mode {state} not allowed (allowed: {MODE_STATES})")
        link.set_switch(state)
        if on_switch:
            on_switch(state)
        return state
    return handler


# Venus vebus alarm paths that the software rules may use (in the driver's services.yml).
# The LED-based ones (Overload, HighTemperature, LowBattery) belong to the device and must not be overwritten.
ALARM_PATHS = {"HighDcCurrent": "Alarms/HighDcCurrent", "HighDcVoltage": "Alarms/HighDcVoltage",
               "Ripple": "Alarms/Ripple"}
# Measurement sources (RAM id, transform). idc_abs: the wiring limit applies in both directions (charge and discharge).
ALARM_SOURCES = {"udc": (4, None), "idc": (5, None), "idc_abs": (5, abs), "ripple": (6, None)}
_RULE_KEYS = {"source", "warn", "alarm", "hyst", "delay_s"}


def _validate_alarms(alarms):
    if not isinstance(alarms, dict):
        raise ValueError("alarms must be an object")
    for name, r in alarms.items():
        if name not in ALARM_PATHS:
            raise ValueError(f"alarm '{name}' not allowed (allowed: {sorted(ALARM_PATHS)})")
        if not isinstance(r, dict) or set(r) - _RULE_KEYS or "source" not in r:
            raise ValueError(f"alarm '{name}': fields {sorted(_RULE_KEYS)}, source is required")
        if r["source"] not in ALARM_SOURCES:
            raise ValueError(f"alarm '{name}': source '{r['source']}' (allowed: {sorted(ALARM_SOURCES)})")
        levels = [r[k] for k in ("warn", "alarm") if r.get(k) is not None]
        if not levels or not all(isinstance(v, (int, float)) for v in levels):
            raise ValueError(f"alarm '{name}': warn and/or alarm must be a number")
        if r.get("warn") is not None and r.get("alarm") is not None and r["warn"] > r["alarm"]:
            raise ValueError(f"alarm '{name}': warn > alarm")
        for k in ("hyst", "delay_s"):
            if not isinstance(r.get(k, 0), (int, float)) or r.get(k, 0) < 0:
                raise ValueError(f"alarm '{name}': {k} must be a number >= 0")


class AlarmRule:
    """One threshold alarm: level 0/1/2, rises after a delay (delay_s), falls with hysteresis (hyst)."""

    def __init__(self, name, rule):
        self.name, self.path = name, ALARM_PATHS[name]
        self.rid, self.fn = ALARM_SOURCES[rule["source"]]
        self.warn, self.alarm = rule.get("warn"), rule.get("alarm")
        self.hyst, self.delay = float(rule.get("hyst", 0)), float(rule.get("delay_s", 0))
        self.level = 0
        self._pending = None          # (target level, start time) while waiting to rise

    def _threshold(self, level):
        return self.warn if level == 1 else self.alarm

    def _raw_level(self, x):
        if self.alarm is not None and x >= self.alarm:
            return 2
        if self.warn is not None and x >= self.warn:
            return 1
        return 0

    def update(self, x, now):
        """x = measurement (None = unknown -> level unchanged). Returns the current level."""
        if x is None:
            return self.level
        if self.fn:
            x = self.fn(x)
        target = self._raw_level(x)
        if target > self.level:
            if self._pending is None or self._pending[0] != target:
                self._pending = (target, now)
            if now - self._pending[1] >= self.delay:
                self.level, self._pending = target, None
        else:
            self._pending = None
            # fall only when the value is below the current level threshold by the hysteresis
            while self.level > target and x < self._threshold(self.level) - self.hyst:
                self.level -= 1
                while self.level > 0 and self._threshold(self.level) is None:
                    self.level -= 1
        return self.level


def load_config(path=None, env=None):
    cfg = Config()
    env = os.environ if env is None else env
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        unknown = set(data) - {f.name for f in fields(Config)}
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        for k, v in data.items():
            setattr(cfg, k, v)
    if env.get("MK2_MQTT_PASSWORD"):
        cfg.password = env["MK2_MQTT_PASSWORD"]
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    for name in ("client_id", "label"):
        val = getattr(cfg, name)
        if not isinstance(val, str) or not _ID_RE.match(val):
            raise ValueError(f"{name} '{val}' is not valid (allowed: letters, digits and _)")
    if cfg.interval_s <= 0 or cfg.stale_s <= 0:
        raise ValueError("interval_s and stale_s must be > 0")
    pf = cfg.power_factor
    if isinstance(pf, bool) or not isinstance(pf, (int, float)) or not 0.1 <= pf <= 1.0:
        raise ValueError(f"power_factor {pf!r} must be a number 0.1..1.0")
    _validate_alarms(cfg.alarms)


def config_from_file(path=None):
    """mk2.toml (recommended), sectioned JSON (same structure as mk2.toml, e.g. on the Venus) or an old flat
    JSON file. Returns (mk2_mqtt.Config, AppConfig or None)."""
    import mk2_config
    if path and path.lower().endswith(".json"):
        try:
            with open(path, encoding="utf-8") as f:
                sectioned = set(json.load(f)) & set(mk2_config.SECTIONS)
        except (OSError, ValueError, TypeError):
            sectioned = False
        if not sectioned:
            return load_config(path), None
    app = mk2_config.load(path)
    return app.mqtt_config(), app


# ---------------------------------------------------------------- measurement conversion (pure)
@dataclass
class Snapshot:
    values: dict = field(default_factory=dict)   # RAM id -> (raw, converted, timestamp)
    led: tuple = None                             # (lo, hi, timestamp)
    switch: tuple = None                          # (state 1-4, timestamp), last SENT
    now: float = 0.0
    fw: int = None                                # device firmware (Winmon 0x05/0x06), e.g. 1842144
    knob: tuple = None                            # (0x04 value, timestamp), last SENT knob
    ac_limit: float = None                        # setting ID 6 IMainsLimit (A), if settings have been read


def product_id_from_fw(fw):
    """Victron firmware number: first 4 digits = product code in hex, last 3 = version.
    1842144 -> 0x1842 = 6210 'Phoenix MultiPlus 120 24V full power' (version 144).
    Returns None if the number does not have the expected format."""
    if not fw:
        return None
    s = str(fw)
    if len(s) != 7 or not s.isdigit():
        return None
    return int(s[:4], 16)


def snapshot_from_link(link):
    fw = getattr(link, "fw", {}) or {}
    fw = fw[0x82] | fw[0x83] << 16 if 0x82 in fw and 0x83 in fw else None
    ac_limit = None
    settings = getattr(link, "settings", None) or {}
    info = settings.get(6)
    if info and hasattr(link, "display_value"):
        try:
            ac_limit = link.display_value(info, info.get("raw"))
        except Exception:  # noqa: BLE001 - missing scale -> limit from the knob only
            ac_limit = None
    return Snapshot(values=dict(link.values), led=link.led, switch=link.switch, now=time.time(), fw=fw,
                    knob=getattr(link, "knob", None), ac_limit=ac_limit)


def current_limit(snap, cal=None):
    """Real limiting value of the AC input (A): the smaller of the knob (Winmon 0x04) and setting ID 6.

    cal = knob_cal.KnobCal: the knob value is converted to a calibrated real mains current (same
    prediction as the DMC 'limit ≈'). The knob's own scale is that of the old 16 A panel, so the nominal
    value is about half of the real one. Without cal the nominal value is used. None = unknown (not published)."""
    vals = []
    if snap.knob and snap.knob[0]:
        vals.append(cal.to_amps(snap.knob[0])[0] if cal else snap.knob[0] / 10.0)
    if snap.ac_limit:
        vals.append(float(snap.ac_limit))
    return round(min(vals), 1) if vals else None


def _val(snap, rid, stale_s):
    ent = snap.values.get(rid)
    if not ent:
        return None
    _raw, conv, ts = ent
    if conv is None or snap.now - ts > stale_s:
        return None
    return conv


def led_word(snap, stale_s):
    if not snap.led:
        return None
    lo, hi, ts = snap.led
    if snap.now - ts > stale_s:
        return None
    return lo | hi << 8


def has_fresh_data(snap, stale_s):
    return any(snap.now - ent[2] <= stale_s and ent[1] is not None for ent in snap.values.values())


def vebus_state(word):
    """Victron vebus /State: 0 off, 3 bulk, 4 absorption, 5 float, 8 passthru, 9 inverting."""
    if word is None:
        return 0
    if word >> BIT_BULK & 1:
        return 3
    if word >> BIT_ABSORPTION & 1:
        return 4
    if word >> BIT_FLOAT & 1:
        return 5
    if word >> BIT_INVERTER & 1:
        return 9
    if word >> BIT_MAINS & 1:
        return 8
    return 0


def _r(x, n):
    return None if x is None else round(x, n)


def _mul(a, b, n=0):
    return None if a is None or b is None else round(a * b, n)


def _ac_power(u, i, power_factor=1.0):
    """Estimated AC power (W) = U * I * power_factor; U * I alone is apparent power (VA)."""
    return None if u is None or i is None else round(u * i * power_factor, 0)


def build_values(snap, stale_s=10.0, cal=None, power_factor=1.0):
    """Measurements -> {vebus path: value}. None = unknown (published as null = not measured).

    NOTE: the device reports AC voltage and current only, not the power factor. The AC power paths (W on the
    Venus) are therefore an ESTIMATE: P = U * I * power_factor ([venus] power_factor, default 1.0 = apparent
    power VA, an upper bound). VRM energy totals based on them are estimates too. Dc/0/Power = Udc * Idc is real.
    Dc/0/Current: positive = charging (verified with a clamp meter). The sign interpretation
    in VRM must be checked once the driver is installed."""
    def v(rid):
        return _val(snap, rid, stale_s)

    word = led_word(snap, stale_s)
    umains, imains, fin = v(0), v(1), v(8)
    uout, iout, fout = v(2), v(9), v(7)
    udc, idc = v(4), v(5)
    if iout is not None:
        iout = max(iout, 0.0)                    # negative IOut is shown as zero
    if umains is not None and umains < MAINS_MIN_V:
        fin = None                               # no frequency below 60 V ('---')
    if word is not None:
        connected = bool(word >> BIT_MAINS & 1)  # relay closed = mains in use
    else:
        connected = umains is not None and umains >= MAINS_MIN_V
    if not connected:
        umains = imains = fin = None             # ActiveIn = active input; disconnected -> not measured

    out = {
        "State": vebus_state(word),
        "Ac/ActiveIn/Connected": 1 if connected else 0,
        "Ac/ActiveIn/ActiveInput": 0 if connected else 240,
        "Ac/ActiveIn/L1/V": _r(umains, 1),
        "Ac/ActiveIn/L1/I": _r(imains, 1),
        "Ac/ActiveIn/L1/P": _ac_power(umains, imains, power_factor),
        "Ac/ActiveIn/L1/F": _r(fin, 1),
        "Ac/ActiveIn/P": _ac_power(umains, imains, power_factor),
        "Ac/Out/L1/V": _r(uout, 1),
        "Ac/Out/L1/I": _r(iout, 1),
        "Ac/Out/L1/P": _ac_power(uout, iout, power_factor),
        "Ac/Out/L1/F": _r(fout, 1),
        "Ac/Out/P": _ac_power(uout, iout, power_factor),
        "Dc/0/Voltage": _r(udc, 2),
        "Dc/0/Current": _r(idc, 1),
        "Dc/0/Power": _mul(udc, idc),
    }
    if word is not None:
        # The LED bit does not distinguish a blinking pre-warning from a steady alarm. Distinguished by state:
        # if the inverter is off and mains is not in use (shut-down, test 4: LED 0x8002), it is an alarm (2) ->
        # the Venus raises a notification. While the inverter runs it is a pre-warning (1).
        low = 0
        if word >> BIT_LOWBAT & 1:
            shut_down = not (word >> BIT_INVERTER & 1) and not (word >> BIT_MAINS & 1)
            low = 2 if shut_down else 1
        out["Alarms/L1/Overload"] = 2 if word >> BIT_OVERLOAD & 1 else 0
        out["Alarms/L1/HighTemperature"] = 2 if word >> BIT_TEMP & 1 else 0
        out["Alarms/L1/LowBattery"] = low
        out["Alarms/LowBattery"] = low
    if snap.switch and snap.switch[0] in (1, 2, 3, 4):
        out["Mode"] = snap.switch[0]
    limit = current_limit(snap, cal)
    if limit is not None:
        out["Ac/ActiveIn/CurrentLimit"] = limit
        out["Ac/In/1/CurrentLimit"] = limit
    return out


# ---------------------------------------------------------------- MQTT publisher
class VebusPublisher:
    """Registers the MultiPlus with the dbus-mqtt-devices driver and publishes the values.

    The paho client's background thread only calls on_connect/on_message/on_disconnect (they set flags),
    all publishing happens in tick() on the main thread."""

    # Persisted paths (services.yml: persist) are stored in the Venus settings -> sent only once
    # per registration and not repeated when the registration is repeated. ModeIsAdjustable is NOT persisted,
    # so it belongs to the normal paths (restored by the refresh after a driver restart).
    ONCE = ("CustomName", "Ac/In/1/CurrentLimitIsAdjustable", "Ac/ActiveIn/CurrentLimitIsAdjustable")
    # Persisted, changing values: sent only when the value changes (no 30 s refresh), so that
    # the Venus settings file is not written needlessly.
    ON_CHANGE = ("Ac/ActiveIn/CurrentLimit", "Ac/In/1/CurrentLimit")

    def __init__(self, client, cfg, source, clock=time.time, limit_handler=None, mode_handler=None):
        self.client, self.cfg, self.source, self.clock = client, cfg, source, clock
        # Current limit control from the Venus (only if allowed in settings and a handler is given)
        self.limit_handler = limit_handler if cfg.allow_current_limit_control else None
        self._limit_request = None       # (A, time) from the paho thread, handled in tick()
        # Mode control (switch) from the Venus, same safeguards as the current limit
        self.mode_handler = mode_handler if cfg.allow_mode_control else None
        self._mode_request = None        # (value, time)
        self._last_keepalive = 0.0
        self._registered_at = 0.0
        self.status_topic = f"device/{cfg.client_id}/Status"
        self.dbus_topic = f"device/{cfg.client_id}/DBus"
        self.connected = False      # MQTT connection open
        self.last_error = None      # broker rejection (e.g. Not authorized), shown by the panel
        self.announced = False      # Status connected=1 sent
        self.registered = False     # driver replied (portalId + instance known)
        self.portal_id = None
        self.instance = None
        self._sent = {}
        self._last_announce = 0.0
        self.fw = None              # device firmware; the Venus shows the version number of the Status message
        self.rules = [AlarmRule(n, r) for n, r in cfg.alarms.items()]   # software alarms
        if self.rules:
            log.info("software alarms: %s", ", ".join(
                f"{r.name}({r.warn}/{r.alarm}, hyst {r.hyst}, delay {r.delay:g} s)" for r in self.rules))
        self.cal = None             # knob calibration -> Venus Input current limit = real A
        if cfg.calibrated_limit:
            import knob_cal
            self.cal = knob_cal.KnobCal.load()
            log.info("current limit for the Venus calibrated: %s", self.cal.source)
        self._fresh_since = None    # when fresh data started (fw wait timeout)

    # -- attaching to the client ------------------------------------------------
    def attach(self, client=None):
        client = client or self.client
        client.will_set(self.status_topic, self._status_payload(0), qos=1, retain=False)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

    def version_text(self):
        """Venus 'Firmware version' row: the device's own firmware, if known."""
        return str(self.fw) if self.fw else self.cfg.version

    def _status_payload(self, connected):
        return json.dumps({"clientId": self.cfg.client_id, "connected": connected,
                           "version": self.version_text(), "services": {self.cfg.label: "vebus"}},
                          separators=(",", ":"))

    def _on_connect(self, client, userdata, flags, rc, *extra):
        # paho 2: rc = ReasonCode (is_failure), paho 1 / test client: int (0 = OK)
        failed = getattr(rc, "is_failure", None)
        failed = (rc != 0) if failed is None else failed
        if failed:
            self.connected = False
            self.last_error = str(rc)
            if "author" in self.last_error.lower() or "password" in self.last_error.lower():
                self.last_error += " (check MK2_MQTT_PASSWORD / [mqtt] password)"
            log.warning("MQTT connection refused: %s", self.last_error)
            return
        self.last_error = None
        self.connected = True
        self.announced = self.registered = False
        self._sent.clear()
        client.subscribe(self.dbus_topic, 1)
        log.info("MQTT connection open, waiting for data before registering")

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        self.announced = self.registered = False
        log.warning("MQTT connection lost")

    def _limit_topics(self):
        return [f"N/{self.portal_id}/vebus/{self.instance}/{p}" for p in LIMIT_PATHS]

    def _mode_topic(self):
        return f"N/{self.portal_id}/vebus/{self.instance}/{MODE_PATH}"

    def _controls(self):
        return bool(self.limit_handler or self.mode_handler)

    def _on_message(self, client, userdata, msg):
        if self.mode_handler and self.registered and msg.topic == self._mode_topic():
            try:
                val = json.loads(msg.payload.decode("utf-8")).get("value")
            except (ValueError, AttributeError):
                return
            self._mode_request = (val, self.clock())      # validated in tick() (rejection goes to the log)
            return
        if self.limit_handler and self.registered and msg.topic in self._limit_topics():
            try:
                val = json.loads(msg.payload.decode("utf-8")).get("value")
            except (ValueError, AttributeError):
                return
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                self._limit_request = (float(val), self.clock())
            return
        if msg.topic != self.dbus_topic:
            return
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            portal = data["portalId"]
            inst = data["deviceInstance"][self.cfg.label]
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("invalid DBus reply from the driver: %s", e)
            return
        portal, inst = str(portal), str(inst)
        same = self.registered and (portal, inst) == (self.portal_id, self.instance)
        self.portal_id, self.instance = portal, inst
        self.registered = True
        if not same:                # new registration -> publish everything; a repeat does not rewrite persisted values
            self._sent.clear()
            self._limit_request = self._mode_request = None
            self._registered_at = self.clock()
            log.info("registered: portalId=%s instance=%s", self.portal_id, self.instance)
            if self.limit_handler:
                for t in self._limit_topics():
                    self.client.subscribe(t, 0)
                log.info("current limit control from the Venus allowed (%s)", ", ".join(LIMIT_PATHS))
            if self.mode_handler:
                self.client.subscribe(self._mode_topic(), 0)
                log.info("Mode control (switch) from the Venus allowed")
            if self._controls():
                self._last_keepalive = 0.0
        else:
            # The driver may have restarted and restored defaults: resend the changing persisted values
            # (current limit) once. The same value does not change the Venus setting.
            for p in self.ON_CHANGE:
                self._sent.pop(p, None)

    # -- publishing -------------------------------------------------------------
    def _publish_status(self, connected):
        self.client.publish(self.status_topic, self._status_payload(connected), qos=1, retain=False)

    def static_values(self):
        adj = 1 if self.limit_handler else 0
        return {"CustomName": self.cfg.custom_name, "ModeIsAdjustable": 1 if self.mode_handler else 0,
                "Ac/In/1/CurrentLimitIsAdjustable": adj, "Ac/ActiveIn/CurrentLimitIsAdjustable": adj}

    def _handle_limit_request(self, now):
        """User control from the Venus -> knob. Our own echo (same value as sent) is ignored, and
        requests are not accepted until the bridge has itself published the limit in this registration."""
        req, self._limit_request = self._limit_request, None
        if req is None or req[1] - self._registered_at < LIMIT_SETTLE_S:
            return
        amps = req[0]
        last = self._sent.get(LIMIT_PATHS[0], _MISSING)
        if last is _MISSING or abs(amps - last[0]) < 0.05:
            return
        try:
            raw = self.limit_handler(amps)
            log.info("Venus: current limit %.1f A -> knob %.1f", amps, raw / 10)
        except Exception as e:  # noqa: BLE001 - a bad request must not crash the bridge
            log.warning("Venus current limit request %.1f A rejected: %s", amps, e)
        for p in self.ON_CHANGE:          # publish the real value back even if the knob does not change
            self._sent.pop(p, None)

    def _handle_mode_request(self, now):
        """User Mode control from the Venus -> switch. Safeguards as for the current limit: LIMIT_SETTLE_S
        after registration is ignored, our own echo is ignored, and nothing is accepted before our own publish.
        Only 1-4 is valid; any other value is rejected and the real state is published back to the Venus."""
        req, self._mode_request = self._mode_request, None
        if req is None or req[1] - self._registered_at < LIMIT_SETTLE_S:
            return
        last = self._sent.get(MODE_PATH, _MISSING)
        if last is _MISSING:
            return
        val = req[0]
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        if val == last[0] and not isinstance(val, bool):
            return
        try:
            if isinstance(val, bool) or not isinstance(val, int) or val not in MODE_STATES:
                raise ValueError(f"only {MODE_STATES} allowed")
            self.mode_handler(val)
            log.info("Venus: Mode %s -> switch %s", val, val)
        except Exception as e:  # noqa: BLE001 - a bad request must not crash the bridge
            log.warning("Venus Mode request %r rejected: %s", req[0], e)
        self._sent.pop(MODE_PATH, None)   # publish the real state back

    def tick(self):
        """Called every cfg.interval_s: registration, value publishing, 'gone' on stale data."""
        if not self.connected:
            return
        now = self.clock()
        if self._controls() and self.registered:
            if now - self._last_keepalive >= self.cfg.keepalive_s:
                self.client.publish(f"R/{self.portal_id}/keepalive", KEEPALIVE_PAYLOAD, qos=0)
                self._last_keepalive = now
            if self.limit_handler:
                self._handle_limit_request(now)      # before the snapshot -> the new knob is included right away
            if self.mode_handler:
                self._handle_mode_request(now)       # likewise the new switch state
        snap = self.source()
        if not has_fresh_data(snap, self.cfg.stale_s):
            if self.announced:                       # MK2 link went quiet -> device gone from VRM
                log.warning("no fresh data: announcing the device as gone")
                self._publish_status(0)
                self.announced = self.registered = False
                self._sent.clear()
            self._fresh_since = None
            return
        if self._fresh_since is None:
            self._fresh_since = now
        if snap.fw:
            self.fw = snap.fw
        # the driver captures the version at registration -> wait briefly for the firmware (at most stale_s)
        if not self.announced and not self.fw and now - self._fresh_since < self.cfg.stale_s:
            return
        # The driver has not replied -> repeat often (at boot the driver may start after the bridge and miss
        # the first announcement, observed 8.11.2026). After registration, repeat rarely.
        every = self.cfg.announce_every_s if self.registered else self.cfg.register_retry_s
        if not self.announced or now - self._last_announce >= every:
            self._publish_status(1)
            self.announced = True
            self._last_announce = now
        if not self.registered:
            return
        values = build_values(snap, self.cfg.stale_s, self.cal, getattr(self.cfg, "power_factor", 1.0))
        values.update(self.static_values())
        for rule in self.rules:
            values[rule.path] = rule.update(_val(snap, rule.rid, self.cfg.stale_s), now)
        pid = product_id_from_fw(self.fw)
        if pid is not None:                 # otherwise the driver default 9760 (wrong device) stays in effect
            values["ProductId"] = pid
        if not self.cfg.null_for_missing:
            values = {k: (0 if v is None else v) for k, v in values.items()}
        self._publish_values(values, now)

    def _publish_values(self, values, now):
        base = f"W/{self.portal_id}/vebus/{self.instance}/"
        for path, val in values.items():
            prev = self._sent.get(path, _MISSING)
            if path in self.ONCE:
                due = prev is _MISSING
            elif path in self.ON_CHANGE:
                due = prev is _MISSING or prev[0] != val
            else:
                due = prev is _MISSING or prev[0] != val or now - prev[1] >= self.cfg.refresh_s
            if due:
                self.client.publish(base + path, json.dumps({"value": val}, separators=(",", ":")), qos=0)
                self._sent[path] = (val, now)

    def stop(self):
        if self.connected and self.announced:
            self._publish_status(0)
            self.announced = False


# ---------------------------------------------------------------- clients
class _Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload if isinstance(payload, bytes) else payload.encode()


class PrintClient:
    """--dry-run: prints the messages and answers the registration like the driver (portalId 'dryrun')."""

    def __init__(self):
        self.on_connect = self.on_message = self.on_disconnect = None

    def will_set(self, topic, payload=None, qos=0, retain=False):
        print(f"LWT   {topic} {payload}")

    def reconnect_delay_set(self, *a, **k):
        pass

    def username_pw_set(self, *a, **k):
        pass

    def connect_async(self, host, port=1883, keepalive=60):
        print(f"(dry-run: not connecting to {host}:{port})")

    def loop_start(self):
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        print(f"SUB   {topic}")

    def publish(self, topic, payload=None, qos=0, retain=False):
        print(f"PUB   {topic} {payload}")
        if topic.endswith("/Status") and '"connected":1' in payload and self.on_message:
            cid = topic.split("/")[1]
            reply = {"portalId": "dryrun", "deviceInstance": {json.loads(payload)["services"].popitem()[0]: 0}}
            self.on_message(self, None, _Msg(f"device/{cid}/DBus", json.dumps(reply)))


def make_client(cfg):
    """Real paho client. Works with both paho 2.x (CallbackAPIVersion) and 1.x."""
    import paho.mqtt.client as mqtt
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.client_id)
    except AttributeError:
        client = mqtt.Client(client_id=cfg.client_id)
    if cfg.password:
        client.username_pw_set(cfg.username, cfg.password)
    client.reconnect_delay_set(1, 30)
    return client


# ---------------------------------------------------------------- main program
def _limit_handler_for(link, cfg, on_knob=None):
    if not cfg.allow_current_limit_control:
        return None
    cal = None
    if cfg.calibrated_limit:
        import knob_cal
        cal = knob_cal.KnobCal.load()
    return make_limit_handler(link, cal, on_knob)


def _mode_handler_for(link, cfg, on_switch=None):
    if not cfg.allow_mode_control:
        return None
    return make_mode_handler(link, on_switch)


def run(source, client, cfg, seconds=0.0, sleep=time.sleep, clock=time.time, should_stop=lambda: False,
        limit_handler=None, mode_handler=None, on_publisher=None):
    pub = VebusPublisher(client, cfg, source, clock=clock, limit_handler=limit_handler,
                         mode_handler=mode_handler)
    if on_publisher:
        on_publisher(pub)        # start_background: state (connected/registered/last_error) for the panel
    pub.attach(client)
    client.connect_async(cfg.host, cfg.port, keepalive=30)
    client.loop_start()
    t0 = clock()
    try:
        while (not seconds or clock() - t0 < seconds) and not should_stop():
            try:
                pub.tick()
            except Exception:  # noqa: BLE001 - the display bridge must not die on a single error
                log.exception("publish failed")
            sleep(cfg.interval_s)
    except KeyboardInterrupt:
        pass
    finally:
        pub.stop()
        # in the background thread sleep = Event.wait, which returns immediately after stop -> really wait,
        # so that connected:0 gets out before the connection is closed
        (time.sleep if should_stop() else sleep)(0.3)
        client.loop_stop()
        client.disconnect()
    return pub


def start_background(link, cfg, client=None, on_knob=None, on_switch=None):
    """Runs the bridge in a background thread alongside an existing MK2 link (e.g. inside the panel,
    so that the serial port is used by only one program). Returns a stop() function that announces the
    device as gone (connected:0) and closes the MQTT connection. on_knob/on_switch: saving of control
    coming from the Venus (used only if allow_current_limit_control / allow_mode_control is on)."""
    import threading
    client = client or make_client(cfg)
    ev = threading.Event()
    th = threading.Thread(target=run, name="mk2_mqtt", daemon=True,
                          kwargs=dict(source=lambda: snapshot_from_link(link), client=client, cfg=cfg,
                                      sleep=ev.wait, should_stop=ev.is_set,
                                      limit_handler=_limit_handler_for(link, cfg, on_knob),
                                      mode_handler=_mode_handler_for(link, cfg, on_switch),
                                      on_publisher=lambda p: setattr(stop, "publisher", p)))

    def stop(timeout=3.0):
        ev.set()
        th.join(timeout)
    stop.publisher = None        # VebusPublisher, once the thread has started
    th.start()
    return stop


def main(argv=None):
    ap = argparse.ArgumentParser(description="MK2 -> Venus GX (dbus-mqtt-devices) display bridge")
    ap.add_argument("--port", default=None, help="MK2 serial port (default: mk2.toml [serial] port)")
    ap.add_argument("--config", default=None, help="mk2.toml (default) or old .json")
    ap.add_argument("--dry-run", action="store_true", help="no MQTT, print the messages")
    ap.add_argument("--seconds", type=float, default=None, help="run time (default: 20 s with --dry-run, otherwise forever)")
    ap.add_argument("--allow-unknown-firmware", action="store_true",
                    help="RISKY: allow control on device firmware that has not been verified (default: read-only)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg, app = config_from_file(args.config)
    import mk2_link
    port = args.port or (app.serial.port if app else "COM1")
    # monitor only unless mk2.toml [control] enabled = true (an old flat .json never enables control)
    control = bool(app and app.control.enabled)
    link = mk2_link.MK2(port, limits=app.link_limits() if app else None, control=control,
                        allow_unknown_firmware=args.allow_unknown_firmware)
    if not control and (cfg.allow_current_limit_control or cfg.allow_mode_control):
        log.warning("remote control is allowed in the settings but control is off: Venus requests are rejected")
    link.start()
    seconds = args.seconds if args.seconds is not None else (20.0 if args.dry_run else 0.0)
    client = PrintClient() if args.dry_run else make_client(cfg)
    try:
        run(lambda: snapshot_from_link(link), client, cfg, seconds=seconds,
            limit_handler=_limit_handler_for(link, cfg), mode_handler=_mode_handler_for(link, cfg))
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
