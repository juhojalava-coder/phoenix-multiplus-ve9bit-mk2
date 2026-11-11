#!/bin/sh
# Remove the GX service (mk2gx) of Phoenix MultiPlus VE.9bit MK2 Link from Venus OS.
#   sh uninstall.sh            remove the service, keep dbus-mqtt-devices
#   sh uninstall.sh --driver   also remove dbus-mqtt-devices v0.9.0 installed by install-driver.sh
# The device itself is not touched (it keeps its current switch state and settings).

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if [ -e /service/mk2gx ]; then
    svc -d /service/mk2gx /service/mk2gx/log 2>/dev/null
    sleep 2
    rm -f /service/mk2gx
fi
[ -f /data/rc.local ] && sed -i '/# >>> mk2gx/,/# <<< mk2gx/d' /data/rc.local
rm -f /etc/udev/rules.d/zz-mk2gx-ignore.rules
udevadm control --reload 2>/dev/null
rm -rf /data/mk2gx
echo "mk2gx removed (log files: /var/log/mk2gx)"

if [ "$1" = "--driver" ]; then
    if [ -e /service/dbus-mqtt-devices ]; then
        svc -d /service/dbus-mqtt-devices /service/dbus-mqtt-devices/log 2>/dev/null
        sleep 2
        rm -f /service/dbus-mqtt-devices
    fi
    [ -f /data/rc.local ] && sed -i '/# >>> dbus-mqtt-devices/,/# <<< dbus-mqtt-devices/d' /data/rc.local
    rm -rf /data/drivers/dbus-mqtt-devices-0.9.0
    echo "dbus-mqtt-devices removed"
fi
echo "Optional: remove the old device settings on Venus (Settings > Devices) if they are still listed."
