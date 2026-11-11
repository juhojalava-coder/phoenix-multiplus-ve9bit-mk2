# GX service: running on the Venus GX (no PC)

`mk2_gx.py` is the bridge without a user interface. It runs as a daemontools service on Venus OS, with the MK2
plugged into the GX through a USB-RS232 adapter. Venus shows and (optionally) controls the MultiPlus without a PC.

```
MultiPlus -- MK2.2b -- USB-RS232 adapter -- USB -- Venus GX
                                                    ├─ mk2gx service (this project)  ─MQTT localhost─┐
                                                    └─ dbus-mqtt-devices  <───────────────────────────┘
```

Tested on: Venus GX, Venus OS **v3.85**, FTDI US232R adapter. Everything is installed under `/data`, which survives
firmware updates; the root file system is not modified permanently.

## Scripted install (best effort)

`venus/install-driver.sh` and `venus/install.sh` automate the steps below. They are written from the manual install
on the test system but **have not been run on a fresh GX**. Read them before use and check the result with
[step 6](#6-check). You need root SSH access (step 0).

```sh
# 1. on the PC: prepare dbus-mqtt-devices-0.9.0 with PyYAML (step 1, points 1-2), then copy both folders
scp -r dbus-mqtt-devices-0.9.0 phoenix-multiplus-ve9bit-mk2 root@VENUS_IP:/data/tmp/

# 2. on the Venus
ssh root@VENUS_IP
cd /data/tmp/phoenix-multiplus-ve9bit-mk2
sh venus/install-driver.sh /data/tmp/dbus-mqtt-devices-0.9.0
sh venus/install.sh                      # plug in the USB-RS232 adapter first
```

`install.sh` options:

| Option | Meaning |
|---|---|
| `--serial SERIAL` | adapter serial number, needed only if more than one USB serial port is connected |
| `--name "TEXT"` | device name on Venus |
| `--allow-limit` | allow the input current limit from Venus/VRM (off by default) |
| `--allow-mode` | allow Mode from Venus/VRM (**switches load and mains**, off by default) |
| `--force-config` | overwrite an existing `/data/mk2gx/mk2gx.json` |

**Default: monitor only.** Without `--allow-limit` / `--allow-mode` all remote control is off and the service sends
no current limit or switch command (`"control": {"enabled": false}`). Either option switches control on. The old
option `--no-limit-control` is still accepted and does nothing (it is the default now).

The scripts never send anything to the MultiPlus. With control on, the switch is not sent at the first start
without a state file (see [step 4](#4-configuration)). Re-running `install.sh` upgrades the program files and keeps
`mk2gx.json`; it then checks that the kept file is valid for the new version. **Upgrading an older install** that
allows remote control: add `"control": {"enabled": true}` to `mk2gx.json` (remote control now requires it), or
re-run with `--force-config` and the options you want. The script stops with this hint instead of starting the
service with invalid settings.

MQTT on the GX is unencrypted: keep the GX on a trusted, closed local network and do not expose port 1883.
Remove with `sh /data/mk2gx/uninstall.sh` (add `--driver` to remove dbus-mqtt-devices too).

## Manual install

The steps used on the test system. Read each command before running it.

### 0. Prerequisites

On the Venus:

1. **Settings > General > Access level**: set a root password and enable **SSH on LAN** (Venus OS v3: security
   profile).
2. **Settings > Services > MQTT on LAN**: enabled.
3. Venus OS v3.85 provides `python3`, `pyserial` and `paho-mqtt`, which is all the service needs. Check with:
   ```sh
   python3 -c "import serial, paho.mqtt; print('ok')"
   ```

On the PC: `scp`/`ssh` (included in Windows 10/11) and this project folder.

### 1. Install dbus-mqtt-devices

[dbus-mqtt-devices](https://github.com/freakent/dbus-mqtt-devices) (a third-party driver, not part of this project)
creates the Venus `vebus` service from MQTT messages. Tested with **v0.9.0**. Its own `setup.sh` was **not** used:
it installs packages with pip/opkg on every boot and fails on Python 3.12. Instead:

1. On the PC, download the v0.9.0 release, extract it to `dbus-mqtt-devices-0.9.0\`.
2. Put the pure-Python PyYAML into the driver folder (Venus has no `python3-pyyaml` package):
   ```bat
   pip download PyYAML==6.0.2 --no-binary :all: --no-deps -d yaml_src
   ```
   Extract the archive and copy its `lib/yaml` folder to `dbus-mqtt-devices-0.9.0\ext\pylib\yaml\`.
3. Edit `dbus-mqtt-devices-0.9.0\services.yml`, `vebus` section: change the two `CurrentLimit` defaults from `50` to
   `50.0` and add `format: "{} A"`. Without the `.0` the Venus settings store rejects decimal limits.
4. Copy to the Venus and set it up:
   ```sh
   scp -r dbus-mqtt-devices-0.9.0 root@VENUS_IP:/data/drivers/
   ssh root@VENUS_IP
   BASE=/data/drivers/dbus-mqtt-devices-0.9.0
   chmod +x $BASE/dbus_mqtt_devices.py $BASE/bin/service/run $BASE/bin/service/log/run
   ln -sfn /opt/victronenergy/dbus-digitalinputs/ext/velib_python $BASE/ext/velib_python
   ```
5. Make sure the service `run` script uses the bundled PyYAML. `$BASE/bin/service/run` must contain:
   ```sh
   PYTHONPATH=$BASE/ext/pylib HOME=$BASE exec $BASE/dbus_mqtt_devices.py
   ```
6. Start it at boot through `/data/rc.local` (create the file if it does not exist, otherwise add the line):
   ```sh
   #!/bin/sh
   ln -sfn /data/drivers/dbus-mqtt-devices-0.9.0/bin/service /service/dbus-mqtt-devices
   ```
   ```sh
   chmod +x /data/rc.local && /data/rc.local
   sleep 10; svstat /service/dbus-mqtt-devices; tail /var/log/dbus-mqtt-devices/current
   ```

This step is also needed for the PC bridge ([mqtt-bridge.md](mqtt-bridge.md)).

### 2. Copy the service files

From the project folder on the PC:

```sh
ssh root@VENUS_IP "mkdir -p /data/mk2gx/service/log /data/mk2gx/udev"
scp mk2_gx.py mk2_link.py mk2_decode.py mk2_mqtt.py mk2_config.py mk2_state.py knob_cal.py ^
    knob_calibration.json root@VENUS_IP:/data/mk2gx/
```

(`^` continues the line in `cmd.exe`; in PowerShell use a backtick or write it on one line.)

### 3. Find the adapter and stop Venus from probing it

Plug the USB-RS232 adapter into the GX and find its stable name and serial number:

```sh
ls -l /dev/serial/by-id/
udevadm info -q property /dev/ttyUSB0 | grep -E "ID_SERIAL_SHORT|ID_BUS"
```

Venus OS's serial-starter tries every new USB serial port with its own drivers (VE.Direct, GPS, ...). Their probe
traffic disturbs the MK2, so the adapter must be marked as ignored. Create
`/data/mk2gx/udev/zz-mk2gx-ignore.rules` (replace `YOUR_SERIAL` with `ID_SERIAL_SHORT`):

```
ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_SERIAL_SHORT}=="YOUR_SERIAL", ENV{VE_SERVICE}="ignore"
```

### 4. Configuration

Venus OS has no TOML reader, so the service uses the same settings as JSON. Create `/data/mk2gx/mk2gx.json`:

```json
{
  "serial": {"port": "/dev/serial/by-id/usb-FTDI_US232R_YOUR_SERIAL-if00-port0"},
  "device": {"nominal_ac_voltage": 120, "max_charge_current_a": 45},
  "battery": {"max_absorption_v": 28.6, "max_float_v": 27.6},
  "knob": {"min_a": 2.0, "max_a": 15.9, "repeat_s": 10},
  "state": {"path": "/data/mk2gx/state.json"},
  "control": {"enabled": false},
  "mqtt": {"host": "localhost", "port": 1883, "client_id": "mk2gx"},
  "venus": {"label": "mp", "custom_name": "MultiPlus 24/3000/70-30 120V (GX)",
            "allow_current_limit_control": false, "allow_mode_control": false},
  "alarms": {"HighDcVoltage": {"source": "udc", "warn": 28.8, "alarm": 29.2, "hyst": 0.2, "delay_s": 5}}
}
```

MQTT to `localhost` on the GX needs no password. All keys are described in [configuration.md](configuration.md).

This example is **monitor only**: the service shows the device on Venus and sends nothing to it. For control from
Venus/VRM set `"control": {"enabled": true}` and the wanted `allow_*` key(s) to `true` (an `allow_*` key without
control is rejected at start-up).

**Switch state at start (control on):** `state.json` holds the last knob value and switch state and is restored at
every start. In monitor only mode nothing is restored or sent. If the file is missing, the switch is not sent and
the device keeps its state. When moving the MK2 from a PC to the
GX, copy the values from the PC's `mk2_panel_state.json` (`knob`, `switch`) so the switch does not change:

```sh
echo '{"knob": 29, "switch": 3}' > /data/mk2gx/state.json
```

### 5. Service and boot

```sh
cat > /data/mk2gx/service/run <<'EOF'
#!/bin/sh
exec 2>&1
exec python3 -u /data/mk2gx/mk2_gx.py --config /data/mk2gx/mk2gx.json
EOF
cat > /data/mk2gx/service/log/run <<'EOF'
#!/bin/sh
exec multilog t s25000 n4 /var/log/mk2gx
EOF
chmod +x /data/mk2gx/service/run /data/mk2gx/service/log/run
```

Add this block to `/data/rc.local` (after the dbus-mqtt-devices line). It restores the udev rule after firmware
updates and starts the service:

```sh
# >>> mk2gx
R=zz-mk2gx-ignore.rules
if ! cmp -s /data/mk2gx/udev/$R /etc/udev/rules.d/$R; then
  cp /data/mk2gx/udev/$R /etc/udev/rules.d/$R
  udevadm control --reload
fi
udevadm trigger --action=add --sysname-match='ttyUSB*'
ln -sfn /data/mk2gx/service /service/mk2gx
# <<< mk2gx
```

Run it once now: `/data/rc.local`.

### 6. Check

```sh
svstat /service/mk2gx
tail -f /var/log/mk2gx/current | tai64nlocal
```

The log should show the connection, `monitor only` or the restored knob/switch, and the registration. The device appears in
**Settings > Devices** within about 10 s. Reboot the GX once and check again.

If the adapter is unplugged, the service restarts every few seconds until it is back (harmless). If the MK2 stops
answering for 60 s, the service exits and daemontools restarts it.

## Operation

| Task | Command |
|---|---|
| Stop | `svc -d /service/mk2gx` |
| Start | `svc -u /service/mk2gx` |
| Restart (e.g. after editing `mk2gx.json`) | `svc -t /service/mk2gx` |
| Log | `tail -n 50 /var/log/mk2gx/current \| tai64nlocal` |

To use PhoenixConfig on a PC instead: `svc -d /service/mk2gx`, move the adapter to the PC, and copy the knob/switch
from `/data/mk2gx/state.json` to the PC's `mk2_panel_state.json`.

## Uninstall

```sh
svc -d /service/mk2gx /service/mk2gx/log
rm /service/mk2gx
sed -i '/# >>> mk2gx/,/# <<< mk2gx/d' /data/rc.local
rm -f /etc/udev/rules.d/zz-mk2gx-ignore.rules
rm -r /data/mk2gx
```

dbus-mqtt-devices: `rm /service/dbus-mqtt-devices; rm -r /data/drivers/dbus-mqtt-devices-0.9.0` and remove its line
from `/data/rc.local`.
