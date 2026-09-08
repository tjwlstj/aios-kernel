#!/bin/sh
# Fixed boot preparation in the Linux hardware/process substrate.
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TERM=dumb
stty -echo
ip link set lo up
if test -d /sys/class/net/eth0; then
    ip link set eth0 up
    if udhcpc -i eth0 -q -n -t 3 -T 3; then
        echo '[AIOS] Network address acquired.'
    else
        echo '[AIOS] Network unavailable; local console remains usable.'
    fi
else
    echo '[AIOS] No Ethernet interface; local console remains usable.'
fi
exec /usr/bin/python3 /opt/aios/linux/aios-image-boot.py
