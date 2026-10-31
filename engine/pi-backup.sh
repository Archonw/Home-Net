#!/bin/bash
# Pi-Wartung backup engine. Runs on the Unraid server as root and needs nothing
# installed on the Pi except ssh, sudo and rsync.
#
#   pi-backup.sh backup <pi-dir>    full run: sync, build image, compress, verify
#   pi-backup.sh start  <pi-dir>    same, detached from the calling ssh session
#   pi-backup.sh status <pi-dir>    live facts about the Pi as key=value
#   pi-backup.sh images <pi-dir>    existing images, one tab separated line each
#   pi-backup.sh state  <pi-dir>    running/last-run state as key=value
#   pi-backup.sh cancel <pi-dir>    stop a running backup
#   pi-backup.sh keygen <pi-dir>    create the key of this Pi if needed, print it
#   pi-backup.sh install-key <pi-dir>  same, then install it using a password from stdin
#   pi-backup.sh due    <root>      run every Pi whose schedule is due (for cron)
#
# How a backup works: rsync keeps a file level mirror of the Pi in an ext4
# container, so repeat runs only transfer changes. The image is then built new
# from that mirror, which makes it clean and free of deleted data. Free space
# stays out of the image; the image grows to the card size on its first boot.
set -u
export LC_ALL=C PATH=/usr/local/sbin:/usr/sbin:/sbin:/usr/local/bin:/usr/bin:/bin

ENGINE_VERSION=1
SCRIPT=$(readlink -f "$0")

log()      { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
step()     { printf '@@STEP %s %s %s\n' "$1" "$2" "$3"; log "--- $3"; }
progress() { printf '@@PROGRESS %s %s\n' "$1" "$2"; }
die()      { log "FEHLER: $*"; ERROR="$*"; exit 1; }

load_conf() {
  PIDIR=${1%/}
  [ -f "$PIDIR/pi.conf" ] || { echo "Keine pi.conf in $PIDIR" >&2; exit 2; }
  # Defaults, in case of a pi.conf from an older app version.
  NAME="" HOST="" PORT=22 USER_NAME=pi KEY=/root/.ssh/pi-wartung JUMP="" JUMP_PORT=22
  KEEP=3 FORMAT=xz EXPAND=1 EXCLUDES="" BWLIMIT=0 COMPRESS=0 PRE_CMD="" POST_CMD=""
  SCHEDULE=off HOUR=3 MARGIN_MB=512
  # shellcheck disable=SC1091
  . "$PIDIR/pi.conf"
  STATE="$PIDIR/.state"
  mkdir -p "$STATE" "$PIDIR/logs"
  LOCK="$STATE/lock"
}

make_ssh_wrapper() {
  SSHW=$(mktemp /tmp/piw-ssh.XXXXXX)
  {
    echo '#!/bin/bash'
    printf 'exec ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=6'
    printf ' -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR -o IdentitiesOnly=yes'
    printf ' -o UserKnownHostsFile=%q -i %q -p %q' "$STATE/known_hosts" "$KEY" "$PORT"
    if [ -n "$JUMP" ]; then
      local proxy
      proxy="ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR -o IdentitiesOnly=yes -o UserKnownHostsFile=$(printf %q "$STATE/known_hosts") -i $(printf %q "$KEY") -p $(printf %q "$JUMP_PORT") -W %h:%p $(printf %q "$JUMP")"
      printf ' -o ProxyCommand=%q' "$proxy"
    fi
    echo ' "$@"'
  } > "$SSHW"
  chmod 700 "$SSHW"
  TARGET="$USER_NAME@$HOST"
}

on_pi() { "$SSHW" "$TARGET" "$@"; }

# ---------------------------------------------------------------- backup
ERROR=""
MNT="" LOOPDEV_ROOT="" PRE_DONE=0 FINISHED=0 CANCELLED=0 WARNINGS=""

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  [ -n "$MNT" ] && mountpoint -q "$MNT" && { umount "$MNT" 2>/dev/null || umount -l "$MNT"; }
  [ -n "$MNT" ] && rmdir "$MNT" 2>/dev/null
  if [ "$PRE_DONE" = 1 ] && [ -n "$POST_CMD" ]; then
    log "Nachher-Befehl auf dem Pi ausführen"
    on_pi "$POST_CMD" || log "WARNUNG: Nachher-Befehl fehlgeschlagen"
  fi
  rm -f "$STATE/root.ext4" "$STATE/image.raw" "$STATE/dd.progress"
  [ -n "${OUT_PART:-}" ] && rm -f "$OUT_PART"
  [ -n "${SSHW:-}" ] && rm -f "$SSHW"
  local status=failed
  [ "$FINISHED" = 1 ] && status=ok
  [ "$CANCELLED" = 1 ] && status=cancelled
  [ "$status" = failed ] && [ -z "$ERROR" ] && ERROR="Abbruch mit Code $rc"
  {
    echo "LAST_STATUS=$status"
    echo "LAST_TIME=$(date +%s)"
    printf 'LAST_ERROR=%q\n' "$ERROR"
    printf 'LAST_IMAGE=%q\n' "${OUT_NAME:-}"
    printf 'LAST_WARNINGS=%q\n' "$WARNINGS"
    [ "$status" = ok ] && echo "LAST_OK_TIME=$(date +%s)" || grep -s '^LAST_OK_TIME=' "$STATE/last"
  } > "$STATE/last.new"
  mv "$STATE/last.new" "$STATE/last"
  rm -f "$STATE/pid"
  log "Ende: $status"
  printf '@@DONE %s\n' "$status"
  exit "$rc"
}

