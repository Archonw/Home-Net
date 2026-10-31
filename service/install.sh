#!/bin/bash
# Installs the phone service on the Unraid server. Run this on the PC, from the repository.
set -eu
UNRAID=${1:-unraid}
HOST=$(ssh -G "$UNRAID" | awk '/^hostname /{print $2}')
ROOT=/mnt/user/backup1/Raspis/PiWartung
CONFIG=/mnt/user/appdata/pi-wartung
PORT=${PORT:-8092}
cd "$(dirname "$0")/.."

ssh "$UNRAID" "mkdir -p $CONFIG && chmod 700 $CONFIG && { [ -f $CONFIG/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C pi-wartung-service -f $CONFIG/id_ed25519; }"
PUB=$(ssh "$UNRAID" "cat $CONFIG/id_ed25519.pub")
LINE="command=\"/bin/bash $ROOT/.engine/pi-backup.sh api\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding $PUB"
ssh "$UNRAID" "grep -qF '$PUB' /root/.ssh/authorized_keys || printf '%s\n' '$LINE' >> /root/.ssh/authorized_keys"

tar czf - piwartung/__init__.py piwartung/core.py service/server.py service/Dockerfile |
  ssh "$UNRAID" "docker build -q -t pi-wartung-service:local -f service/Dockerfile -"
ssh "$UNRAID" "docker rm -f pi-wartung-service >/dev/null 2>&1; docker run -d --name pi-wartung-service --restart unless-stopped \
  --label net.unraid.docker.managed=dockerman --label net.unraid.docker.icon=https://raw.githubusercontent.com/Archonw/Home-Net/main/assets/icons/pi-wartung.png \
  -p $PORT:8080 -v $CONFIG:/config -e UNRAID_HOST=$HOST -e BACKUP_ROOT=$ROOT pi-wartung-service:local"
echo "Dienst läuft auf http://$HOST:$PORT. Das Passwort legst du in der App unter Einstellungen fest."
