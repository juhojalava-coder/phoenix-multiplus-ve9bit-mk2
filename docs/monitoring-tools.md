# Communication monitoring tools

These tools are the **reverse-engineering toolbox** of the project: they were used to observe the serial
communication between a configuration program and the MK2, and to analyse the recordings
([methodology.md](methodology.md)). You do not need them to use PhoenixConfig, the MQTT bridge or the GX service.
They are included so that findings can be repeated and checked, for example on other firmware versions.

## Recording the traffic

The relay sits between a program that talks to the MK2 and the real MK2, and records everything.

```
configuration program -> COM7 ==com0com== COM21 -> mk2_proxy.py -> real COM port (e.g. COM1) -> MK2 -> device
```

1. Create a virtual serial port pair with [com0com](https://com0com.sourceforge.net/) (Windows), for example
   COM7 <-> COM21. Some programs do not list ports from certain com0com classes; if a port is not offered,
   rename the pair to a "Ports (COM & LPT)" class port.
2. Close every program that uses the real port.
3. Start the relay: `python mk2_proxy.py COM21 COM1` (virtual port end, real port, optional baud rate).
4. Select COM7 in the program you want to observe.

`mk2_proxy.py` forwards every byte unchanged and writes `capture_YYYYmmdd_HHMMSS.log` with the lines
`time  direction  hex`, where `TX` is PC to MK2 and `RX` is MK2 to PC. Frames are grouped by a silent gap.

**It does not filter or block anything**: the observed program can change settings and reset the device as usual.

## Looking at a recording

| Tool | Use |
|---|---|
| `python mk2_panel.py --log capture_X.log` | PhoenixConfig in passive mode: live measurements, LEDs and switch while another program talks to the device. Sends nothing |
| `python mk2_watch.py capture_X.log` | follows a log in real time and prints changes of the RAM values, the LED word, the switch and S frames |
| `python led_changes.py capture_X.log` | lists the changes of the LED word with Udc/Idc/IMains at that moment (to find which bit is which lamp) |
| `python timeline.py capture_X.log` | timeline of switch commands, LED changes and UOut/ripple changes |
| `python switch_timeline.py capture_X.log [state] [n]` | timeline around a switch change: switch/knob commands, LEDs and measurements |
| `python writes_since.py capture_X.log [T]` | lists the settings writes (0x33 + 0x34) and resets after time T |
| `python compare_settings.py original.log new.log` | final state of the settings after the writes of the second log vs. the first |
| `python calibrate_knob.py capture_X.log ...` | derives `knob_calibration.json` (knob value to real mains current) from logs |

`mk2_decode.py` is the library behind them: it parses the logs (frames, Winmon commands, RAM scales, settings,
writes, resets) and holds the names of the settings and flags.

## Probing the device directly

These talk to the MK2 through a real serial port, so close PhoenixConfig and every other program first.

| Tool | Use |
|---|---|
| `python mk2_probe.py COM1 V L F0 A0` | sends **read-only** MK2 commands (a letter plus optional hex bytes) and decodes the replies. Other commands are refused |
| `python read_only.py [COM1] [seconds]` | connects like the panel and prints the measurements once a second. Sends no knob, switch or settings |
| `python l_probe.py COM1 [rounds]` | compares the MK2 `L` (LED status) command with the Winmon LED word. Sends only V, R, S (no-send), version, L and 0x02 |
| `python link_quality.py PORT [seconds]` | measures the quality of the link on a port: response rate and times, dropped bytes, plausibility. Read-only; useful for comparing a motherboard COM port with a USB-RS232 adapter |

## Finding which signals depend on something you do: `mk2_survey.py`

A read-only recorder for experiments such as "what changes when I turn the physical front switch of the
MultiPlus (on / charger only / off)?". It polls everything that can be read, keeps running while the device does not
answer, reconnects by itself, and lets you label phases while it runs.

```bat
python mk2_survey.py COM1 1800 on        (port, seconds (optional), name of the first phase (optional))
```

It sends **nothing that changes the device**: no switch, no knob, no settings. Only the whitelisted read commands
`V`, `L`, `F 05` and Winmon 0x02, 0x30 and 0x31 are used (the reconnect repeats the normal connection sequence
V, R, S). Close PhoenixConfig and every other program that uses the port first.

**Procedure for the front switch** (the rocker always passes through "off", so the device will stop answering and
the survey will report the link as DOWN and reconnect when it comes back; that is expected and informative):

1. Start the survey with the switch in its normal position, wait for a minute (phase `on`).
2. Type `charger_only` + Enter, then turn the switch. The settings are read once at the label.
3. Wait about a minute. Then label the next position (for example `off`) and turn the switch; and so on.
4. Return to the starting position, label it again (for example `on_again`) and wait a minute.
5. Type `q` + Enter. The summary is printed and written to a file.

**Mind the load.** "Off" switches the output off, and in "charger only" the load is on the mains only while the mains
is present. Do the experiment when the load can be without power for a few minutes. Do not write settings or reset
the device during the experiment. If the device boots into a different state after "off", the survey records it,
because it sends no switch or knob value of its own.

**Reading the result** (`survey_<time>_summary.txt`):

- Per phase: which values each signal had. Signals marked `DIFF` differ between phases (measurements are compared
  as ranges, so ordinary load changes show up too: judge them with the situation in mind).
- Interesting: the LED word (`led`), the replies of `L` and `F 05` (`probe_*`), RAM IDs 10-63 that start to answer
  (`extra*`), settings that change (`set*`), and the first samples after a reconnect.
- `survey_<time>.jsonl` has every sample with time and phase. `capture_direct_<time>.log` has the raw traffic,
  including frames the device sends on its own; use `led_changes.py`, `timeline.py` and `mk2_decode.py` on it.

## Emulator control file

When the emulator runs, `emu_ctrl.json` in the program folder is read every 0.5 s and can change the LED word and the
RAM values while it runs (see the header of `mk2_emu.py`), for example
`{"led": "0x8040", "ram": {"4": 2650}}`.