probe_pi() {
  on_pi true || die "Pi nicht erreichbar ($TARGET, Port $PORT)"
  on_pi 'sudo -n true' 2>/dev/null || die "sudo ohne Passwort funktioniert auf dem Pi nicht"
  on_pi 'command -v rsync >/dev/null' || die "rsync fehlt auf dem Pi (sudo apt install rsync)"

  ROOTDEV=$(on_pi "lsblk -rpno NAME,MOUNTPOINT | awk '\$2==\"/\"{print \$1}'")
  [ -n "$ROOTDEV" ] || die "Root-Partition nicht gefunden"
  DISK=$(on_pi "lsblk -rpno PKNAME $ROOTDEV | head -1")
  [ -n "$DISK" ] || die "Datenträger zu $ROOTDEV nicht gefunden"
  [ "$(on_pi "lsblk -rno FSTYPE $ROOTDEV")" = ext4 ] || die "Root-Dateisystem ist nicht ext4, das wird nicht unterstützt"

  BOOTDEV=$(on_pi "lsblk -rpno NAME,MOUNTPOINT $DISK | awk '\$2==\"/boot/firmware\"||\$2==\"/boot\"{print \$1; exit}'")
  [ -n "$BOOTDEV" ] || die "Boot-Partition nicht gefunden"
  [ "$(on_pi "lsblk -rno FSTYPE $BOOTDEV")" = vfat ] || die "Boot-Partition ist nicht vfat"

  on_pi "sudo -n env LC_ALL=C sfdisk -d $DISK" > "$STATE/table.src" || die "Partitionstabelle nicht lesbar"
  grep -q '^label: dos' "$STATE/table.src" || die "Nur MBR-Partitionstabellen werden unterstützt"
  [ "$(grep -c '^/dev/' "$STATE/table.src")" = 2 ] || die "Nur das Standardlayout mit Boot und Root wird unterstützt"
  grep -qF "$BOOTDEV :" "$STATE/table.src" && grep -qF "$ROOTDEV :" "$STATE/table.src" \
    || die "Partitionstabelle passt nicht zu den Einhängepunkten"

  on_pi "sudo -n env LC_ALL=C dumpe2fs -h $ROOTDEV 2>/dev/null" > "$STATE/fs.src" || die "Dateisystem-Daten nicht lesbar"
  FS_UUID=$(awk -F': +' '/^Filesystem UUID/{print $2}' "$STATE/fs.src")
  FS_LABEL=$(awk -F': +' '/^Filesystem volume name/{print $2}' "$STATE/fs.src")
  [ "$FS_LABEL" = "<none>" ] && FS_LABEL=""
  FS_BS=$(awk -F': +' '/^Block size/{print $2}' "$STATE/fs.src")
  FS_ISIZE=$(awk -F': +' '/^Inode size/{print $2}' "$STATE/fs.src")
  # Copy the features of the source, a newer mke2fs would otherwise enable ones
  # the kernel on an old Pi cannot read. The filtered ones are runtime states.
  FS_FEATURES=$(awk -F': +' '/^Filesystem features/{print $2}' "$STATE/fs.src" \
    | tr ' ' '\n' | grep -vE '^(needs_recovery|recover|orphan_present|mmp|$)' | paste -sd, -)
  [ -n "$FS_UUID" ] && [ -n "$FS_BS" ] && [ -n "$FS_FEATURES" ] || die "Dateisystem-Daten unvollständig"

  USED_KB=$(on_pi "df -k --output=used / | tail -1" | tr -d ' ')
  PI_HOSTNAME=$(on_pi hostname)
  PI_MODEL=$(on_pi "tr -d '\0' < /proc/device-tree/model" 2>/dev/null)
  PI_OS=$(on_pi ". /etc/os-release; echo \$PRETTY_NAME")
  log "Pi: $PI_HOSTNAME, $PI_MODEL, $PI_OS"
  log "Root $ROOTDEV ($((USED_KB / 1024)) MiB belegt), Boot $BOOTDEV, Features $FS_FEATURES"
}

