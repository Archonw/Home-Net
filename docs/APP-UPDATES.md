# Android-App installieren und aktuell halten

Home-Net stellt die Android-App selbst zum Herunterladen bereit, auch ohne den Netzwerkmonitor. Damit
braucht das Handy keinen Zugang zu GitHub.

```
GitHub-Release ──(jede Stunde)──▶ Home-Net (Zugang) ──▶ https://<home-net>/app ──▶ App (oder Browser beim ersten Mal)
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

**Erste Installation:** Die Seite `/app` (z. B. `https://home.example.de/app` oder `http://192.168.178.20:8093/app`) ist
ohne Anmeldung erreichbar und bietet die APK zum Herunterladen an. Sie enthält nur die App, keine Zugangsdaten oder
Gerätedaten. Alte Adressen unter `/m/netzwerkmonitor/app` funktionieren weiter.

**Updates:** Die App fragt beim Start Home-Net, ob dort eine neuere Version liegt, und bietet „Installieren“ an. Sie lädt
die APK selbst und übergibt sie dem Android-Installer. Beim ersten Mal muss man ihr in den Android-Einstellungen erlauben,
Apps zu installieren, und das Update einmal bestätigen. Danach spielt Android ab Version 12 weitere Updates meist ohne
Rückfrage ein. Eine zusätzliche App wie Obtainium ist nicht nötig. Wer sie trotzdem nutzt, trägt `https://<home-net>/app`
als Quelle ein (Version Extraction: `netzwerkmonitor-(.+)\.apk`, Match Group `1`).

## Andere Haushalte

Jede Installation holt sich neue Versionen selbst von GitHub, so kommt ein Update dort an, ohne dass jemand etwas
tun muss. Alle APKs sind mit demselben Schlüssel signiert, deshalb lässt sich jede neue Version über die alte
installieren, egal ob sie aus GitHub oder von Home-Net kommt.

## Eigener Fork

Wer Home-Net forkt und selbst weiterentwickelt, bekommt Programm, Container und App aus dem eigenen Repository:

- **Installieren:** `HOME_NET_REPO=<besitzer>/<repo> python3 home-net install` oder `home-net install --repo <besitzer>/<repo>`.
  Das Repository steht danach als `repo` in `home-net.json`; `home-net update` lädt Programm und Versionen von dort, im
  Kanal `stabil` die Container von `ghcr.io/<besitzer>/<repo>`, und der Zugang sucht dort nach der App.
- **Container:** `.github/workflows/release.yml` veröffentlicht unter dem Namen des Repositorys, in dem es läuft.
- **App:** Ohne eigenen Signierschlüssel (Secrets `NETMON_KEYSTORE_*`) baut der Workflow keine Releases. Eine selbst
  signierte App lässt sich nicht über die offizielle installieren und umgekehrt; vorher die eine deinstallieren.
  Der Link „Quellcode auf GitHub“ in der App zeigt auf das Repository, aus dem sie gebaut wurde.

## App-Stores

Beschreibung, Symbol und Bildschirmfotos für F-Droid und IzzyOnDroid liegen unter `fastlane/metadata/android/`
(deutsch und englisch). IzzyOnDroid nimmt die APK direkt aus den GitHub-Releases `app-<Nummer>`; die Aufnahme beantragt
der Besitzer des Repositorys dort selbst.
