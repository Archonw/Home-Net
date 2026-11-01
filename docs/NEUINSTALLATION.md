# Neuinstallation

Nach dem Umstieg auf `home-net` reicht `./home-net backup` zum Sichern und auf dem neuen Rechner `./home-net restore <archiv>`.
Die Images liegen getrennt im Sicherungsordner und brauchen eine eigene Kopie außerhalb des Servers.

Der Signierschlüssel `netmon-release.keystore` und sein Passwort müssen sicher aufbewahrt werden, ohne sie gehen Updates der
App nur per Neuinstallation.

## Vor dem Umstieg

`tools/backup-config.sh` auf dem PC ausführen. Es legt die Einstellungen und Geheimnisse als Archiv unter
`/mnt/user/backup1/HomeNet-Konfig` ab und behält die letzten fünf. Images und Spiegel liegen in
`/mnt/user/backup1/Raspis/PiWartung` und sind nicht im Archiv. Beides braucht eine Kopie außerhalb des Servers, sonst
schützt es nicht vor einem Ausfall des Unraid selbst.

### Server wiederherstellen

1. Das Archiv auf dem Unraid als root auspacken: `tar xzf <archiv> -P`. Alle Dateien kommen an ihren alten Ort.
2. `ntfy` und den Netzwerkmonitor über die zurückgeholten Unraid-Vorlagen anlegen. Für den Monitor vorher das Image bauen: in `modules/netzwerkmonitor` `docker build -t networkmonitor:local .`
3. Am PC in `modules/pi-wartung`: `./service/install.sh` und `./service/set-templates.sh`.