check_space() {
  local avail_kb need_kb
  avail_kb=$(df -k --output=avail "$PIDIR" | tail -1 | tr -d ' ')
  need_kb=$((USED_KB * 3))
  [ "$avail_kb" -gt "$need_kb" ] || die "Zu wenig Platz auf dem Ziel: $((avail_kb/1024)) MiB frei, $((need_kb/1024)) MiB nötig"
}

open_mirror() {
  local want_kb=$((USED_KB * 14 / 10 + 2097152)) have_kb=0
  local container="$STATE/mirror.ext4"
  MNT=$(mktemp -d /tmp/piw-mnt.XXXXXX)
  if [ ! -f "$container" ]; then
    log "Neuer Spiegel-Container: $((want_kb / 1024)) MiB"
    truncate -s "${want_kb}K" "$container"
    mke2fs -q -F -t ext4 -m 0 -L piw-mirror "$container" || die "Spiegel-Container nicht erstellbar"
  else
    have_kb=$(($(stat -c %s "$container") / 1024))
  fi
  e2fsck -fy "$container" >/dev/null 2>&1
  [ $? -lt 4 ] || die "Spiegel-Container ist beschädigt: $container"
  if [ "$have_kb" -gt 0 ] && [ "$have_kb" -lt "$want_kb" ]; then
    log "Spiegel-Container vergrößern auf $((want_kb / 1024)) MiB"
    truncate -s "${want_kb}K" "$container"
    resize2fs "$container" >/dev/null 2>&1 || die "Spiegel-Container nicht vergrößerbar"
  fi
  mount -o loop,noatime "$container" "$MNT" || die "Spiegel-Container nicht einhängbar"
}

# The fstab of the restored Pi still mounts these, so the empty folders must exist.
restore_mountpoints() {
  local m
  for m in "$@"; do
    log "Netzwerk-Einhängepunkt bleibt leer: $m"
    mkdir -p "$MNT$m"
    chown --reference="$(dirname "$MNT$m")" "$MNT$m"
  done
}

