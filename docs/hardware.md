# Hardware

## What was tested

Everything in this project was tested on **one** system:

| Part | Model |
|---|---|
| Inverter/charger | Victron Phoenix MultiPlus 24/3000/70-30, **120 V / 60 Hz**, pre-VE.Bus (VE.9bit) |
| Device firmware | **1842144** (product 0x1842, version 144) |
| Interface | Victron Interface **MK2.2b** – VE.Bus to RS232, product code **ASS030120210** |
| PC | Windows, built-in COM port (COM1), and FTDI US232R USB-RS232 adapter |
| GX | Venus GX, Venus OS v3.85, FTDI US232R USB-RS232 adapter |
| Battery | 24 V LiFePO4 |

Other MultiPlus/Phoenix models, firmware versions, 230 V units and other interfaces are **untested**. The
230 V device profile is present but rejected on purpose until its limits are defined and tested.

## The MK2.2b interface

- PC side: RS232, DB9 female (a normal COM port)
- Device side: RJ45 (the VE.Bus / VE.9bit port of the MultiPlus)
- Protocols: VE.Bus and the older **VE.9bit**. This project needs the VE.9bit mode, which the MK2.2b handles itself.
- The MK2 is powered from the serial port (DTR) and from the device. The program sets DTR.

The newer **MK3-USB does not support VE.9bit** devices and cannot be used here.

## Connection options

### A. PC with an RS232 port

```
MultiPlus  --RJ45 cable--  MK2.2b  --DB9--  PC COM port
```

Run PhoenixConfig on the PC. Optionally publish to Venus over the LAN ([mqtt-bridge.md](mqtt-bridge.md)).

### B. PC with a USB-RS232 adapter

```
MultiPlus  --RJ45--  MK2.2b  --DB9--  USB-RS232 adapter  --USB--  PC
```

- Use a real RS232 level adapter (not a TTL/3.3 V "USB serial" board).
- FTDI-based adapters (e.g. FTDI US232R) are recommended and tested. Windows may need the FTDI driver.
- Prolific PL2303 adapters are not tested.

### C. Directly on the Venus GX (no PC)

```
MultiPlus  --RJ45--  MK2.2b  --DB9--  USB-RS232 adapter  --USB--  Venus GX
```

The GX has no RS232 port, so a USB-RS232 adapter is **required**. Venus OS must be told to leave the adapter alone,
otherwise its serial-starter probes the port with other drivers. See [venus-gx-service.md](venus-gx-service.md).

Victron's own `mk2-dbus` on Venus does not support pre-VE.Bus units; the GX service in this project replaces it
for this device.

## One host at a time

The MK2 can talk to only one program. Close PhoenixConfig before starting the GX service or the official
configuration software, and the other way round.

## Without hardware

- **No MultiPlus and no MK2:** use the built-in emulator ([emulator.md](emulator.md)). PhoenixConfig works fully.
- **MQTT test without the MultiPlus:** the emulator can publish to a real Venus as a separate device. You only need
  a Venus OS device with dbus-mqtt-devices installed.
