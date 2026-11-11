#!/bin/sh
# Install dbus-mqtt-devices (third-party driver, https://github.com/freakent/dbus-mqtt-devices) on Venus OS
# for the Phoenix MultiPlus VE.9bit MK2 Link. BEST EFFORT: based on the manual install on Venus OS v3.85.
#
# The driver's own setup.sh is not used: it runs pip/opkg at every boot and fails on Python 3.12.
# This script installs everything under /data (survives firmware updates).
#
# Prepare on the PC (see docs/venus-gx-service.md):
#   - extract dbus-mqtt-devices v0.9.0 to a folder dbus-mqtt-devices-0.9.0
#   - put pure-Python PyYAML into dbus-mqtt-devices-0.9.0/ext/pylib/yaml
#   - copy the folder to the Venus, e.g. scp -r dbus-mqtt-devices-0.9.0 root@VENUS:/data/tmp/
# Then on the Venus:
#   sh install-driver.sh /data/tmp/dbus-mqtt-devices-0.9.0
set -e

SRC="$1"
NAME=dbus-mqtt-devices-0.9.0
BASE=/data/drivers/$NAME

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root"
[ -d /opt/victronenergy ] || die "this is not Venus OS"
[ -n "$SRC" ] && [ -f "$SRC/dbus_mqtt_devices.py" ] || die "usage: sh install-driver.sh /path/to/$NAME"
[ -f "$SRC/ext/pylib/yaml/__init__.py" ] || die "PyYAML missing: copy the pure-Python 'yaml' package to $SRC/ext/pylib/yaml"
[ -d /opt/victronenergy/dbus-digitalinputs/ext/velib_python ] || die "velib_python not found on this Venus OS version"

echo "== installing $NAME to $BASE"
if [ -e /service/dbus-mqtt-devices ]; then
    svc -d /service/dbus-mqtt-devices 2>/dev/null || true
fi
mkdir -p /data/drivers
if [ "$(realpath "$SRC")" != "$BASE" ]; then
    rm -rf "$BASE"
    cp -r "$SRC" "$BASE"
fi

# velib_python from the Venus OS installation
mkdir -p "$BASE/ext"
rm -rf "$BASE/ext/velib_python"
ln -s /opt/victronenergy/dbus-digitalinputs/ext/velib_python "$BASE/ext/velib_python"

# services.yml: CurrentLimit defaults as float (the Venus settings store rejects 2.0 for an integer setting)
# and the unit "A". Only the two *CurrentLimit keys (not *IsAdjustable) are changed. Original kept as .orig.
Y="$BASE/services.yml"
[ -f "$Y.orig" ] || cp "$Y" "$Y.orig"
awk '
  /^[ ]+[A-Za-z0-9\/]*CurrentLimit:/ { inlim = 1; hasfmt = 0; print; next }
  /^[ ]+[A-Za-z0-9\/]+:[ ]*(#.*)?$/  { inlim = 0 }
  inlim && /^[ ]+format:/ { hasfmt = 1 }
  inlim && /^[ ]+default:[ ]*50[ ]*(#.*)?$/ {
      sub(/default:[ ]*50/, "default: 50.0"); print
      if (!hasfmt) { match($0, /^[ ]+/); printf "%sformat: \"{} A\"\n", substr($0, 1, RLENGTH); hasfmt = 1 }
      next }
  { print }
' "$Y.orig" > "$Y.tmp"
mv "$Y.tmp" "$Y"
echo "services.yml CurrentLimit lines:"
grep -n -A4 "CurrentLimit:" "$Y" | grep -E "CurrentLimit:|default|format" || true

# service run script with the bundled PyYAML
cat > "$BASE/bin/service/run" <<EOF
#!/bin/sh
echo "*** starting dbus-mqtt-devices ***"
BASE=$BASE
exec 2>&1
# ext/pylib holds pure-Python PyYAML (Venus OS has no python3-pyyaml package)
PYTHONPATH=\$BASE/ext/pylib HOME=\$BASE exec \$BASE/dbus_mqtt_devices.py
EOF
chmod +x "$BASE/dbus_mqtt_devices.py" "$BASE/bin/service/run"
[ -f "$BASE/bin/service/log/run" ] && chmod +x "$BASE/bin/service/log/run"

# start at boot: /data/rc.local block (idempotent)
RC=/data/rc.local
if [ ! -f "$RC" ]; then
    printf '#!/bin/sh\n' > "$RC"
else
    [ -f "$RC.bak-dbus-mqtt-devices" ] || cp "$RC" "$RC.bak-dbus-mqtt-devices"
    sed -i '/# >>> dbus-mqtt-devices/,/# <<< dbus-mqtt-devices/d' "$RC"
    # comment out lines of the driver's own setup.sh (other versions, setup-dependencies)
    sed -i '/^[^#].*dbus-mqtt-devices/s/^/# /' "$RC"
fi
cat >> "$RC" <<EOF
# >>> dbus-mqtt-devices
ln -sfn $BASE/bin/service /service/dbus-mqtt-devices
# <<< dbus-mqtt-devices
EOF
chmod +x "$RC"

ln -sfn "$BASE/bin/service" /service/dbus-mqtt-devices
svc -u /service/dbus-mqtt-devices 2>/dev/null || true
echo "== waiting 10 s for the driver"
sleep 10
svstat /service/dbus-mqtt-devices || true
tail -n 8 /var/log/dbus-mqtt-devices/current 2>/dev/null | tai64nlocal 2>/dev/null || true
echo "== done. Make sure Settings > Services > MQTT on LAN is enabled for the PC bridge."
