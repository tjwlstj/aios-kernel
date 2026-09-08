#!/bin/sh
# Executed only in a disposable installer VM with one newly created target disk.
set -eu
test "$(cat /sys/class/block/vda/serial)" = AIOS_BUILD_DISK
test "$(cat /sys/class/block/vda/size)" = 8388608
test "$(cat /sys/class/block/vda/ro)" = 0
if grep -q '^/dev/vda' /proc/mounts; then
    echo 'AIOS image builder refused a mounted target.' >&2
    exit 1
fi
apk add --no-cache python3 ca-certificates sfdisk e2fsprogs syslinux
printf 'label: dos\n,256M,83,*\n,,83\n' | sfdisk /dev/vda
mkfs.ext4 -F -O ^64bit /dev/vda1
mkfs.ext4 -F /dev/vda2
mkdir -p /mnt/target
modprobe ext4
mount -t ext4 /dev/vda2 /mnt/target
mkdir /mnt/target/boot
mount -t ext4 /dev/vda1 /mnt/target/boot
KERNELOPTS='console=ttyS0,115200 psi=1' BOOT_TIMEOUT=1 setup-disk -m sys -k virt -B syslinux /mnt/target
test -s /usr/share/syslinux/mbr.bin
dd if=/usr/share/syslinux/mbr.bin of=/dev/vda bs=440 count=1 conv=notrunc
mkdir -p /mnt/target/opt/aios /mnt/target/etc/aios /mnt/target/etc/network \
    /mnt/target/usr/share/aios/installation-files /mnt/target/usr/local/sbin
cp -R /mnt/source/runtime /mnt/target/opt/aios/linux
find /mnt/target/opt/aios -type d -exec chmod 755 {} +
find /mnt/target/opt/aios -type f -exec chmod 644 {} +
cp /mnt/source/boot.json /mnt/target/etc/aios/boot.json
chroot /mnt/target adduser -D -u 1000 aios
chroot /mnt/target passwd -l root
# adduser -D creates a locked account; confirm it without treating the
# password utility's "already locked" exit as an installation failure.
awk -F: '$1=="aios" && $2 ~ /^[!*]/ { found=1 } END { exit !found }' /mnt/target/etc/shadow
printf 'aios-hosted\n' > /mnt/target/etc/hostname
printf '127.0.0.1 localhost aios-hosted\n::1 localhost\n' > /mnt/target/etc/hosts
# Network acquisition belongs to a bounded guest boot hook; no network service
# can hold the console waiting for DHCP, and no packages are installed at boot.
printf 'auto lo\niface lo inet loopback\n' > /mnt/target/etc/network/interfaces
rm -f /mnt/target/etc/runlevels/boot/networking
cp /mnt/source/aios-start-system /mnt/target/usr/local/sbin/aios-start-system
chmod 755 /mnt/target/usr/local/sbin/aios-start-system
sed -i '/getty/d' /mnt/target/etc/inittab
printf '\nttyS0::once:/usr/local/sbin/aios-start-system\n' >> /mnt/target/etc/inittab
# setup-disk generated serial_port/serial_baud from KERNELOPTS.
test -s /mnt/target/boot/extlinux.conf
chroot /mnt/target apk info -vv > /mnt/target/usr/share/aios/installation-files/packages.txt
ls /mnt/target/lib/modules > /mnt/target/usr/share/aios/installation-files/kernel.txt
python3 /mnt/source/image_finalize.py
sync
echo AIOS_IMAGE_INSTALL_COMPLETE
