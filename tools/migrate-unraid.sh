#!/bin/bash
# Moves the running services on the Unraid server to Home-Net. Run this on the PC, from the repository.
set -eu
UNRAID=${1:-unraid}
BASE=/mnt/user/appdata/home-net
REPO=$BASE/repo
DATA=$BASE/data
cd "$(dirname "$0")/.."
read -r -s -p "Neues Passwort für Home-Net (mindestens 8 Zeichen): " PW; echo
read -r -s -p "Nochmal: " PW2; echo
[ "$PW" = "$PW2" ] || { echo "Die Eingaben stimmen nicht überein."; exit 1; }
echo "Einstellungen sichern"
./tools/backup-config.sh "$UNRAID"
echo "Programm kopieren"
ssh "$UNRAID" "mkdir -p $REPO $DATA && chmod 700 $BASE && find $REPO -mindepth 1 -delete"
git archive HEAD | ssh "$UNRAID" "tar xf - -C $REPO"
printf '%s\n' "$PW" | ssh "$UNRAID" "python3 $REPO/tools/migrate_unraid.py $REPO $DATA"
