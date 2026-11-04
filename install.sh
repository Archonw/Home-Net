#!/bin/sh
# Home-Net mit einer Zeile installieren:
#
#   curl -fsSL https://raw.githubusercontent.com/Archonw/Home-Net/main/install.sh | sh
#
# Lädt das Programm nach <ordner>/repo und startet `home-net install` mit den Daten in <ordner>/data. Der Ordner ist auf
# Unraid /mnt/user/appdata/home-net, als root sonst /opt/home-net, für andere Benutzer ~/home-net (HOME_NET_DIR ändert
# ihn). Ein eigener Fork: HOME_NET_REPO=<besitzer>/<repo>. Weitere Angaben gehen an `home-net install`, z. B.
#   curl -fsSL …/install.sh | sh -s -- --modules netzwerkmonitor,pihole --owner anna
# Ist Home-Net schon installiert, holt das Skript stattdessen die neue Version (`home-net update`).
set -eu

REPO="${HOME_NET_REPO:-Archonw/Home-Net}"

say() { printf '%s\n' "$*"; }
fail() { say "Home-Net: $*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "Python 3 fehlt. Bitte zuerst installieren (z. B. apt install python3)."
command -v docker >/dev/null 2>&1 || fail "Docker fehlt. Anleitung: https://docs.docker.com/engine/install/"
if ! docker compose version >/dev/null 2>&1; then
  [ -f /etc/unraid-version ] && fail "Docker Compose fehlt. Auf Unraid unter Apps das Plugin „Docker Compose Manager“ installieren."
  fail "Docker Compose fehlt (Paket docker-compose-plugin)."
fi
command -v tar >/dev/null 2>&1 || fail "tar fehlt."

if [ -n "${HOME_NET_DIR:-}" ]; then
  BASE="$HOME_NET_DIR"
elif [ -f /etc/unraid-version ]; then
  BASE=/mnt/user/appdata/home-net
elif [ "$(id -u)" = 0 ]; then
  BASE=/opt/home-net
else
  BASE="$HOME/home-net"
fi

if [ -f "$BASE/data/home-net.json" ] && [ -x "$BASE/repo/home-net" ]; then
  say "Home-Net ist in $BASE schon installiert, hole die neue Version ..."
  exec python3 "$BASE/repo/home-net" update
fi

SOURCE="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
say "Lade Home-Net aus $REPO nach $BASE/repo ..."
mkdir -p "$BASE"
rm -rf "$BASE/repo.neu"
mkdir "$BASE/repo.neu"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$SOURCE" | tar -xz -C "$BASE/repo.neu" --strip-components 1
elif command -v wget >/dev/null 2>&1; then
  wget -qO- "$SOURCE" | tar -xz -C "$BASE/repo.neu" --strip-components 1
else
  fail "curl oder wget fehlt."
fi
[ -f "$BASE/repo.neu/home-net" ] || fail "Der Download enthält kein Home-Net."
rm -rf "$BASE/repo"
mv "$BASE/repo.neu" "$BASE/repo"

# Über `curl | sh` ist die Eingabe das Skript selbst; die Fragen kommen darum vom Terminal
if [ -t 0 ] || ! sh -c ": </dev/tty" 2>/dev/null; then
  exec python3 "$BASE/repo/home-net" install --data "$BASE/data" --repo "$REPO" "$@"
fi
exec python3 "$BASE/repo/home-net" install --data "$BASE/data" --repo "$REPO" "$@" </dev/tty
