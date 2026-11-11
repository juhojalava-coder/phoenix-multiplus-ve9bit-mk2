# Configuration (`mk2.toml`)

Copy `mk2.example.toml` to `mk2.toml` and edit it. Every key is commented in the example file. The file is checked
at start-up: an unknown key, a wrong type or a value outside the built-in safety limits stops the program with a
clear message. **Settings can only narrow the built-in limits, never widen them.**

Other file: `--config path.toml` or the environment variable `MK2_CONFIG`. On Venus OS use the same sections as a
`.json` file ([venus-gx-service.md](venus-gx-service.md#4-configuration)).

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[serial]` | `port` | `COM1` | MK2 port (`COM1`, `/dev/serial/by-id/...`, `emulator`) |
| `[device]` | `nominal_ac_voltage` | `120` | device profile; only 120 is enabled |
| | `max_charge_current_a` | `45` | highest charge current the app will write |
| `[battery]` | `max_absorption_v`, `max_float_v` | `28.6`, `27.6` | warning limits in the write preview (24 V LiFePO4) |
| `[knob]` | `min_a`, `max_a` | `2.0`, `15.9` | allowed current limiter range (old 16 A panel scale) |
| | `repeat_s` | `10` | switch and knob are re-sent this often (RAM, no EEPROM wear) |
| `[control]` | `enabled` | `false` | `false` = **monitor only**: connecting sends no current limit or switch command. `true` = control (saved knob restored on connect; required by the Venus `allow_*` keys) |
| `[state]` | `path` | `""` | state file of `mk2_gx.py` |
| `[mqtt]` | `host`, `port` | `venus.local`, `1883` | Venus MQTT broker |
| | `username`, `password` | `""` | prefer `MK2_MQTT_PASSWORD` or the ⚙ dialog for the password |
| | `client_id` | `mk2pc` | MQTT client id; also selects the Venus device instance |
| `[venus]` | `label`, `custom_name` | `mp`, ... | device id and name on Venus |
| | `interval_s`, `refresh_s` | `2`, `30` | publish interval and refresh of unchanged values |
| | `power_factor` | `1.0` | AC power published to Venus = U × I × this (**estimate**; 1.0 = VA, an upper bound). Range 0.1–1.0 |
| | `allow_current_limit_control` | `false` | Venus/VRM may set the input current limit (requires `[control] enabled = true`) |
| | `allow_mode_control` | `false` | Venus/VRM may change Mode (**switches load and mains**; requires `[control] enabled = true`) |
| `[alarms.<name>]` | `source`, `warn`, `alarm`, `hyst`, `delay_s` | none | alarms `HighDcCurrent`, `HighDcVoltage`, `Ripple` from `udc`, `idc`, `idc_abs`, `ripple` |

PhoenixConfig's ⚙ MQTT settings override `[mqtt] host/port/username/password` and are stored in
`mk2_panel_state.json`; `mk2.toml` is never written by the program.

Not in the settings on purpose: the firmware lock. Writing settings, reset and control work only on verified
device firmware (`mk2_link.WRITE_ALLOWED_FW`); other firmware is read-only unless a program is started with the
command line option `--allow-unknown-firmware` (PhoenixConfig, `mk2_mqtt.py`, `mk2_gx.py`).
