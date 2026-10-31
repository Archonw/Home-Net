# Home-Net

Ein Baukasten für Dienste im Heimnetz, lauffähig auf jedem Linux mit Docker. Du wählst bei der Installation oder später,
welche Module du nutzt. Der Plan steht in [docs/PLAN.md](docs/PLAN.md).

| Ordner | Inhalt |
| --- | --- |
| `android/` | Android-App, zeigt eine Kachel je Modul |
| `modules/netzwerkmonitor/` | Überwacht die Geräte im Netz |
| `modules/pi-wartung/` | Sichert Raspberry Pis als Images, die sich auf SD-Karten schreiben lassen |
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

## Eine weitere App hinzufügen

Ein Modul bekommt seinen Ordner unter `modules/`, in der App eine Kachel in `HomeScreen`, einen Eintrag in `Screen` und seine Adresse in `Settings`.

## Lizenz

[Apache License 2.0](LICENSE)
