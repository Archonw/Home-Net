# Home-Net

Ein Baukasten für Dienste im Heimnetz, lauffähig auf jedem Linux mit Docker. Du wählst bei der Installation oder später,
welche Module du nutzt. Der Plan steht in [docs/PLAN.md](docs/PLAN.md).

## Installieren

Auf einem Linux mit Docker und Compose:

```bash
git clone https://github.com/Archonw/Home-Net.git
cd Home-Net
./home-net install
```

Das Programm fragt nach Datenordner, Modulen und Passwort. Danach läuft alles unter `http://<rechner>:8093`, mit einer Anmeldung für alle Module.

| Befehl | Wirkung |
| --- | --- |
| `./home-net modules` | Zeigt die verfügbaren Module |
| `./home-net add <modul>` / `remove <modul>` | Modul ergänzen oder stoppen, seine Daten bleiben |
| `./home-net status` | Zeigt, was läuft |
| `./home-net update` | Holt die neue Version und startet neu |
| `./home-net password` | Ändert das Passwort |
| `./home-net backup` / `restore <archiv>` | Sichert Einstellungen und Geheimnisse oder spielt sie zurück |

## Aufbau

| Ordner | Inhalt |
| --- | --- |
| `core/` | Installationsprogramm und gemeinsamer Zugang |
| `android/` | Android-App, zeigt eine Kachel je Modul |
| `modules/netzwerkmonitor/` | Überwacht die Geräte im Netz |
| `modules/pi-wartung/` | Sichert Raspberry Pis als Images, die sich auf SD-Karten schreiben lassen |
| `modules/ntfy/` | Eigener Server für Benachrichtigungen |
| `tools/` | Hilfsprogramme, zum Beispiel die Sicherung der Einstellungen |
| `assets/icons/` | Logos |
| `docs/` | Plan und Anleitung zur Neuinstallation |

## Android-App: Installation und Updates

GitHub Actions baut bei jedem Push die APK. Nach einem Push auf `main` liegt sie unter **Releases** als
`netzwerkmonitor.apk`. Der Dateiname bleibt, weil der Monitor neue Versionen darüber erkennt und ans Handy weitergibt.

Damit Updates ankommen, muss im Networkmonitor unter *Einstellungen* das Repository für App-Updates auf
`Archonw/Home-Net` stehen. Die Versionsnummern laufen oberhalb der alten Releases weiter.

Signiert wird mit den Secrets `NETMON_KEYSTORE_BASE64`, `NETMON_KEYSTORE_PASSWORD`, `NETMON_KEY_ALIAS` und
`NETMON_KEY_PASSWORD`.

## Logos

In `assets/icons` liegen die Logos als SVG und PNG: `home-net` für die App und die Gruppe auf dem Unraid, `netzwerk` und
`pi-wartung` für die beiden Dienste. Die Container auf dem Unraid laden sie von hier.

## Ein Modul hinzufügen

Ein Modul ist ein Ordner unter `modules/` mit einer `module.json`: Name, Logo, Container, Rechte und Schnittstelle. Der Kern
baut daraus die Installation und den Zugang. Für die App kommt eine Kachel in `HomeScreen` dazu.

## Lizenz

[Apache License 2.0](LICENSE)
