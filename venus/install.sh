#!/bin/sh
# Install the GX service (mk2_gx.py) of Phoenix MultiPlus VE.9bit MK2 Link on Venus OS.
# BEST EFFORT: based on the manual install on a Venus GX with Venus OS v3.85 and an FTDI US232R adapter.
#
# Requirements: dbus-mqtt-devices installed (install-driver.sh), MK2.2b + USB-RS232 adapter plugged into the GX.
#
# Copy the project folder to the Venus and run from it:
#   scp -r phoenix-multiplus-ve9bit-mk2 root@VENUS:/data/tmp/
#   ssh root@VENUS
#   cd /data/tmp/phoenix-multiplus-ve9bit-mk2 && sh venus/install.sh
#
# Options:
#   --serial SERIAL     adapter USB serial number (ID_SERIAL_SHORT), if more than one USB serial port exists
#   --name "TEXT"       device name on Venus (default "MultiPlus (VE.9bit MK2)")
#   --allow-limit       allow input current limit control from Venus/VRM. Default: off
#   --allow-mode        allow Mode control from Venus/VRM (switches load and mains!). Default: off
#   --force-config      overwrite an existing /data/mk2gx/mk2gx.json
#
# Default = MONITOR ONLY: all remote control is off and the service sends no current limit or switch command
# ([control] enabled = false). --allow-limit / --allow-mode switch control on ([control] enabled = true).
# The script itself never sends anything to the device. With control on, the switch state is not changed at the
# first start (no state file = switch not sent). Re-running the script upgrades the program files.
# MQTT on the GX is unencrypted (port 1883): keep the GX on a trusted, closed local network.
set -e

DEST=/data/mk2gx
SERIAL=""
NAME="MultiPlus (VE.9bit MK2)"
MODE=false
LIMIT=false
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --serial) SERIAL="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --allow-limit) LIMIT=true; shift ;;
        --allow-mode) MODE=true; shift ;;
        --no-limit-control) LIMIT=false; shift ;;   # old option, now the default
        --force-config) FORCE=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
CONTROL=false
if [ "$LIMIT" = true ] || [ "$MODE" = true ]; then
    CONTROL=true
fi

die() { echo "ERROR: $*" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")/.." && pwd)

# ---------------------------------------------------------------- checks
[ "$(id -u)" = 0 ] || die "run as root"
[ -d /opt/victronenergy ] || die "this is not Venus OS"
for f in mk2_gx.py mk2_link.py mk2_decode.py mk2_mqtt.py mk2_config.py mk2_state.py knob_cal.py knob_calibration.json; do
    [ -f "$HERE/$f" ] || die "$f not found in $HERE (run from the project folder)"
done
python3 -c "import serial, paho.mqtt.client" 2>/dev/null || die "python3 with pyserial and paho-mqtt is required"
if [ ! -e /service/dbus-mqtt-devices ]; then
    echo "WARNING: dbus-mqtt-devices is not installed (venus/install-driver.sh). The device will not appear on Venus."
fi

# ---------------------------------------------------------------- find the adapter
# candidates: USB serial ports with a serial number
CANDS=""
for tty in /dev/ttyUSB*; do
    [ -e "$tty" ] || continue
    props=$(udevadm info -q property -n "$tty" 2>/dev/null) || continue
    bus=$(echo "$props" | sed -n 's/^ID_BUS=//p')
    sn=$(echo "$props" | sed -n 's/^ID_SERIAL_SHORT=//p')
    model=$(echo "$props" | sed -n 's/^ID_MODEL=//p')
    [ "$bus" = usb ] && [ -n "$sn" ] || continue
    echo "found $tty: serial $sn, $model"
    if [ -z "$SERIAL" ] || [ "$SERIAL" = "$sn" ]; then
        CANDS="$CANDS $tty:$sn"
    fi
