# MK2 / VE.9bit protocol notes

What was found about talking to a **pre-VE.Bus Phoenix MultiPlus (VE.9bit)** through an **MK2.2b** interface.
Everything was worked out by monitoring the traffic of the official configuration software and by testing against
the emulator and one real device ([methodology.md](methodology.md)).

Labels: **[verified]** = confirmed on the real device. **[inferred]** = reasoned, not confirmed directly.
Data is from one unit: MultiPlus 24/3000/70-30 120 V, device firmware **1842144** (Winmon 0x05 = 0x1BE0,
0x06 = 0x001C), MK2 firmware 1130136, 2400 baud 8N1. The MK2 is powered from DTR.

## 1. Frame format

```
[len] [FF] [command ...] [checksum]        the sum of all bytes is 0 (mod 256)
```

- Winmon commands are sent with the letters X, Y, Z (0x58-0x5A) in rotation. The reply uses the same letter.
- Replies are triplets `[code lo hi]`; each triplet arrives as its own frame about 50 ms apart. The device answers
  after about 150-180 ms.
- The MK2 sends a version frame on its own when the line is idle (`07 FF 56 98 3E 11 00 57 66`).
- The device reset frame `06 21 00 00 00 00 03 C4` is **not** MK2 framed (no 0xFF byte, no MK2 checksum).

## 2. Commands

### MK2 level

| Command | Bytes | Meaning |
|---|---|---|
| V | `02 FF 56 A9` | version, answered with a V frame |
| R | `02 FF 52 AD` | MK2 reset, answered with a V frame |
| S (connect) | `S 04 00 FF 01 10 00 04` | NO-SEND flag (0x10), used when opening a session |
| S (reset) | `S 04 00 FF 01 50 00 04` | flags 0x50, sent before the reset frame. Acknowledged with `02 FF 53 AC` |
| F 05 | | MasterMultiLED: **this device does not answer** |
| L | | LED status: returns only zeros, not useful |

### Winmon

| Command | Arguments | Reply | Meaning |
|---|---|---|---|
| 0x02 | none | 0x81 lo hi | LED word (16 bits) |
| 0x03 | 1 / 2 / 3 / 4 | none | switch: 1 charger only, **2 inverter only**, 3 on, 4 off **[all verified]** |
| 0x04 | value | none | input current limiter knob, value = amps x 10 **[verified]** |
| 0x05 / 0x06 | none | 0x82 / 0x83 | firmware version (low / high word) |
| 0x30 | RAM id | 0x85 | ReadRAMVar |
| 0x31 | id lo hi | 0x86 (0x91 = not supported) | ReadSetting |
| 0x33 | id lo hi | none | WriteSetting: select the target |
| 0x34 | data lo hi | 0x88 | WriteData. The data in the reply is garbage |
| 0x35 | id lo hi | 0x89..0x8D | GetSettingInfo: scale, offset, default, min, max |
| 0x36 | RAM id | 0x8E, 0x8F | GetRAMVarInfo: scale, offset |
| 0x44 FE, 0x3B | | none | sent by the official software when reading settings; meaning unknown |

`mk2_link.py` sends only these commands (whitelist). 0x33/0x34 are possible only through `write_settings()`, and
the device reset only through `reset()`.

## 3. Measurements (RAM variables)

Polling order of the official software: 9, 2, 1, 0, 8, 5, 7, 4, 6. One round takes about 1.6 s.

| ID | Name | Scale | Notes |
|---|---|---|---|
| 0 | UMains | 1/100 V | |
| 1 | IMains | 1/100 A | |
| 2 | UOut | 1/100 V | |
| 4 | Udc | 1/100 V | |
| 5 | Idc | signed | **positive = charging** [verified with a clamp meter, about 20 A] |
| 6 | Udc ripple | 1/100 V | |
| 7 | InverterPeriod | Sc 0x7857, offset 256 | Hz = 1961 / ((raw + 256) x 0.1) [verified at 50 and 60 Hz] |
| 8 | MainsPeriod | like 7 | |
| 9 | IOut | signed | |