sync_mirror() {
  local excl="$STATE/excludes" rc try netmounts=() line
  while IFS= read -r line; do
    [ -n "$line" ] && netmounts+=("$(printf '%b' "$line")")
  done < <(on_pi "findmnt -rno TARGET -t nfs,nfs4,cifs,smb3,fuse.sshfs,fuse.rclone,davfs")
  {
    cat <<'EOF'
/lost+found
/var/swap
/var/cache/apt/archives/*.deb
/var/cache/apt/*.bin
/var/lib/apt/lists/*
/tmp/*
/var/tmp/*
/home/*/.cache/*
/root/.cache/*
/home/*/.local/share/Trash/*
EOF
    printf '%s\n' "$EXCLUDES"
    # Network mounts are not on the card. Naming them keeps a stale mount from
    # raising an I/O error, which would make rsync skip all deletions.
    local m
    for m in "${netmounts[@]}"; do printf '%s\n' "$m"; done
  } | grep -v '^$' > "$excl"

  local opts=(-aHAXS --numeric-ids -x --delete --delete-excluded --force --timeout=600
              --exclude-from="$excl" --info=progress2,stats2 --no-inc-recursive
              -e "$SSHW" --rsync-path="sudo -n nice -n 19 ionice -c3 rsync")
  [ "$BWLIMIT" -gt 0 ] && opts+=(--bwlimit="$BWLIMIT")
  [ "$COMPRESS" = 1 ] && opts+=(-z)

  # WLAN dropouts can last minutes, so the waits grow. The mirror keeps what arrived.
  local waits=(30 120 300 600 900) wait
  for try in 1 2 3 4 5 6; do
    log "rsync Versuch $try"
    rsync "${opts[@]}" "$TARGET:/" "$MNT/" 2>&1 | tr '\r' '\n' | awk '
      /^[ \t]*[0-9,]+[ \t]+[0-9]+%/ {
        now = systime()
        if (now - last >= 5 && match($0, /[0-9]+%/)) {
          printf "@@PROGRESS %s %s %s\n", substr($0, RSTART, RLENGTH-1), $1, $3; fflush(); last = now
        }
        next }
      NF { print; fflush() }'
    rc=${PIPESTATUS[0]}
    case "$rc" in
      0) restore_mountpoints "${netmounts[@]}"; return 0 ;;
      24) log "Hinweis: Dateien sind während der Sicherung verschwunden (normal im laufenden Betrieb)"
          restore_mountpoints "${netmounts[@]}"; return 0 ;;
      23) WARNINGS="Einige Dateien konnten nicht gelesen werden, siehe Protokoll"; log "WARNUNG: $WARNINGS"
          restore_mountpoints "${netmounts[@]}"; return 0 ;;
      10|11|12|30|35|255)
        wait=${waits[$((try - 1))]:-0}
        [ "$wait" -gt 0 ] || break
        log "Verbindung abgebrochen (Code $rc), neuer Versuch in $wait s"
        sleep "$wait" ;;
      *) die "rsync fehlgeschlagen (Code $rc)" ;;
    esac
  done
  die "rsync nach mehreren Versuchen fehlgeschlagen (Code $rc)"
}

sync_boot() {
  local hash old=""
  hash=$(on_pi "sudo -n sha256sum $BOOTDEV | cut -d' ' -f1") || die "Boot-Partition nicht lesbar"
  [ -f "$STATE/boot.sha256" ] && old=$(cat "$STATE/boot.sha256")
  if [ "$hash" = "$old" ] && [ -f "$STATE/boot.raw" ]; then
    log "Boot-Partition unverändert"
    return
  fi
  log "Boot-Partition kopieren"
  on_pi "sudo -n dd if=$BOOTDEV bs=1M status=none | gzip -1" | gzip -dc > "$STATE/boot.tmp"
  local statuses=("${PIPESTATUS[@]}")
  [ "${statuses[0]}" = 0 ] && [ "${statuses[1]}" = 0 ] || die "Boot-Partition konnte nicht kopiert werden"
  # A mounted FAT carries a dirty flag; clear it so the restored Pi boots quietly.
  fsck.vfat -a "$STATE/boot.tmp" >/dev/null 2>&1
  [ $? -le 1 ] || die "Boot-Partition ist fehlerhaft"
  mv "$STATE/boot.tmp" "$STATE/boot.raw"
  echo "$hash" > "$STATE/boot.sha256"
}

inject_expand() {
  local m="$1"
  mkdir -p "$m/usr/local/sbin"
  cat > "$m/usr/local/sbin/pi-wartung-expand" <<'EOF'
#!/bin/sh
# Grows the root partition to the full card on the first boot of a restored
# image, then removes itself. Installed into the image by Pi-Wartung.
PATH=/usr/local/sbin:/usr/sbin:/sbin:/usr/local/bin:/usr/bin:/bin
LOG=/var/log/pi-wartung-expand.log
MARK=/var/lib/pi-wartung-expand.reboot
exec >>"$LOG" 2>&1
date
finish() {
  rm -f "$MARK" /etc/systemd/system/sysinit.target.wants/pi-wartung-expand.service \
        /etc/systemd/system/pi-wartung-expand.service /usr/local/sbin/pi-wartung-expand
  sync
}
ROOT=$(lsblk -rpno NAME,MOUNTPOINT | awk '$2=="/"{print $1}')
DISK=$(lsblk -rpno PKNAME "$ROOT" | head -1)
NUM=$(cat "/sys/class/block/$(basename "$ROOT")/partition")
if [ -z "$ROOT" ] || [ -z "$DISK" ] || [ -z "$NUM" ]; then
  echo "Datentraeger nicht erkannt"
  finish
  exit 0
fi
if [ ! -e "$MARK" ]; then
  before=$(cat "/sys/class/block/$(basename "$ROOT")/size")
  echo ', +' | sfdisk --force --no-reread --no-tell-kernel -N "$NUM" "$DISK"
  partx -u -n "$NUM" "$DISK"
  after=$(cat "/sys/class/block/$(basename "$ROOT")/size")
  echo "Partition $before -> $after Sektoren"
  if [ "$before" = "$after" ] && [ "$(cat "/sys/class/block/$(basename "$DISK")/size")" -gt "$((after + 8192))" ]; then
    echo "Kernel uebernimmt die neue Groesse erst nach Neustart"
    touch "$MARK"
    sync
    systemctl --no-block reboot
    exit 0
  fi
fi
resize2fs "$ROOT"
finish
exit 0
EOF
  chmod 755 "$m/usr/local/sbin/pi-wartung-expand"
  cat > "$m/etc/systemd/system/pi-wartung-expand.service" <<'EOF'
[Unit]
Description=Pi-Wartung: Root-Partition auf die ganze Karte vergroessern
DefaultDependencies=no
After=local-fs.target systemd-remount-fs.service
Before=sysinit.target shutdown.target
Conflicts=shutdown.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/pi-wartung-expand
TimeoutStartSec=600

[Install]
WantedBy=sysinit.target
EOF
  mkdir -p "$m/etc/systemd/system/sysinit.target.wants"
  ln -sf ../pi-wartung-expand.service "$m/etc/systemd/system/sysinit.target.wants/pi-wartung-expand.service"
}

build_root() {
  local img="$STATE/root.ext4" used_kb size_kb rc tries=0 output
  used_kb=$(df -k --output=used "$MNT" | tail -1 | tr -d ' ')
  size_kb=$((used_kb * 13 / 10 + 524288))
  while :; do
    rm -f "$img"
    truncate -s "${size_kb}K" "$img"
    log "Dateisystem aufbauen: $((size_kb / 1024)) MiB"
    # shellcheck disable=SC2086
    output=$(mke2fs -q -F -t ext4 -b "$FS_BS" -I "$FS_ISIZE" -i 16384 -m 5 -O "none,$FS_FEATURES" \
      -U "$FS_UUID" ${FS_LABEL:+-L "$FS_LABEL"} -d "$MNT" "$img" 2>&1)
    rc=$?
    [ "$rc" = 0 ] && break
    printf '%s\n' "$output" | tail -5
    # Only a lack of space is worth another try.
    printf '%s' "$output" | grep -qiE 'no space|could not allocate|inode' || die "Dateisystem konnte nicht aufgebaut werden"
    tries=$((tries + 1))
    [ "$tries" -lt 3 ] || die "Dateisystem konnte nicht aufgebaut werden"
    log "Zu klein, nächster Versuch mit mehr Platz"
    size_kb=$((size_kb * 3 / 2))
  done
  umount "$MNT" || die "Spiegel-Container nicht aushängbar"

  if [ "$EXPAND" = 1 ]; then
    mount -o loop,noatime "$img" "$MNT" || die "Neues Dateisystem nicht einhängbar"
    inject_expand "$MNT"
    umount "$MNT" || die "Neues Dateisystem nicht aushängbar"
  fi
  rmdir "$MNT"; MNT=""

  e2fsck -fy "$img" >/dev/null 2>&1
  [ $? -lt 4 ] || die "Neues Dateisystem ist fehlerhaft"
  resize2fs -M "$img" >/dev/null 2>&1 || die "Verkleinern fehlgeschlagen"
  local blocks
  blocks=$(dumpe2fs -h "$img" 2>/dev/null | awk -F': +' '/^Block count/{print $2}')
  blocks=$((blocks + MARGIN_MB * 1024 * 1024 / FS_BS))
  resize2fs "$img" "$blocks" >/dev/null 2>&1 || die "Freien Platz hinzufügen fehlgeschlagen"
  e2fsck -fn "$img" >/dev/null 2>&1 || die "Dateisystem-Prüfung nach dem Verkleinern fehlgeschlagen"
  truncate -s $((blocks * FS_BS)) "$img"
  log "Root-Dateisystem: $((blocks * FS_BS / 1048576)) MiB"
}

assemble_image() {
  local rootimg="$STATE/root.ext4" raw="$STATE/image.raw" table="$STATE/table.new"
  local root_start boot_start boot_size root_sectors total
  boot_start=$(grep -F "$BOOTDEV :" "$STATE/table.src" | sed -E 's/.*start= *([0-9]+).*/\1/')
  boot_size=$(grep -F "$BOOTDEV :" "$STATE/table.src" | sed -E 's/.*size= *([0-9]+).*/\1/')
  root_start=$(grep -F "$ROOTDEV :" "$STATE/table.src" | sed -E 's/.*start= *([0-9]+).*/\1/')
  [ "$(stat -c %s "$STATE/boot.raw")" = $((boot_size * 512)) ] || die "Boot-Kopie passt nicht zur Partitionstabelle"
  # Round the partition up to 4 MiB, as SD cards prefer.
  root_sectors=$(( ($(stat -c %s "$rootimg") / 512 + 8191) / 8192 * 8192 ))
  total=$((root_start + root_sectors))

  sed -E "\|^$ROOTDEV :|s/size= *[0-9]+/size=$root_sectors/; /^last-lba:/d; /^device:/d; /^sector-size:/d" \
    "$STATE/table.src" > "$table"
  rm -f "$raw"
  truncate -s $((total * 512)) "$raw"
  # Device names in the dump refer to the Pi; sfdisk only needs the fields.
  sed -E 's|^/dev/[^ ]+ : ||' "$table" | sfdisk --quiet "$raw" || die "Partitionstabelle nicht schreibbar"
  dd if="$STATE/boot.raw" of="$raw" bs=4M seek=$((boot_start * 512)) oflag=seek_bytes conv=notrunc,sparse status=none || die "Boot nicht schreibbar"
  dd if="$rootimg" of="$raw" bs=4M seek=$((root_start * 512)) oflag=seek_bytes conv=notrunc,sparse status=none || die "Root nicht schreibbar"
  rm -f "$rootimg"

  sfdisk -d "$raw" > "$STATE/table.check" || die "Partitionstabelle im Image nicht lesbar"
  local src_id new_id
  src_id=$(awk '/^label-id:/{print $2}' "$STATE/table.src")
  new_id=$(awk '/^label-id:/{print $2}' "$STATE/table.check")
  [ "$src_id" = "$new_id" ] || die "Datenträger-ID im Image stimmt nicht ($new_id statt $src_id)"
  log "Image: $((total * 512 / 1048576)) MiB, ID $new_id"
  RAW_BYTES=$((total * 512))
}

compress_image() {
  local raw="$STATE/image.raw" stamp ext comp
  stamp=$(date +%Y-%m-%d_%H%M)
  case "$FORMAT" in
    xz)   ext=img.xz; comp=(xz -T0 -6 -c) ;;
    gz)   ext=img.gz; comp=(gzip -6 -c); command -v pigz >/dev/null && comp=(pigz -6 -c) ;;
    img)  ext=img;    comp=(cat) ;;
    *)    die "Unbekanntes Format $FORMAT" ;;
  esac
  OUT_NAME="${NAME}_${stamp}.${ext}"
  local out="$PIDIR/$OUT_NAME"
  OUT_PART="$out.part"
  if [ "$FORMAT" = img ]; then
    cp --sparse=always "$raw" "$OUT_PART" || die "Image nicht kopierbar"
  else
    local dd_progress="$STATE/dd.progress" watcher
    : > "$dd_progress"
    ( while sleep 5; do
        b=$(tr '\r' '\n' < "$dd_progress" | awk '/bytes/{v=$1} END{print v+0}')
        printf '@@PROGRESS %s %s\n' "$((b * 100 / RAW_BYTES))" "$((b / 1048576)) MiB"
      done ) &
    watcher=$!
    dd if="$raw" bs=4M status=progress 2>"$dd_progress" | nice -n 10 "${comp[@]}" > "$OUT_PART"
    local statuses=("${PIPESTATUS[@]}")
    kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
    [ "${statuses[0]}" = 0 ] && [ "${statuses[1]}" = 0 ] || die "Komprimieren fehlgeschlagen"
  fi
  case "$FORMAT" in
    xz) xz -t "$OUT_PART" || die "Prüfung des Archivs fehlgeschlagen" ;;
    gz) gzip -t < "$OUT_PART" || die "Prüfung des Archivs fehlgeschlagen" ;;
  esac
  local sha
  sha=$(sha256sum "$OUT_PART" | cut -d' ' -f1)
  mv "$OUT_PART" "$out"
  OUT_PART=""
  {
    echo "date=$(date '+%Y-%m-%d %H:%M')"
    echo "hostname=$PI_HOSTNAME"
    echo "model=$PI_MODEL"
    echo "os=$PI_OS"
    echo "raw_bytes=$RAW_BYTES"
    echo "sha256=$sha"
    echo "expand=$EXPAND"
    echo "warnings=$WARNINGS"
  } > "$out.info"
  log "Fertig: $OUT_NAME ($(($(stat -c %s "$out") / 1048576)) MiB)"
}