done
set -- $CANDS
if [ $# -eq 0 ]; then
    die "no USB serial adapter found${SERIAL:+ with serial $SERIAL}. Plug in the USB-RS232 adapter."
elif [ $# -gt 1 ]; then
    die "more than one USB serial port: choose one with --serial SERIAL (see the list above)"
fi
TTY=${1%%:*}
SERIAL=${1#*:}
BYID=""
for l in /dev/serial/by-id/*; do
    [ -e "$l" ] || continue
    [ "$(readlink -f "$l")" = "$(readlink -f "$TTY")" ] && BYID="$l"
done
[ -n "$BYID" ] || die "no /dev/serial/by-id link for $TTY"
echo "== adapter: $TTY  serial $SERIAL  ($BYID)"

# ---------------------------------------------------------------- program files
if [ -e /service/mk2gx ]; then
    echo "== stopping the running service (upgrade)"
    svc -d /service/mk2gx 2>/dev/null || true
    sleep 2
fi
mkdir -p "$DEST/service/log" "$DEST/udev"
for f in mk2_gx.py mk2_link.py mk2_decode.py mk2_mqtt.py mk2_config.py mk2_state.py knob_cal.py knob_calibration.json; do
    cp "$HERE/$f" "$DEST/$f"
done

# ---------------------------------------------------------------- udev: Venus serial-starter must not probe the adapter
# (its VE.Direct / GPS probes disturb the MK2). Kept in /data and restored by rc.local after firmware updates.
cat > "$DEST/udev/zz-mk2gx-ignore.rules" <<EOF
ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_SERIAL_SHORT}=="$SERIAL", ENV{VE_SERVICE}="ignore"
EOF

# ---------------------------------------------------------------- configuration (JSON: Venus OS has no TOML reader)
if [ -f "$DEST/mk2gx.json" ] && [ $FORCE = 0 ]; then
    echo "== keeping the existing $DEST/mk2gx.json (use --force-config to overwrite)"
else
    cat > "$DEST/mk2gx.json" <<EOF
{
  "serial": {"port": "$BYID"},
  "device": {"nominal_ac_voltage": 120, "max_charge_current_a": 45},
  "battery": {"max_absorption_v": 28.6, "max_float_v": 27.6},
  "knob": {"min_a": 2.0, "max_a": 15.9, "repeat_s": 10},
  "state": {"path": "$DEST/state.json"},
  "control": {"enabled": $CONTROL},
  "mqtt": {"host": "localhost", "port": 1883, "client_id": "mk2gx"},
  "venus": {"label": "mp", "custom_name": "$NAME",
            "allow_current_limit_control": $LIMIT, "allow_mode_control": $MODE}
}
EOF
    echo "== wrote $DEST/mk2gx.json (edit it for alarms and limits, see docs/configuration.md)"
fi
# the settings must be valid for the new program version (e.g. remote control now requires "control")
if ! CHECK=$(cd "$DEST" && python3 -c "import mk2_config; mk2_config.load('$DEST/mk2gx.json')" 2>&1); then
    echo "$CHECK" | tail -n 3 >&2
    echo "Fix $DEST/mk2gx.json, e.g. add  \"control\": {\"enabled\": true}  if Venus control is wanted," >&2
    echo "or set allow_current_limit_control / allow_mode_control to false, or re-run with --force-config." >&2
    die "settings not valid: the service was not started"
fi
if [ "$CONTROL" = false ]; then
    echo "   monitor only: nothing is sent to the device (use --allow-limit / --allow-mode for remote control)."
elif [ ! -f "$DEST/state.json" ]; then
    echo "   control on, no state file: the switch is NOT sent at start, the knob starts at its minimum."
    echo "   To keep your current settings, create $DEST/state.json, e.g. {\"knob\": 29, \"switch\": 3}"
fi

# ---------------------------------------------------------------- service
cat > "$DEST/service/run" <<EOF
#!/bin/sh
exec 2>&1
exec python3 -u $DEST/mk2_gx.py --config $DEST/mk2gx.json
EOF
cat > "$DEST/service/log/run" <<'EOF'
#!/bin/sh
exec multilog t s25000 n4 /var/log/mk2gx
EOF
chmod +x "$DEST/service/run" "$DEST/service/log/run"

cat > "$DEST/UNINSTALL.txt" <<EOF
Uninstall: sh $DEST/uninstall.sh   (or see docs/venus-gx-service.md)
Stop/start/restart: svc -d|-u|-t /service/mk2gx    Log: tail /var/log/mk2gx/current | tai64nlocal
EOF
cp "$HERE/venus/uninstall.sh" "$DEST/uninstall.sh" 2>/dev/null || true

# ---------------------------------------------------------------- boot: /data/rc.local block (idempotent)
RC=/data/rc.local
if [ ! -f "$RC" ]; then
    printf '#!/bin/sh\n' > "$RC"
else
    [ -f "$RC.bak-mk2gx" ] || cp "$RC" "$RC.bak-mk2gx"
    sed -i '/# >>> mk2gx/,/# <<< mk2gx/d' "$RC"
fi
cat >> "$RC" <<'EOF'
# >>> mk2gx
R=zz-mk2gx-ignore.rules
if ! cmp -s /data/mk2gx/udev/$R /etc/udev/rules.d/$R; then
  cp /data/mk2gx/udev/$R /etc/udev/rules.d/$R
  udevadm control --reload
fi
udevadm trigger --action=add --sysname-match='ttyUSB*'
ln -sfn /data/mk2gx/service /service/mk2gx
# <<< mk2gx
EOF
chmod +x "$RC"

# ---------------------------------------------------------------- activate now
R=zz-mk2gx-ignore.rules
cp "$DEST/udev/$R" /etc/udev/rules.d/$R || die "cannot write /etc/udev/rules.d (read-only root fs?)"
udevadm control --reload
udevadm trigger --action=add --sysname-match="$(basename "$TTY")"
# stop a probe that serial-starter may already be running on the port
if [ -x /opt/victronenergy/serial-starter/stop-tty.sh ]; then
    /opt/victronenergy/serial-starter/stop-tty.sh "$(basename "$TTY")" >/dev/null 2>&1 || true
fi
ln -sfn "$DEST/service" /service/mk2gx
svc -u /service/mk2gx 2>/dev/null || true

echo "== waiting 20 s for the service"
sleep 20
svstat /service/mk2gx || true
tail -n 12 /var/log/mk2gx/current 2>/dev/null | tai64nlocal 2>/dev/null || true
echo "== done. The device should appear in Settings > Devices. Reboot once and check again."
