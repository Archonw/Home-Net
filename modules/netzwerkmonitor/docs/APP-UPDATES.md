# Android-App installieren und aktuell halten

Der Monitor stellt die Android-App selbst zum Herunterladen bereit. Damit braucht das Handy keinen
Zugang zu GitHub.

```
GitHub-Release ──(alle 6 h)──▶ Monitor /data/app ──▶ https://<monitor>/app ──▶ Obtainium / Browser
```

## Woher der Monitor die App bekommt

- **Automatisch aus GitHub:** Jeder Merge auf `main` baut eine neue APK und legt sie als Release
  `app-<Nummer>` ab. Der Monitor schaut beim Start und dann alle 6 Stunden nach und lädt eine neuere
  Version nach `/data/app/netzwerkmonitor-0.1.<Nummer>.apk`. Ältere Versionen löscht er.
  Einstellungen → *Android-App*:
  - **GitHub-Repository:** Standard `Archonw/Home-Net`, leer = nicht automatisch suchen.
  - **GitHub-Token:** nur nötig, solange das Repository privat ist. Auf GitHub unter
    *Settings → Developer settings → Fine-grained tokens* ein Token nur für dieses Repository mit
    *Contents: Read-only* anlegen.
  - **Jetzt nach Update suchen** prüft sofort.
- **Von Hand:** Eine APK als `netzwerkmonitor-<version>.apk` in den App-Ordner legen (auf Unraid
  z. B. `/mnt/user/appdata/networkmonitor/data/app/`, allgemein `app/` im Ordner, der
  als `/data` eingebunden ist). Die höchste Version wird angeboten.

## Auf dem Handy

Die Seite `/app` (z. B. `https://netmon.example.de/app` oder `http://192.168.178.20:8085/app`) ist
ohne Passwort erreichbar, damit Obtainium sie abfragen kann. Sie enthält nur die App, keine
Zugangsdaten oder Gerätedaten.

**Obtainium:** *App hinzufügen* → URL `https://<monitor>/app` eintragen. Obtainium erkennt die Seite
als HTML-Quelle, findet den Link auf die APK und meldet eine neue Version, sobald der Monitor eine
neuere bereitstellt. Für eine schöne Versionsanzeige unter *Version Extraction* den Ausdruck
`netzwerkmonitor-(.+)\.apk` mit *Match Group* `1` eintragen; dann passt die Version genau zu der in der App.

**Ohne Obtainium:** Die App fragt beim Öffnen der Geräteliste ihren eigenen Monitor, ob dort eine
neuere Version liegt, und bietet „Installieren“ an. Sie lädt die APK selbst und übergibt sie dem
Android-Installer. Beim ersten Mal muss man ihr in den Android-Einstellungen erlauben, Apps zu
installieren, und das Update einmal bestätigen. Hat sich die App einmal selbst aktualisiert, spielt
Android ab Version 12 weitere Updates meist ohne Rückfrage ein (solange Obtainium nicht zuletzt
installiert hat).

## Andere Haushalte

Jede Installation hat ihren eigenen Monitor. In der App trägt man dessen Adresse ein, und auch
Obtainium zeigt auf diesen eigenen Monitor. Jeder Monitor holt sich neue Versionen selbst von GitHub,
so kommt ein Update dort an, ohne dass jemand etwas tun muss. Voraussetzung: Der Monitor kann das
Repository lesen, also entweder ist es öffentlich oder dort ist ein Token eingetragen.

Alle APKs sind mit demselben Schlüssel signiert, deshalb lässt sich jede neue Version über die alte
installieren, egal ob sie aus GitHub, vom Monitor oder über Obtainium kommt.
