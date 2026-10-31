#!/bin/bash
# Saves the settings and secrets of Home-Net from the Unraid server into one archive. Run this on the PC.
# The archive holds passwords and keys, so it stays readable by root only. The images are not part of it.
set -eu
UNRAID=${1:-unraid}
DEST=/mnt/user/backup1/HomeNet-Konfig
ssh "$UNRAID" bash -s "$DEST" <<'REMOTE'
set -eu
DEST=$1
mkdir -p "$DEST"
chmod 700 "$DEST"
FILE="$DEST/home-net-konfig-$(date +%Y-%m-%d_%H%M).tar.gz"
ROOT=/mnt/user/backup1/Raspis/PiWartung
TEMPLATES=/boot/config/plugins/dockerMan/templates-user
paths=(/mnt/user/appdata/ntfy /mnt/user/appdata/networkmonitor /mnt/user/appdata/pi-wartung)
paths+=("$ROOT/inventory.json" "$ROOT/.engine/ntfy.conf")
paths+=("$TEMPLATES/my-Ntfy.xml" "$TEMPLATES/my-networkmonitor.xml" "$TEMPLATES/my-pi-wartung-service.xml")
paths+=(/boot/config/plugins/folder.view2/docker.json /var/lib/docker/unraid-autostart)
for key in /root/.ssh/pi-wartung* /root/.ssh/dynaframe /root/.ssh/master; do
  [ -e "$key" ] && paths+=("$key")
done
while IFS= read -r conf; do paths+=("$conf"); done < <(find "$ROOT" -mindepth 2 -name pi.conf -not -path '*/.state/*')
tar czf "$FILE.part" --ignore-failed-read -P "${paths[@]}"
chmod 600 "$FILE.part"
mv "$FILE.part" "$FILE"
ls -1t "$DEST"/home-net-konfig-*.tar.gz | tail -n +6 | xargs -r rm -f
echo "Gesichert: $FILE ($(du -h "$FILE" | cut -f1)), $(tar tzf "$FILE" | wc -l) Einträge"
REMOTE
