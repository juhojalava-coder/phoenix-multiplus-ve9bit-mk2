# MQTT bridge: the MultiPlus on Venus GX over the LAN

The bridge (`mk2_mqtt.py`) publishes the measurements and state to the MQTT broker of a Venus GX / Cerbo GX.
The [dbus-mqtt-devices](https://github.com/freakent/dbus-mqtt-devices) driver on Venus turns them into a normal
`vebus` service, so Venus, VRM and the overview page show the MultiPlus like a VE.Bus inverter/charger.

```
MultiPlus -- MK2.2b -- PC (PhoenixConfig + bridge)  ==LAN, MQTT 1883==>  Venus GX (dbus-mqtt-devices)
```

What Venus shows: AC in/out voltage, current, power and frequency, battery voltage and current, state (bulk,
absorption, float, inverting...), LED-based alarms, input current limit, Mode, firmware, product name. Optional
alarms from measured values (high DC voltage/current, ripple) can be configured.

What Venus can control (both off by default):

- **Input current limit** (`allow_current_limit_control`): sets the current limiter knob.
- **Mode** (`allow_mode_control`): charger only / inverter only / on / off. **This switches the load and mains.**

Both also require control to be on: `[control] enabled = true` in `mk2.toml` (the program refuses to start with an
`allow_*` key on and control off), and in PhoenixConfig control on (green **CONTROL ON** on the DMC). In monitor only mode Venus
requests are rejected and logged. Settings writes and device reset are never possible from Venus.

**AC power is an estimate.** The device reports AC voltage and current, not the power factor. The AC power values
(shown as W on Venus/VRM) are U × I × `[venus] power_factor`. The default 1.0 publishes the apparent power (VA),
an upper bound of the real power, so AC powers and the VRM energy totals can read high. Set a typical power factor
of your loads if you know it (resistive loads ~1.0, motors and electronics often 0.6–0.95). Battery (DC) power is
measured.

**Network security.** The bridge uses unencrypted MQTT (port 1883) and, with control on, can change the device.
Use it only on a trusted, closed local network: do not forward port 1883 or expose the Venus to the internet.
A remembered password is stored in plain text in `mk2_panel_state.json`; prefer the `MK2_MQTT_PASSWORD`
environment variable on shared computers.

## 1. Prepare the Venus

1. Install **dbus-mqtt-devices** on the Venus: see [venus-gx-service.md](venus-gx-service.md#1-install-dbus-mqtt-devices).
2. On the Venus: **Settings > Services > MQTT on LAN**: enabled (plain MQTT, port 1883).
3. Note the Venus IP address (**Settings > Ethernet** or **Wi-Fi**). `venus.local` works on many networks.
4. Security profile (Venus OS v3, **Settings > General**): the bridge uses plain MQTT on port 1883 (no TLS).
   The test system used the **Weak** profile (password protected, unencrypted); the MQTT password is then the
   Venus password. TLS-only profiles are not supported by the bridge.

## 2a. From PhoenixConfig (recommended)

1. Connect to the MK2 as usual.
2. Click **⚙** next to "Publish to Venus (MQTT)" and enter the Venus address, port (1883), username (usually empty)
   and password. Tick **Remember the password** to keep it (stored unencrypted in `mk2_panel_state.json`).
3. Tick **Publish to Venus (MQTT)**.

The top bar shows the bridge state:

| Text | Meaning |
|---|---|
| `MQTT: connecting...` | connecting to the broker |
| `MQTT: waiting for Venus...` | connected, waiting for dbus-mqtt-devices to register the device |
| `MQTT on Venus` | the device is visible on Venus |
| `MQTT: password rejected` (red) | wrong password: open ⚙ |
| `MQTT: cannot reach HOST:PORT` (red) | wrong address, MQTT on LAN disabled, or no network |

The password can also come from `mk2.toml` (`[mqtt] password`) or the environment variable `MK2_MQTT_PASSWORD`:

```bat
rem cmd.exe
set MK2_MQTT_PASSWORD=your_password
python mk2_panel.py
```
```powershell
# PowerShell
$env:MK2_MQTT_PASSWORD = "your_password"; python mk2_panel.py
```

## 2b. Standalone bridge (no window)

Set `[serial] port` and `[mqtt]` in `mk2.toml`, then:

```bat
python mk2_mqtt.py                 run the bridge
python mk2_mqtt.py --dry-run       no MQTT, print the messages for 20 s (check the conversion)
python mk2_mqtt.py --port COM3     override the port
```

## On the Venus

The device appears in **Settings > Devices** with the name from `[venus] custom_name`. Each client id
(`[mqtt] client_id`) gets its own device instance; the PC bridge (`mk2pc`), the GX service (`mk2gx`) and the
emulator (`mk2emu`) therefore show up as separate devices and never mix their data.

When the bridge stops (Disconnect, closing the program), the device is reported as disconnected and disappears from
Venus.

## Notes

- If the Venus is connected to VRM with two-way communication, the allowed controls work over the internet too.
- Restart / Equalization on the Venus device page have no effect on this device.
- Venus shows the current limit as the **estimated real grid current** (from the knob calibration), rounded to the
  knob step of 0.1 on the old 16 A scale. Asking for 5.0 A can therefore show 5.1 A.
