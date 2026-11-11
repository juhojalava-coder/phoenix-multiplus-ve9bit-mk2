# Testing without hardware: the emulator

`mk2_emu.py` emulates an MK2 interface with a Phoenix MultiPlus 120 V (firmware 1842144). Its answers come from a
capture of the real device, so settings, scales, firmware and timing (about 45 s to read all settings) match the
real unit.

## In PhoenixConfig

1. `python mk2_panel.py`
2. Port: **emulator (built-in, no device)**, **Connect**.

The connection dot is orange while the emulator is used. Everything works like with the real device: measurements,
DMC, reading and writing settings (writes stay in memory until the program is closed), settings backup. As with the
real device, Connect is monitor only: click **Monitor only - click to enable control** on the DMC to try the switch
and the knob.

An **EMULATOR** card appears in the left column:

| Field | Unit |
|---|---|
| Battery voltage | V |
| Battery current (+ charge, − discharge) | A |
| AC out current | A |
| AC in voltage | V |
| AC in current | A |

Values apply with Enter, the arrows or when leaving the field. **Click the DMC LEDs** to switch them on or off (for
example bulk, float, overload); LED clicks do nothing with a real device.

## MQTT test without a MultiPlus

Tick **Publish to Venus (MQTT)** while connected to the emulator. The emulator is published as a **separate
device**: name "EMULATOR (PhoenixConfig)", client id `mk2emu`. It gets its own device instance on Venus and never
mixes with a real MultiPlus. You only need a Venus OS device with dbus-mqtt-devices
([venus-gx-service.md](venus-gx-service.md#1-install-dbus-mqtt-devices)) and MQTT on LAN.

Change the values in the EMULATOR card and watch Venus: battery voltage/current, AC loads, state from the LEDs
(bulk / absorption / float), alarms (`[alarms]` in `mk2.toml`), and, if allowed (`[control] enabled` plus the
`allow_*` keys) and control is on (green **CONTROL ON** on the DMC), current limit and Mode set from Venus. Disconnect removes the device from Venus again.

## Standalone emulator (com0com)

For testing other programs against the emulator, use a virtual serial port pair (for example com0com COM7 <-> COM21):

```bat
python mk2_emu.py COM21
```

Then connect the other program (or `python mk2_panel.py --direct COM7`) to COM7. `emu_ctrl.json` in the project
folder can change the LED word and RAM values while it runs, e.g. `{"led": "0x8040", "ram": {"4": 2650}}`.
