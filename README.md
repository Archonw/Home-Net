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

## Konten und Anmeldung

Jeder meldet sich mit Benutzername und Passwort an. Im Heimnetz reicht das, von außen, über einen Reverse Proxy mit HTTPS, gehört ein Passkey dazu.

- **Rollen:** Der Eigentümer darf alles. Admins verwalten Benutzer. Benutzer sehen nur die Dienste und Pi-Kategorien, die ihnen freigegeben sind.
- **Passkey einrichten:** Unter **Benutzer** die Adresse für unterwegs speichern, unter **Mein Konto** einen Code holen und auf dieser Adresse `/login` öffnen. Die Wiederherstellungscodes aufbewahren.
- **Benachrichtigungen:** Jedes Konto wählt unter **Mein Konto** oder in der App unter **Einstellungen → Benachrichtigungen**, welche Meldungen es möchte. Es bekommt nur Meldungen zu Diensten und Pi-Kategorien, die es sehen darf. Die gekoppelte App empfängt sie über eine Verbindung im Hintergrund direkt vom eigenen Server, ohne Dienste von Google; ntfy ist dafür nicht nötig.
- **App koppeln:** Unter **Mein Konto** einen Kopplungscode holen und in der App unter **Einstellungen → Zugang** eintragen. Geräte lassen sich einzeln entfernen.
- **Protokoll:** Anmeldungen und Änderungen stehen unter **Protokoll**.
- **Passwort vergessen:** `./home-net password <konto>` auf dem Server.

Der Port von Home-Net darf nicht direkt ins Internet weitergeleitet werden, sondern nur über den Proxy.

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
