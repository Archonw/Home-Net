# Android-App installieren und aktuell halten

Home-Net stellt die Android-App selbst zum Herunterladen bereit, auch ohne den Netzwerkmonitor. Damit
braucht das Handy keinen Zugang zu GitHub.

```
GitHub-Release ──(jede Stunde)──▶ Home-Net (Zugang) ──▶ https://<home-net>/app ──▶ App / Obtainium / Browser
```

## Woher Home-Net die App bekommt

- **Automatisch aus GitHub:** Jeder Merge auf `main`, der die App ändert, baut eine neue APK und legt sie als
  Release `app-<Nummer>` ab. Der Zugang schaut beim Start und dann jede Stunde nach und lädt eine neuere Version
  nach `gateway/app/netzwerkmonitor-0.1.<Nummer>.apk` im Datenordner von Home-Net. Ältere Versionen löscht er.
  Auf der Home-Net-Seite unter *Einstellungen → Android-App* (nur Eigentümer und Admins):
  - **GitHub-Repository:** Standard `Archonw/Home-Net`, leer = nicht automatisch suchen.
  - **GitHub-Token:** freiwillig. Es hebt das Abfrage-Limit von 60 auf 5000 pro Stunde; bei einem privaten
    Repository ist es Pflicht. Auf GitHub unter *Settings → Developer settings → Fine-grained tokens* ein Token
    nur für dieses Repository mit *Contents: Read-only* anlegen.
  - **Jetzt nach Update suchen** prüft sofort. In der App geht das unter Einstellungen → „Nach Update suchen“.
- **Von Hand:** Eine APK als `netzwerkmonitor-<version>.apk` in `gateway/app/` im Datenordner legen. Die höchste
  Version wird angeboten.

Repository und Token stehen in `gateway/app.json`. Bei Installationen von vor dieser Änderung übernimmt
`home-net update` sie einmal aus den Einstellungen des Netzwerkmonitors.

## Auf dem Handy

Die Seite `/app` (z. B. `https://home.example.de/app` oder `http://192.168.178.20:8093/app`) ist ohne Anmeldung
erreichbar, damit Obtainium sie abfragen kann. Sie enthält nur die App, keine Zugangsdaten oder Gerätedaten.
Alte Adressen unter `/m/netzwerkmonitor/app` funktionieren weiter.

**Obtainium:** *App hinzufügen* → URL `https://<home-net>/app` eintragen. Obtainium erkennt die Seite als
HTML-Quelle, findet den Link auf die APK und meldet eine neue Version, sobald Home-Net eine neuere bereitstellt.
Für eine schöne Versionsanzeige unter *Version Extraction* den Ausdruck `netzwerkmonitor-(.+)\.apk` mit
*Match Group* `1` eintragen; dann passt die Version genau zu der in der App.

**Ohne Obtainium:** Die App fragt beim Start Home-Net, ob dort eine neuere Version liegt, und bietet
„Installieren“ an. Sie lädt die APK selbst und übergibt sie dem Android-Installer. Beim ersten Mal muss man ihr
in den Android-Einstellungen erlauben, Apps zu installieren, und das Update einmal bestätigen. Hat sich die App
einmal selbst aktualisiert, spielt Android ab Version 12 weitere Updates meist ohne Rückfrage ein (solange
Obtainium nicht zuletzt installiert hat).

## Andere Haushalte

Jede Installation holt sich neue Versionen selbst von GitHub, so kommt ein Update dort an, ohne dass jemand etwas
tun muss. Alle APKs sind mit demselben Schlüssel signiert, deshalb lässt sich jede neue Version über die alte
installieren, egal ob sie aus GitHub, von Home-Net oder über Obtainium kommt.