prune_images() {
  [ "$KEEP" -gt 0 ] || return 0
  local f n=0
  while IFS= read -r f; do
    n=$((n + 1))
    if [ "$n" -gt "$KEEP" ]; then
      log "Alte Sicherung entfernen: $(basename "$f")"
      rm -f "$f" "$f.info"
    fi
  done < <(ls -1t "$PIDIR/${NAME}_"*.img "$PIDIR/${NAME}_"*.img.xz "$PIDIR/${NAME}_"*.img.gz 2>/dev/null)
}

cmd_backup() {
  load_conf "$1"
  exec 9>"$LOCK"
  flock -n 9 || { echo "Für diesen Pi läuft schon eine Sicherung" >&2; exit 3; }
  echo $$ > "$STATE/pid"
  make_ssh_wrapper
  trap cleanup EXIT
  trap 'CANCELLED=1; ERROR="Abgebrochen"; exit 130' INT TERM HUP
  log "Sicherung von $NAME ($TARGET) startet"

  step 1 6 "Pi prüfen"
  probe_pi
  check_space

  if [ -n "$PRE_CMD" ]; then
    log "Vorher-Befehl auf dem Pi ausführen"
    on_pi "$PRE_CMD" || die "Vorher-Befehl fehlgeschlagen"
    PRE_DONE=1
  fi

  step 2 6 "Dateien abgleichen"
  open_mirror
  sync_mirror

  step 3 6 "Boot-Partition sichern"
  sync_boot

  if [ "$PRE_DONE" = 1 ] && [ -n "$POST_CMD" ]; then
    log "Nachher-Befehl auf dem Pi ausführen"
    on_pi "$POST_CMD" || WARNINGS="Nachher-Befehl fehlgeschlagen"
    PRE_DONE=0
  fi

  step 4 6 "Kleines Dateisystem aufbauen"
  build_root

  step 5 6 "Image zusammensetzen"
  assemble_image

  step 6 6 "Komprimieren und prüfen"
  compress_image
  rm -f "$STATE/image.raw"
  prune_images
  ls -1t "$PIDIR"/logs/*.log 2>/dev/null | tail -n +21 | xargs -r -d '\n' rm -f
  FINISHED=1
}

# ---------------------------------------------------------------- helpers
cmd_start() {
  load_conf "$1"
  local logfile
  logfile="$PIDIR/logs/$(date +%Y-%m-%d_%H%M%S).log"
  ( flock -n 9 ) 9>"$LOCK" || { echo "Für diesen Pi läuft schon eine Sicherung" >&2; exit 3; }
  setsid nohup bash "$SCRIPT" backup "$PIDIR" >"$logfile" 2>&1 </dev/null &
  echo "$logfile"
}

cmd_state() {
  load_conf "$1"
  local running=0
  ( flock -n 9 ) 9>"$LOCK" || running=1
  echo "RUNNING=$running"
  echo "ENGINE_VERSION=$ENGINE_VERSION"
  [ -f "$STATE/last" ] && cat "$STATE/last"
  local log
  log=$(ls -1t "$PIDIR"/logs/*.log 2>/dev/null | head -1)
  printf 'LOGFILE=%q\n' "$log"
  [ -f "$STATE/mirror.ext4" ] && echo "MIRROR_BYTES=$(du -B1 "$STATE/mirror.ext4" | cut -f1)"
  return 0
}

cmd_cancel() {
  load_conf "$1"
  [ -f "$STATE/pid" ] || { echo "Keine laufende Sicherung"; return 0; }
  local pid
  pid=$(cat "$STATE/pid")
  # The backup runs in its own session; children die with the group, the main
  # process receives TERM last so that its cleanup finds them gone.
  pkill -TERM -s "$pid" -x 'rsync|ssh|xz|gzip|pigz|mke2fs|resize2fs|e2fsck|dd|sleep' 2>/dev/null
  kill -TERM "$pid" 2>/dev/null
  echo "Abbruch gesendet"
}

cmd_status() {
  load_conf "$1"
  make_ssh_wrapper
  trap 'rm -f "$SSHW"' EXIT
  on_pi 'echo "hostname=$(hostname)"
    echo "model=$(tr -d "\0" < /proc/device-tree/model 2>/dev/null)"
    . /etc/os-release; echo "os=$PRETTY_NAME"
    echo "kernel=$(uname -r)"
    echo "uptime=$(uptime -p 2>/dev/null)"
    echo "load=$(cut -d" " -f1-3 /proc/loadavg)"
    echo "disk=$(df -h --output=used,size,pcent / | tail -1 | xargs)"
    echo "mem=$(free -m | awk "/^Mem:/{print \$3\" / \"\$2\" MiB\"}")"
    t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null) && echo "temp=$((t/1000)) °C"
    echo "ip=$(hostname -I | cut -d" " -f1)"
    sudo -n true 2>/dev/null && echo "sudo=ok" || echo "sudo=fehlt"
    command -v rsync >/dev/null && echo "rsync=ok" || echo "rsync=fehlt"
    [ -f /var/run/reboot-required ] && echo "reboot=ja" || echo "reboot=nein"'
}

cmd_images() {
  load_conf "$1"
  local f
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    local info=""
    [ -f "$f.info" ] && info=$(tr '\n' '\036' < "$f.info")
    printf '%s\t%s\t%s\t%s\n' "$(basename "$f")" "$(stat -c %s "$f")" "$(stat -c %Y "$f")" "$info"
  done < <(ls -1t "$PIDIR/${NAME}_"*.img "$PIDIR/${NAME}_"*.img.xz "$PIDIR/${NAME}_"*.img.gz 2>/dev/null)
}

cmd_keygen() {
  load_conf "$1"
  mkdir -p "$(dirname "$KEY")"
  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C "pi-wartung:$NAME" -f "$KEY" || exit 1
  cat "$KEY.pub"
}

# Reads the password from stdin. It is used once and never stored or logged.
cmd_install_key() {
  load_conf "$1"
  command -v sshpass >/dev/null || { echo "sshpass fehlt auf dem Unraid" >&2; exit 1; }
  local pub script rc
  pub=$(cmd_keygen "$PIDIR") || { echo "Schlüssel konnte nicht erzeugt werden" >&2; exit 1; }
  IFS= read -r SSHPASS
  export SSHPASS
  script="umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF $(printf %q "$pub") ~/.ssh/authorized_keys || echo $(printf %q "$pub") >> ~/.ssh/authorized_keys"
  sshpass -e ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR \
    -o UserKnownHostsFile="$STATE/known_hosts" -o PubkeyAuthentication=no \
    -o PreferredAuthentications=password,keyboard-interactive -o NumberOfPasswordPrompts=1 \
    -p "$PORT" "$USER_NAME@$HOST" "$script"
  rc=$?
  unset SSHPASS
  case "$rc" in
    0) ;;
    5) echo "Benutzername oder Passwort ist falsch" >&2; exit 1 ;;
    6) echo "Der Host-Schlüssel des Pi wurde nicht akzeptiert" >&2; exit 1 ;;
    *) echo "Anmeldung am Pi fehlgeschlagen (Code $rc). Ist die Passwort-Anmeldung dort erlaubt?" >&2; exit 1 ;;
  esac
  make_ssh_wrapper
  trap 'rm -f "$SSHW"' EXIT
  on_pi true || { echo "Schlüssel übertragen, aber die Anmeldung damit klappt nicht" >&2; exit 1; }
  on_pi 'sudo -n true' 2>/dev/null && echo "sudo=ok" || echo "sudo=fehlt"
}

cmd_due() {
  local root=${1%/} conf now hour age need
  now=$(date +%s); hour=$((10#$(date +%H)))
  while IFS= read -r conf; do
    (
      load_conf "$(dirname "$conf")"
      case "$SCHEDULE" in
        daily)   need=$((20 * 3600)) ;;
        weekly)  need=$((156 * 3600)) ;;
        monthly) need=$((27 * 86400)) ;;
        *) exit 0 ;;
      esac
      [ "$hour" -ge "$HOUR" ] || exit 0
      LAST_OK_TIME=0
      [ -f "$STATE/last" ] && . "$STATE/last"
      age=$((now - ${LAST_OK_TIME:-0}))
      [ "$age" -ge "$need" ] || exit 0
      echo "Geplante Sicherung: $NAME"
      bash "$SCRIPT" backup "$PIDIR" >"$PIDIR/logs/$(date +%Y-%m-%d_%H%M%S).log" 2>&1
    )
  done < <(find "$root" -mindepth 2 -name pi.conf -not -path '*/.state/*' -not -path '*/.engine/*')
}

case "${1:-}" in
  backup|start|state|cancel|status|images|keygen) c=$1; shift; "cmd_$c" "$@" ;;
  install-key) shift; cmd_install_key "$@" ;;
  due) shift; exec 8>/tmp/pi-wartung-due.lock; flock -n 8 || exit 0; cmd_due "$@" ;;
  version) echo "$ENGINE_VERSION" ;;
  *) sed -n '2,14p' "$SCRIPT"; exit 2 ;;
esac
