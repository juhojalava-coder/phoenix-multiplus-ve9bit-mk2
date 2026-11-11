# PhoenixConfig (desktop app)

`mk2_panel.py` is the main tool: monitoring, Digital Multi Control and settings for the MultiPlus.

## Install

1. Install Python 3.11 or newer (tested with 3.14 on Windows; Tk is included in the Windows installer).
2. In the project folder:
   ```bat
   pip install -r requirements.txt
   copy mk2.example.toml mk2.toml
   ```
3. Edit `mk2.toml` if needed (charge current limit, battery voltage warnings, Venus address). See
   [configuration.md](configuration.md). The program works without `mk2.toml` too, using safe defaults.

## Start

```bat
python mk2_panel.py
```

On Windows use `python` (or `py -3`), not `python3`; or double-click `PhoenixConfig.bat`.

The window opens **without a connection**. In the top right of the settings area:

- **Port**: serial ports are listed, FTDI adapters first. `emulator` is always last.
- **Refresh**: list the ports again (e.g. after plugging in a USB adapter).
- **Connect / Disconnect**: opens or closes the MK2 connection. The last used port is remembered.
- **Publish to Venus (MQTT)**: also show the device on a Venus GX ([mqtt-bridge.md](mqtt-bridge.md)).
- **⚙**: MQTT settings (Venus address, port, username, password).

Command line options (optional):

```
python mk2_panel.py --direct COM1        connect at start-up (with control on, also restores the last switch state)
python mk2_panel.py --direct emulator    start with the built-in emulator
python mk2_panel.py --mqtt               tick "Publish to Venus" at start-up
python mk2_panel.py --config other.toml  use another settings file
python mk2_panel.py --debug              show developer numbers (raw values, LED word, poll cycle)
python mk2_panel.py --log capture.log    communication monitoring: show a capture log written by mk2_proxy.py
python mk2_panel.py --allow-unknown-firmware   RISKY: allow writing and control on unverified firmware
```

## Monitor only and control

**Connect is monitor only by default.** The program reads and shows the measurements and can read the settings,
but sends nothing that changes the device: no input current limit (knob) and no switch command. The top bar shows
`monitor only` or `control ON`.

- The state button at the top right of the **Digital Multi Control** shows the mode: yellow **Monitor only - click
  to enable control**, green **CONTROL ON - click for monitor only**. Clicking a switch button or the knob while
  monitor only shows a tip next to the cursor and nothing is sent.
- **Enabling control** (after Connect) asks for confirmation the first time only. The input current limiter
  continues from where you left it; the first time it is the **conservative minimum 2.0 A** (`[knob] min_a`), so
  charging never starts with an unknown limit; raise it yourself with the knob. The switch is not changed until
  you click it, and a switch command is never sent before the current limit. While control is on, the last switch
  and knob values are repeated every 10 s (RAM level), so they come back after a device reset or power cut.
- **The choice is remembered per port** (`mk2_panel_state.json`): after you have enabled control on a port, the
  next Connect on the same port starts with control on and that port's last current limit, without asking.
  Another port (or the emulator) does not inherit the permission or the limit, and asks again. Clicking the green
  button returns to monitor only, and that is remembered too (the device keeps the last limit until its next
  reset).
- `mk2.toml` `[control] enabled = true` switches control on at every Connect. Then the saved input current limit is
  restored on connect (the device does not report it; 2.0 A if none is saved), and with `--direct` the saved
  switch state as well.
- **Connect never changes the switch state** unless control is on and `--direct` is used. The device does not
  report its switch position, so the program shows the most likely position (dashed outline) until you click a
  switch button.
- Venus/VRM remote control ([mqtt-bridge.md](mqtt-bridge.md)) works only while control is on.

**Unverified firmware is read-only.** Writing, reset and control are allowed only on the firmware this program was
verified with (1842144, [hardware.md](hardware.md)). On other firmware the top bar shows `READ-ONLY: unverified
firmware`, the write buttons stay disabled and the DMC button shows **READ-ONLY** (control cannot be enabled). Reading the settings and saving
them to a file still work. `--allow-unknown-firmware` lifts the lock at your own risk.

## Window

- **Left cards**: AC output, AC input and battery measurements (about 1.6 s update). Battery current `+` (green) is
  charging.
- **Settings tabs** (General, Grid, Inverter, Charger, Virtual switch, All settings): press **Read settings**
  first (about 45–50 s). Edit values in the tabs; an orange bar shows the number of unsaved changes.
- **Write changes...**: preview, validate and write the changes, then read them back. Some settings need a device
  reset to take effect; the program asks when needed. A reset briefly interrupts the output.
  **If a write fails** (no acknowledgement, the read-back differs, or the connection is lost, e.g. the USB adapter
  is unplugged), writing stops at that setting: the remaining settings are not written and the program lists what
  was written, what failed and what was not written. The top bar shows `SETTINGS PARTLY WRITTEN` in red and **no
  reset is offered**. Further writes are refused until you **Read settings** again; then check them and write the
  remaining changes.
- **Raw editor...**: all writable settings and flag bits as raw values.
- **Save settings to file... / Load settings from file...**: a JSON backup of all settings. Loading only works on
  the same firmware and goes through the same preview and write steps. The **All settings** tab can also export a
  CSV report.
- **Digital Multi Control** (bottom): the LEDs of the front panel, the switch (charger only, off, on, and inverter
  only) and the input current limiter knob (drag or mouse wheel). The text under the knob shows the estimated real
  grid current limit. **toggle layout** switches between the physical panel layout and the classic layout.

## Files the program writes

| File | Content |
|---|---|
| `mk2_panel_state.json` | last switch, per port the monitor only / control choice and the last knob, port, window size, DMC layout, MQTT address and (only if "Remember" is ticked) the MQTT password in plain text. Keep the file private; anyone who can read it can log in to the Venus MQTT broker |
| `capture_direct_*.log` | all MK2 traffic of a session (for troubleshooting) |

## Typical first session

1. Connect, wait until the measurements appear.
2. **Read settings**, then **Save settings to file...** to keep a backup of the original settings.
3. Change what you need, **Write changes...**, check the preview, confirm.