RAM 3 is supported but unused. IDs 10-37 are not supported.

Display rules that match the official software [verified with the emulator]: negative IOut is shown as 0.0;
mains frequency is shown as "---" when raw = 0 or UMains < 60.00 V.

## 4. Settings (IDs 0-48)

Display value = scale x (raw + offset). Scales and limits come from command 0x35. IDs 49 and up are not supported.

| ID | Name | Unit / scale |
|---|---|---|
| 0, 1 | Flags0, Flags1 | bits, see section 5 |
| 2 / 3 | UBatAbsorption / UBatFloat | 0.01 V |
| 4 | IBatBulk | 1 A (charge current) |
| 5 | UInvSetpoint | 1 V (94-128) |
| 6 | IMainsLimit | 0.2 A (2-30) |
| 7 | RepeatedAbsorptionTime | 15 min |
| 8 | RepeatedAbsorptionInterval | 360 min |
| 9 | MaxAbsorptionDuration | 60 min |
| 10 | ChargeCharacteristic | 1 Fixed, 2 Adaptive, 3 Adaptive + BatterySafe |
| 11 / 12 | UBatLowLimit / hysteresis | 0.01 V; restart level = 11 + 12 |
| 13 | NumberOfSlaves | 0-4 |
| 14 | ThreePhaseMode | 0 three phase, 1 split 180, 2 two leg 120 |
| 15 | vsUsage | 0 unused, 1 relay, 2 ignore AC |
| 16, 28 | virtual switch load limits | 0.01 A |
| 17, 18, 29, 30 | virtual switch Udc limits | 0.01 V |
| 19-27, 31-35, 38-42 | virtual switch delays | seconds = raw - 1 (raw 0 = off) |
| 36, 37 | virtual switch delays | minutes = raw - 1 |
| 43 | vsMinimumOnTime | min |
| 44 / 45 | AC low disconnect / hysteresis | 1 V; connect level = 44 + 45 |
| 46 / 47 | AC high disconnect / hysteresis | 1 V, offset +100; connect level = 46 - 47 |
| 48 | AssistCurrentBoost | 0.1 |

"Battery type" is not a device setting: it is a preset combination of curve, absorption and float voltage and times.

## 5. Flags

**Flags0** (original value 0x01A4):

| Bit | Meaning |
|---|---|
| 0 | MultiPhase |
| 1 | Leader |
| 2 | 60 Hz |
| 3 | DisableWaveCheck (UPS function, **inverted**) |
| 4 | DoNotStopAfter10HrBulk (inverted) |
| 5 | AssistEnabled (PowerAssist) |
| 6 | DisableCharge (inverted: "enable charger") |
| 7 | always = NOT bit 3 (change both together) |
| 8 | DisableAES (inverted) |
| 9, 10, 12 | **"Not promoted": never touched** |
| 11 | Storage mode [verified by a write test] |
| 13 | DisableGroundRelay (inverted) |
| 14 | WeakACInput |
| 15 | unknown, always preserved |

**Flags1** (mask 0x1FFF, original value 0x0800): bit 16 virtual switch on at bulk protection; bits 17-20
virtual switch on, pre-alarm (temperature, low battery, overload, Udc ripple), bit 0 = alarm, 1 = pre-alarm;
bits 21-24 virtual switch off, same order; bit 25 on at general failure; bit 26 invert virtual switch;
bit 27 accept wide input frequency; bit 28 dynamic current limiter.

## 6. LED word (command 0x02, reply 0x81)

`word = lo | hi << 8`

| Bit | LED |
|---|---|
| 0 / 1 / 2 | overload / low battery / temperature [emulator] |
| 4 | inverter on [verified] |
| 5 | mains on [verified] |
| 6 / 7 / 8 | bulk / absorption / float [verified] |
| 15 | always 1 |

Bits 3 and 9-14 had no effect on the Digital Multi Control view.

## 7. Switch and current limiter knob

- **Switch (0x03):** the state stays in the device without repeating (tested >= 4 min), also across an MK2 reset.
  Programs repeat it every 10 s. The device does **not** report its switch position.
