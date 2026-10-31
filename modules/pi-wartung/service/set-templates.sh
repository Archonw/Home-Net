#!/bin/bash
# Gives the Home-Net containers Unraid templates, so that the autostart switch works.
# Run this on the PC, from the repository. Add --dry-run to only show the templates; secrets stay hidden then.
# Note: the template of networkmonitor holds its passwords in plain text on the flash drive.
set -eu
UNRAID=unraid
BASE=https://raw.githubusercontent.com/Archonw/Home-Net/main/assets/icons
NTFY=https://raw.githubusercontent.com/binwiederhier/ntfy/main/docs/static/img/ntfy.png
cd "$(dirname "$0")"
scp -q make_templates.py "$UNRAID:/tmp/piw-templates.py"
ssh "$UNRAID" "python3 /tmp/piw-templates.py ${1:-} \
  ntfy $NTFY http://[IP]:[PORT:80]/ \
  pi-wartung-service $BASE/pi-wartung.png '' \
  networkmonitor $BASE/netzwerk.png http://[IP]:8085/; \
  rm -f /tmp/piw-templates.py"
