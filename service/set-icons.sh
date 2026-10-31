#!/bin/bash
# Gives the Home-Net containers their logos in Unraid. Run this on the PC, from the repository.
# Unraid reads the logo from a container label, and labels only change when a container is
# recreated. relabel.py keeps all other settings and goes back to the old container on failure.
set -eu
UNRAID=${1:-unraid}
BASE=https://raw.githubusercontent.com/Archonw/Home-Net/main/assets/icons
NTFY=https://raw.githubusercontent.com/binwiederhier/ntfy/main/docs/static/img/ntfy.png
cd "$(dirname "$0")"
scp -q relabel.py "$UNRAID:/tmp/piw-relabel.py"
ssh "$UNRAID" "python3 /tmp/piw-relabel.py ntfy $NTFY; \
  python3 /tmp/piw-relabel.py pi-wartung-service $BASE/pi-wartung.png; \
  python3 /tmp/piw-relabel.py networkmonitor $BASE/netzwerk.png; \
  rm -f /tmp/piw-relabel.py; \
  python3 - <<PY
import json
f = '/boot/config/plugins/folder.view2/docker.json'
d = json.load(open(f))
for v in d.values():
    if v.get('name') == 'Home-Net':
        v['icon'] = '$BASE/home-net.png'
json.dump(d, open(f, 'w'), indent=4)
PY
sleep 20; docker ps --format '{{.Names}}: {{.Status}}' | grep -E '^(ntfy|networkmonitor|pi-wartung-service):'"
echo "Fertig. Lade die Docker-Seite in Unraid neu (Strg+F5)."
