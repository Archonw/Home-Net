# Sicherheit / Security

## Eine Lücke melden / Reporting a vulnerability

Bitte **keine öffentlichen Issues** für Sicherheitslücken. Melde sie vertraulich über
**Security → Report a vulnerability** in diesem Repository. Du bekommst innerhalb einer Woche eine Antwort. Ist die Lücke
bestätigt, kommt die Korrektur als neue Version `vX.Y.Z`, und die Meldung wird nach dem Release veröffentlicht.

Please **do not open public issues** for security problems. Report them privately via **Security → Report a
vulnerability** in this repository. You will get an answer within a week. Confirmed issues are fixed in a new release
`vX.Y.Z`; the advisory is published after the release.

## Unterstützte Versionen / Supported versions

Korrekturen gibt es für die jeweils neueste Version im Kanal `stabil` und für `main`. Ältere Versionen bitte mit
`./home-net update` aktualisieren.

Fixes go into the newest release of the `stabil` channel and into `main`. Please update older installations with
`./home-net update`.

## Wie Home-Net geschützt ist / Security model

- **Ein Eingang:** Alle Module sind nur über den Zugang (`core/gateway`) erreichbar. Er prüft die Anmeldung und reicht
  Benutzer, Rolle und erlaubte Kategorien als Kopfzeilen `X-Home-Net-*` an die Module weiter. Module lauschen auf einer
  Socket-Datei oder in einem internen Docker-Netz, nicht auf einem offenen Port.
- **Heimnetz und außen:** Aus dem Heimnetz reicht das Passwort. Von außen (über einen Reverse Proxy oder Tunnel) braucht
  jede Anmeldung zusätzlich einen Passkey. Der Port von Home-Net darf nie direkt ins Internet weitergeleitet werden.
- **Fehlversuche:** Nach 5 falschen Passwörtern ist die Anmeldung für diese Adresse 15 Minuten gesperrt.
- **Geheimnisse** (Passwörter, API-Schlüssel, Signierschlüssel der App) liegen nur im Datenordner der Installation bzw.
  in den Secrets des Repositorys, nie im Code. Das Repository ist öffentlich.
- **Rechte:** Nur der Container `pi-wartung-engine` hat erhöhte Rechte (für das Sichern der SD-Karten). Netzwerkmonitor
  und PC-Sicherung laufen im Host-Netz, weil sie Geräte im Heimnetz finden bzw. den Netzwerkstart anbieten.

In short: one gateway in front of every module, password inside the home network, password plus passkey from outside,
lockout after 5 failed logins, no secrets in the repository. Never forward the Home-Net port to the internet directly.

## Was als Lücke zählt / In scope

Umgehen der Anmeldung oder der Rechte, Zugriff auf Module am Zugang vorbei, Auslesen von Geheimnissen, Ausführen von
Code über die Weboberfläche, die App oder die Schnittstellen der Module.

Authentication or permission bypass, reaching a module without the gateway, leaking secrets, code execution through
the web pages, the app or a module's API.
