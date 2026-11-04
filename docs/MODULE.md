# Ein Modul schreiben

Ein Modul ist ein Ordner `modules/<id>/` mit einer `module.json`, einem `Dockerfile` und dem Dienst. Der Kern baut aus
allen gewählten Modulen die Compose-Datei und die Liste für den Zugang; `./home-net add <id>` nimmt das Modul auf.
Ein kleines, vollständiges Vorbild ist `modules/pihole`.

## module.json

```json
{
  "id": "beispiel",
  "name": "Beispiel",
  "description": "Ein Satz, was das Modul tut",
  "icon": "assets/icons/beispiel.png",
  "api": {
    "socket": "/run/beispiel/api.sock",
    "health": "/health",
    "public": ["/health"],
    "web": true
  },
  "services": {
    "beispiel-dienst": {
      "build": "modules/beispiel",
      "environment": ["TZ=${TZ}", "LISTEN_SOCKET=/run/beispiel/api.sock", "EVENTS_DIR=/run/home-net-events"],
      "volumes": [
        "${DATA_DIR}/beispiel/config:/data",
        "${DATA_DIR}/beispiel/run:/run/beispiel",
        "${DATA_DIR}/events/beispiel:/run/home-net-events"
      ]
    }
  },
  "events": [
    {"id": "beispiel_fehler", "label": "Beispiel meldet einen Fehler"},
    {"id": "beispiel_update", "label": "Update für Beispiel verfügbar", "manager_only": true}
  ]
}
```

| Feld | Bedeutung |
| --- | --- |
| `id` | Ordnername, nur `a-z`, `0-9`, `-`. Der Zugang leitet `/m/<id>/…` an das Modul weiter |
| `name`, `description` | Anzeige auf der Startseite und in der App. Die Beschreibung braucht eine englische Übersetzung in `i18n/en/module.json` |
| `icon` | PNG unter `assets/icons/`, auch das Symbol des Containers auf Unraid |
| `api.socket` | Socket-Datei, auf der der Dienst lauscht. Der Ordner wird mit dem Zugang geteilt (`${DATA_DIR}/<id>/run`). So ist der Dienst nur über den Zugang erreichbar, auch im Host-Netz |
| `api.service` + `api.port` | Statt `socket`: Container und Port im Docker-Netz von Home-Net |
| `api.external_port` | Für fremde Server ohne Anbindung an den Zugang (z. B. `pocket-id-server`): nur ein Link auf diesen Port |
| `api.health` | Pfad, den der Zugang für „läuft“ abfragt, Standard `/health` |
| `api.public` | Pfade ohne Anmeldung, sparsam einsetzen |
| `api.web` | `true`, wenn das Modul eine eigene Weboberfläche hat (Link auf der Startseite) |
| `services` | Container wie in Docker Compose. `build` ist relativ zum Repository; im Kanal `stabil` wird daraus das Image `ghcr.io/<repo>/<container>:<version>`, gebaut von `tools/release_images.py` für amd64 und arm64 |
| `requires` | Optional: `{"path": "…", "message": "…"}` oder `{"arch": ["amd64"]}`. `home-net add` lehnt das Modul ab, wenn es nicht passt |
| `events` | Arten von Meldungen. `manager_only` heißt: nur Eigentümer und Admins können sie wählen. Jedes `label` braucht eine englische Übersetzung |

Platzhalter in `services`: `${DATA_DIR}` (Datenordner der Installation), `${TZ}` und weitere aus der `.env` des Kerns.

## Was der Dienst vom Zugang bekommt

Der Zugang meldet an, prüft, ob das Konto das Modul nutzen darf, und reicht die Anfrage weiter mit:

| Kopfzeile | Inhalt |
| --- | --- |
| `X-Home-Net-User` | Kontoname, Base64 |
| `X-Home-Net-Role` | `owner`, `admin` oder `user` |
| `X-Home-Net-Categories` | JSON-Liste der erlaubten Kategorien (z. B. für Pi-Wartung), Base64 |
| `X-Forwarded-Prefix` | `/m/<id>`, für Links in der Weboberfläche |
| `X-Forwarded-For` | Adresse des Aufrufers |

Die Rechte wendet das Modul selbst an. Üblich: Ansehen darf jeder mit Zugriff, Ändern nur `owner` und `admin`.
Kopfzeilen `X-Home-Net-*` von außen entfernt der Zugang, sie stammen also immer von ihm.

HTML-Seiten des Moduls bekommen vom Zugang `window.HOME_NET_PREFIX`, das Farbschema und die Übersetzung
(`window.hnT`) eingesetzt. Texte im Skript übersetzt du mit
`const tr = text => (window.hnT ? window.hnT(text) : text);`.

## Meldungen ans Handy

Für jede Meldung legt der Dienst eine Datei `*.json` in `EVENTS_DIR` ab (erst unter anderem Namen schreiben, dann
umbenennen):

```json
{"type": "beispiel_fehler", "title": "Beispiel: Fehler", "message": "Was passiert ist", "priority": "default",
 "category": "", "link": ""}
```

Der Zugang übernimmt sie, prüft, wer sie sehen darf und wer sie gewählt hat, und schickt sie an die App. Nur Arten aus
`events` werden angenommen. `priority` ist `default` oder `high`, `category` beschränkt die Meldung auf Konten mit
dieser Kategorie.

## Übersetzungen

Der deutsche Text ist der Schlüssel. Die englischen Texte des Moduls stehen in einer eigenen Datei `i18n/en/<bereich>.json` (z. B. `pihole.json`), Beschreibung und
Meldungen in `i18n/en/module.json`. `python3 -m unittest discover tests` prüft, dass nichts fehlt.

## Tests und CI

Tests liegen in `modules/<id>/tests`. Für ein neues Modul einen Job in `.github/workflows/modules.yml` ergänzen (nach
dem Vorbild von `pihole`), damit Tests und `docker build` bei jeder Änderung laufen.

## In der App

Für die App kommt eine Kachel in `HomeScreen` (`android/.../PiScreens.kt`) dazu, die Schnittstelle in einer eigenen
`<Name>Api.kt`, die Seiten in `<Name>Screens.kt`. Die App muss ohne das Modul und mit älteren Versionen des Moduls
zurechtkommen.
