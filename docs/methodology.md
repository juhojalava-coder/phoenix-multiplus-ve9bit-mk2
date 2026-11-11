# Methodology

The communication of this old firmware (VE.9bit) is only partly documented. Victron's public MK2 protocol document
covers the VE.Bus side and a few generic commands; the settings and many details of the pre-VE.Bus devices are not
described. This project was therefore built by observation and careful testing.

## 1. Monitoring

A virtual serial port pair (com0com on Windows: for example COM7 <-> COM21) was placed between the official
configuration software and the real MK2:

```
configuration software -> COM7 ==com0com== COM21 -> mk2_proxy.py -> real COM port -> MK2 -> MultiPlus
```

`mk2_proxy.py` forwards every byte unchanged and writes a timestamped log (`capture_*.log`, direction `TX` =
PC to MK2, `RX` = MK2 to PC). `mk2_decode.py` parses these logs (frames, Winmon commands, settings, scales), and
small scripts (`mk2_watch.py`, `compare_settings.py`, `led_changes.py`, `timeline.py`, `switch_timeline.py`,
`writes_since.py`) answer questions such as "which bytes change when I click this", "what exactly was written" or
"which LED bit belongs to which lamp".

## 2. The emulator

`mk2_emu.py` answers like the device, with the replies taken from a captured log. It served two purposes:

- A safe target for the monitoring experiments (which display rules the official software applies, for example the
  frequency display threshold or the LED bit map) without touching the real unit.
- Development of the control program: PhoenixConfig, the MQTT bridge and the tests run against it. It is now part of
  the project so everybody can try the software without hardware ([emulator.md](emulator.md)).

## 3. Verification on the real device

Findings were marked **[verified]** only after a test on the real device with measurements (clamp meter, the
display of the device, Venus readings): switch states, knob limit, charge current changes, settings that take effect
without a reset. Findings that were only reasoned stay **[inferred]** ([protocol.md](protocol.md)).

## 4. Safety measures that came from this process

- A whitelist of allowed MK2 and Winmon commands; settings can be written only through a validated path.
- Flag bits that are not understood (Flags0 bits 9, 10, 12, 15) are never changed.
- Hard limits for settings that could harm the system, narrowed (never widened) by the configuration file.
- Switch and knob are RAM-level and re-sent; EEPROM is written only by explicit settings writes with a read-back.

## 5. Limits of the findings

Everything was tested on a single unit (see [hardware.md](hardware.md)). Other models and firmware versions may
behave differently.
