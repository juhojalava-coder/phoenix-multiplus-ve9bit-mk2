"""Shared settings file mk2.toml for all programs (panel, bridge, future control core).

Lookup: --config argument, environment variable MK2_CONFIG, or mk2.toml next to this file.
If the file does not exist, defaults are used (the same as in mk2.example.toml).

Validation: unknown section or key, wrong type, or a value outside the safety limits -> ConfigError
with a clear message before the serial port is opened.

What is NOT in the settings (on purpose, see docs/protocol.md and mk2_link.py): allowed MK2 commands, protection of
Flags0 bits 9/10/12, the write order and the absolute protocol limits of the knob. The settings can
only narrow the safety limits, never widen them.

Dependency: tomllib (Python 3.11+ standard library), tomli on older versions. If neither is available (e.g. Venus OS),
use a JSON file (.json) with the same sections: {"serial": {"port": ...}, "venus": {...}, "alarms": {...}}.
"""
import json
import os
from dataclasses import dataclass, field, fields

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 or Venus OS (tomllib missing)
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "mk2.toml")

# Absolute limits (protocol / this device). The settings may only be within these.
KNOB_HARD_MIN_A, KNOB_HARD_MAX_A = 2.0, 15.9      # mk2_link.KNOB_MIN/KNOB_MAX (0.1 A)
IBATBULK_HARD_MAX_A = 70.0                        # MultiPlus 24/3000/70: charger maximum
POWER_FACTOR_MIN = 0.1                            # [venus] power_factor range 0.1..1.0
# Mains voltage -> device profile in mk2_link.DEVICE_PROFILES. Only a tested profile is accepted.


class ConfigError(ValueError):
    pass


@dataclass
class Serial:
    port: str = "COM1"                # Linux: /dev/serial/by-id/usb-...


@dataclass
class Device:
    nominal_ac_voltage: int = 120     # device profile; only 120 is tested (mk2_link.DEVICE_PROFILES)
    max_charge_current_a: float = 45.0  # upper limit for writing IBatBulk (ID 4), e.g. wiring capacity


@dataclass
class Battery:
    max_absorption_v: float = 28.6    # panel warning when writing settings (24 V LiFePO4)
    max_float_v: float = 27.6


@dataclass
class Knob:
    min_a: float = 2.0                # allowed range of the knob (Winmon 0x04) in the panel
    max_a: float = 15.9
    repeat_s: float = 10.0            # switch and knob are re-sent at this interval


@dataclass
class Control:
    # False = monitor only: opening the connection sends no knob (current limit) or switch (Mode) command.
    # True = control: the saved knob (and in --direct / the GX service the saved switch) is restored on connect,
    # and the panel / Venus may change them. Remote control ([venus] allow_*) requires this.
    enabled: bool = False


@dataclass
class Mqtt:
    host: str = "venus.local"
    port: int = 1883
    username: str = ""
    password: str = ""                # preferably the environment variable MK2_MQTT_PASSWORD
    client_id: str = "mk2pc"


@dataclass
class Venus:
    label: str = "mp"
    custom_name: str = "MultiPlus 24/3000/70-30 120V"
    interval_s: float = 2.0
    refresh_s: float = 30.0
    announce_every_s: float = 300.0
    stale_s: float = 10.0
    null_for_missing: bool = False
    calibrated_limit: bool = True
    allow_current_limit_control: bool = False   # Venus Input current limit control drives the knob
    allow_mode_control: bool = False            # Venus Mode control (1-4) drives the switch
    keepalive_s: float = 30.0
    # The device reports AC voltage and current only, so the AC power for the Venus is estimated:
    # P = U * I * power_factor. 1.0 = apparent power (VA), an upper bound of the real power (W).
    power_factor: float = 1.0


@dataclass
class State:
    # State file of mk2_gx.py (last sent knob and switch). Empty = mk2_panel_state.json
    # in the program folder. The panel always uses mk2_panel_state.json.
    path: str = ""


@dataclass
class AppConfig:
    serial: Serial = field(default_factory=Serial)
    state: State = field(default_factory=State)
    control: Control = field(default_factory=Control)
    device: Device = field(default_factory=Device)
    battery: Battery = field(default_factory=Battery)
    knob: Knob = field(default_factory=Knob)
    mqtt: Mqtt = field(default_factory=Mqtt)
    venus: Venus = field(default_factory=Venus)
    alarms: dict = field(default_factory=dict)
    path: str = None                  # where it was read from (None = defaults)

    # -- derived values for other modules ------------------------------------
    def knob_raw_range(self):
        return round(self.knob.min_a * 10), round(self.knob.max_a * 10)

    def link_limits(self):
        """mk2_link.MK2(limits=...)"""
        lo, hi = self.knob_raw_range()
        return {"knob_min": lo, "knob_max": hi, "repeat_s": self.knob.repeat_s,
                "max_charge_current_a": self.device.max_charge_current_a,
                "nominal_ac_voltage": self.device.nominal_ac_voltage}

    def mqtt_config(self, env=None):
        """mk2_mqtt.Config from these settings (+ password from the environment variable)."""
        import mk2_mqtt
        env = os.environ if env is None else env
        cfg = mk2_mqtt.Config(
            host=self.mqtt.host, port=self.mqtt.port, username=self.mqtt.username,
            password=env.get("MK2_MQTT_PASSWORD") or self.mqtt.password, client_id=self.mqtt.client_id,
            label=self.venus.label, custom_name=self.venus.custom_name, interval_s=self.venus.interval_s,
            refresh_s=self.venus.refresh_s, announce_every_s=self.venus.announce_every_s,
            stale_s=self.venus.stale_s, null_for_missing=self.venus.null_for_missing,
            calibrated_limit=self.venus.calibrated_limit, alarms=dict(self.alarms),
            allow_current_limit_control=self.venus.allow_current_limit_control,
            allow_mode_control=self.venus.allow_mode_control,
            keepalive_s=self.venus.keepalive_s, power_factor=self.venus.power_factor)
        mk2_mqtt.validate_config(cfg)
        return cfg


