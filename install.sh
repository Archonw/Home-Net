#!/bin/bash
# Installs Pi-Wartung for the current user, so it runs without the Unraid share mounted.
set -eu
SRC=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/.local/share/pi-wartung"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications"
rm -rf "$DEST/piwartung" "$DEST/engine"
cp -r "$SRC/piwartung" "$SRC/engine" "$SRC/pi-wartung" "$DEST/"
chmod +x "$DEST/pi-wartung"
ln -sf "$DEST/pi-wartung" "$HOME/.local/bin/pi-wartung"
cat > "$HOME/.local/share/applications/pi-wartung.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pi-Wartung
Comment=Raspberry Pis verwalten und als Image sichern
Exec=$DEST/pi-wartung
Icon=$DEST/piwartung/icon.png
Terminal=false
Categories=System;Utility;
EOF
echo "Installiert. Pi-Wartung steht jetzt im Startmenü, oder starte es mit: pi-wartung"
