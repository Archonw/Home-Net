# Neuinstallation von Hand

So kommt der heutige Stand nach einem Ausfall wieder hoch. Das Installationsprogramm ersetzt diese Anleitung später.

## Vorher

`tools/backup-config.sh` auf dem PC ausführen. Es legt die Einstellungen und Geheimnisse als Archiv unter
`/mnt/user/backup1/HomeNet-Konfig` ab und behält die letzten fünf. Images und Spiegel liegen in
`/mnt/user/backup1/Raspis/PiWartung` und sind nicht im Archiv. Beides braucht eine Kopie außerhalb des Servers, sonst
schützt es nicht vor einem Ausfall des Unraid selbst.

## Server wiederherstellen

1. Das Archiv auf dem Unraid als root auspacken: `tar xzf <archiv> -P`. Alle Dateien kommen an ihren alten Ort.
2. `ntfy` und den Netzwerkmonitor über die zurückgeholten Unraid-Vorlagen anlegen. Für den Monitor vorher das Image bauen: in `modules/netzwerkmonitor` `docker build -t networkmonitor:local .`
3. Am PC in `modules/pi-wartung`: `./service/install.sh` und `./service/set-templates.sh`.

## PC wiederherstellen

1. `~/.ssh/config` und die Schlüssel zurückholen, darunter ein Eintrag `unraid`.
2. In `modules/pi-wartung` `./install.sh` ausführen.
3. App öffnen und unter Einstellungen die Pfade prüfen.

## Android-App

Installation aus den Releases von `Archonw/Home-Net` oder über `http://<server>:8085/app`. In der App die Adressen und Passwörter eintragen.

Der Signierschlüssel `netmon-release.keystore` und sein Passwort müssen sicher aufbewahrt werden, ohne sie gehen Updates nur per Neuinstallation.