- **Knob (0x04, value = amps x 10, allowed here 2.0-15.9):** overrides the AC input current limit setting (ID 6).
  The knob scale comes from the old 16 A panel, the device is a 30 A unit; the real limit is non-linear
  (about x1.52 at knob 1.0 up to x1.74-1.86 at 2.8-6.6). The calibration is in `knob_calibration.json`
  (piecewise linear, `knob_cal.py`).
- **The limit applies to the total mains current (load + charge) [verified]:** knob 3.0 (estimate 5.4 A), when the
  load rose to 2.1 A, IMains stayed at 5.5 A and the charger reduced its current. The calibration was made in
  "charger only" mode.
- **The knob value cannot be read from the device.** PhoenixConfig remembers the last value it sent and sends it on
  every connection (RAM level, no EEPROM wear).
- **Connection behaviour of the official software [verified from a capture]:** when it connects it forces switch
  `4` (off) and knob `0x42` (6.6 A); when the remote panel is closed it sends switch `3` (on) and knob `0`, and the
  charger then follows its own limit (IBatBulk). It is meant for configuration, not for running control.
  PhoenixConfig sets the state explicitly instead.
- **Inferring the switch from LEDs** (`mk2_link.infer_switch`), used only for the dashed outline:

  | LEDs | UMains | Candidates |
  |---|---|---|
  | mains on | any | on, charger only |
  | inverter on, no mains | >= 90 V | probably inverter only (on + "ignore AC" looks the same) |
  | inverter on, no mains | < 90 V | on, inverter only |
  | only low battery | any | shut down on low voltage (on, inverter only) |
  | all dark (0x8000) | >= 90 V | off |
  | all dark | < 90 V | off, charger only |

## 8. Writing settings

Sequence used by `mk2_link.write_settings()`:

1. Flags only (IDs 0, 1): 0x35 (info) and 0x31 (fresh value), then read-modify-write: only the bits shown in the UI
   change, bit 7 follows bit 3.
2. `0x33 id_lo id_hi` (no reply), about 0.11 s pause, `0x34 data_lo data_hi`, then reply 0x88.
3. Read-back with 0x31 (added by this project).

Order (hysteresis values last): `0, 1, 6, 13, 14, 44, 46, 5, 11, 48, 10, 2, 3, 4, 7, 8, 9, 15, 16, 18, 17, 23, 22,
21, 20, 24, 25, 26, 27, 19, 28, 30, 29, 34, 35, 32, 33, 31, 36, 39, 40, 41, 42, 37, 38, 43, 45, 47, 12`.
Writing all 49 settings takes about 0.32 s each.

Checks before sending: device min/max; hard limits (ID 5 between 94 and 128 V, ID 44 at most 120 V, ID 46 between
120 and 143 V); a LiFePO4 warning when absorption > 28.6 V or float > 27.6 V (configurable). Settings survive a
reset and power loss (EEPROM).

## 9. Device reset and when it is needed

Sequence: `S 04 00 FF 01 50 00 04` (ack `02 FF 53 AC`), about 0.1 s later the raw `06 21 00 00 00 00 03 C4`, then the
version is read again. The official software asks "reset?" after **every** write.

**Settings confirmed to take effect without a reset on the real device:**

| Setting | Test | Result |
|---|---|---|
| 5 UInvSetpoint | 120 to 117 and back | UOut followed immediately while inverting |
| 4 IBatBulk | 52 to 15 to 20 A | charge current changed immediately |
| 11 UBatLowLimit | 18.6 to 23.0 to 22.0 | pre-alarm followed the new restart level in about 4 s |
| Flags0 bit 6 (enable charger) | on and off while the load was on mains | charging started and stopped at once, no interruption |
| 15 vsUsage, 18 / 21 | changed live | the relay reacted at once |

PhoenixConfig does not ask for a reset when all changed values are in this list; otherwise it lists the untested
ones by name. Others (60 Hz, 2-3 phase, slaves) probably need a reset [inferred, from the protocol document:
"active after reset"].