SECTIONS = {"serial": Serial, "device": Device, "battery": Battery, "knob": Knob, "mqtt": Mqtt, "venus": Venus,
            "state": State, "control": Control}


def _section(name, cls, data):
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a section")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"[{name}] unknown keys: {sorted(unknown)} (allowed: {sorted(known)})")
    obj = cls()
    for k, v in data.items():
        default = getattr(obj, k)
        if isinstance(default, bool):
            ok = isinstance(v, bool)
        elif isinstance(default, float):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
            v = float(v) if ok else v
        elif isinstance(default, int):
            ok = isinstance(v, int) and not isinstance(v, bool)
        else:
            ok = isinstance(v, str)
        if not ok:
            raise ConfigError(f"[{name}] {k} = {v!r}: wrong type (expected {type(default).__name__})")
        setattr(obj, k, v)
    return obj


def _check(cfg):
    d, k = cfg.device, cfg.knob
    import mk2_link
    try:
        mk2_link.device_profile(d.nominal_ac_voltage)
    except mk2_link.NotAllowed as e:
        raise ConfigError(f"[device] nominal_ac_voltage = {d.nominal_ac_voltage}: {e}") from e
    if not 0 < d.max_charge_current_a <= IBATBULK_HARD_MAX_A:
        raise ConfigError(f"[device] max_charge_current_a = {d.max_charge_current_a}: "
                          f"must be 0 < x <= {IBATBULK_HARD_MAX_A} A")
    if not KNOB_HARD_MIN_A <= k.min_a <= k.max_a <= KNOB_HARD_MAX_A:
        raise ConfigError(f"[knob] min_a {k.min_a} / max_a {k.max_a}: must satisfy "
                          f"{KNOB_HARD_MIN_A} <= min_a <= max_a <= {KNOB_HARD_MAX_A}")
    if k.repeat_s <= 0:
        raise ConfigError("[knob] repeat_s must be > 0")
    if not cfg.serial.port:
        raise ConfigError("[serial] port is missing")
    if not 0 < cfg.mqtt.port < 65536:
        raise ConfigError(f"[mqtt] port = {cfg.mqtt.port}")
    v = cfg.venus
    remote = [k for k in ("allow_current_limit_control", "allow_mode_control") if getattr(v, k)]
    if remote and not cfg.control.enabled:
        raise ConfigError(f"[venus] {', '.join(remote)} = true requires [control] enabled = true "
                          f"(monitor only sends nothing to the device)")
    if not POWER_FACTOR_MIN <= v.power_factor <= 1.0:
        raise ConfigError(f"[venus] power_factor = {v.power_factor}: must be {POWER_FACTOR_MIN}..1.0")


def from_dict(data, path=None):
    unknown = set(data) - set(SECTIONS) - {"alarms"}
    if unknown:
        raise ConfigError(f"unknown sections: {sorted(unknown)} (allowed: {sorted(SECTIONS) + ['alarms']})")
    cfg = AppConfig(path=path)
    for name, cls in SECTIONS.items():
        if name in data:
            setattr(cfg, name, _section(name, cls, data[name]))
    cfg.alarms = data.get("alarms", {})
    _check(cfg)
    try:
        cfg.mqtt_config(env={})       # also validates alarms and identifiers
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return cfg


def load(path=None):
    """Reads the settings. path=None -> MK2_CONFIG or mk2.toml; missing file -> defaults.
    Extension .json -> JSON with the same section structure as TOML (Venus OS has no tomllib)."""
    path = path or os.environ.get("MK2_CONFIG") or DEFAULT_PATH
    if not os.path.exists(path):
        return from_dict({}, path=None)
    if path.lower().endswith(".json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError as e:
            raise ConfigError(f"{path}: JSON error: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: the JSON file must be an object")
    else:
        if tomllib is None:
            raise ConfigError(f"{path}: TOML support missing (tomllib/tomli), use a .json settings file")
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: TOML error: {e}") from e
    try:
        return from_dict(data, path=path)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e
