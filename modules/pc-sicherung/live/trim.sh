#!/bin/bash
# Makes the boot system small: it is loaded over the network into memory on every backup.
set -e
kver="$(ls /lib/modules | head -1)"
cp "/boot/vmlinuz-$kver" /kernel

mods="/lib/modules/$kver/kernel"
rm -rf "$mods"/{sound,samples,ubuntu,v4l2loopback,zfs} \
       "$mods"/drivers/{gpu,media,infiniband,staging,iio,bluetooth,isdn,net/wireless,net/can,hwmon,input/joystick,input/touchscreen} \
       "$mods"/drivers/{comedi,video,leds,mfd,power,platform/chrome,misc,nfc,fpga,w1,gnss,most,firewire,pcmcia} \
       "$mods"/drivers/{mtd,target,accel,regulator,gpio,watchdog,rtc,net/wwan,net/ieee802154,net/wan,net/dsa,usb/gadget} \
       "$mods"/net/{bluetooth,wireless,mac80211,can,nfc,sunrpc,ceph,rds,tipc,sctp,openvswitch,netfilter,ipv4/netfilter,ipv6/netfilter} \
       "$mods"/fs/{nfs,nfsd,ceph,cifs,smb,ocfs2,gfs2,afs,orangefs,coda,9p,lustre,ubifs,jffs2}
depmod "$kver"

# Network cards that need firmware: Realtek is in most home PCs, Broadcom and Intel 10G more rarely
mkdir -p /tmp/fw
for keep in rtl_nic bnx2 bnx2x tigon intel/ice; do
    [ -e "/lib/firmware/$keep" ] && mkdir -p "/tmp/fw/$(dirname "$keep")" && cp -a "/lib/firmware/$keep" "/tmp/fw/$keep"
done
rm -rf /lib/firmware /usr/lib/firmware
mv /tmp/fw /usr/lib/firmware

rm -rf /boot/* /usr/share/doc /usr/share/man /usr/share/info /usr/share/locale /usr/share/i18n \
       /usr/share/zoneinfo /usr/share/bash-completion /usr/share/lintian /usr/share/initramfs-tools \
       /var/lib/apt/lists/* /var/cache/* /var/log/* /usr/lib/modules-load.d /etc/initramfs-tools \
       /usr/lib/x86_64-linux-gnu/gconv /usr/share/perl* /usr/lib/x86_64-linux-gnu/perl* \
       /usr/share/misc/pci.ids /usr/share/hwdata /usr/lib/udev/hwdb.bin /usr/lib/udev/hwdb.d \
       /usr/lib/x86_64-linux-gnu/libapt-pkg.so* 2>/dev/null || true
apt-get -qq purge -y initramfs-tools initramfs-tools-core >/dev/null 2>&1 || true
rm -rf /var/lib/dpkg /var/lib/apt /var/cache/apt /etc/apt /usr/bin/apt* /usr/bin/dpkg* /usr/lib/apt
echo hn-pc-sicherung >/etc/hostname
