# Mitmachen

Danke, dass du Home-Net verbessern willst. Fehler und Wünsche gehören in die **Issues**, Änderungen als **Pull Request**
gegen `main`. Sicherheitslücken bitte nicht öffentlich, sondern wie in [SECURITY.md](SECURITY.md) beschrieben melden.

*English:* Issues and pull requests in English are welcome. The code and the user-facing texts are German first; every
visible text needs an English translation in `i18n/en/` (see below). Run the checks listed here before opening a PR.

## Aufbau in Kürze

| Ordner | Inhalt |
| --- | --- |
| `core/home_net.py` | Das Programm `home-net`: Installation, Module, Updates, Compose-Datei |
| `core/gateway/` | Der Zugang: Anmeldung, Konten, Passkeys, Meldungen, App-Updates, Weiterleitung zu den Modulen |
| `modules/<id>/` | Ein Dienst je Ordner, beschrieben durch `module.json` ([Anleitung](docs/MODULE.md)) |
| `android/` | Die App (Kotlin, Jetpack Compose), eine Kachel je Modul |
| `i18n/<sprache>/` | Übersetzungen, der deutsche Text ist der Schlüssel |
| `tests/` | Tests des Kerns und des Zugangs |
| `tools/` | Release-Images, Probelauf und Bildschirmfotos der App im Emulator |

Wie die Teile zusammenhängen, steht in [CLAUDE.md](CLAUDE.md) (die Notizen für Entwickler, auch für KI-Assistenten).

## Ausprobieren

```bash
git clone https://github.com/<du>/Home-Net.git
cd Home-Net
./home-net install --repo <du>/Home-Net
./home-net kanal main      # Container aus deinem Code bauen statt fertige Images zu laden
```

Nach einer Änderung baut `./home-net update` neu. Ein Testrechner oder eine VM ist besser als das eigene Heimnetz.

## Prüfungen vor dem Pull Request

GitHub Actions führt dieselben Prüfungen aus; nur die Module, die du änderst, und immer der Kern.

```bash
python3 -m unittest discover tests                       # Kern, Zugang, Übersetzungen
cd modules/<id> && python3 -m unittest discover tests     # die meisten Module
cd modules/netzwerkmonitor && pip install -e '.[dev]' && pytest && ruff check . && ruff format --check .
cd android && ./gradlew assembleDebug                     # App
```

Ändert sich die App, den Zugang oder eine Übersetzung, startet der Workflow **App-Probelauf** die App im Emulator gegen
einen echten Zugang. Er muss grün sein, bevor die Änderung auf `main` kommt, denn jeder Merge auf `main`, der `android/`
ändert, geht als Update an alle Handys.

## Regeln

- **Schnittstelle und App zusammen:** Ändert sich die Schnittstelle eines Moduls, wird die App im selben Pull Request
  angepasst. Die App muss mit älteren Servern zurechtkommen (fehlende Felder haben einen sinnvollen Standard).
- **Übersetzungen:** Jeder neue sichtbare Text kommt mit seiner englischen Übersetzung in denselben Commit
  (`i18n/en/<bereich>.json`, Schlüssel ist der deutsche Text). Zusammengesetzte Texte nur als Vorlage mit Platzhaltern:
  in der App `t("… {0} …", wert)`, im Web `window.hnT`. `tests/test_core.py` prüft, dass Platzhalter passen und dass
  jede Beschreibung und jede Meldung aus `module.json` übersetzt ist.
- **Rechte:** Module verlassen sich auf die Kopfzeilen `X-Home-Net-User`, `X-Home-Net-Role` und
  `X-Home-Net-Categories` des Zugangs und wenden die Rechte selbst an. Ein Modul ist nie direkt erreichbar, nur über
  den Zugang.
- **Keine Geheimnisse und keine persönlichen Daten** im Repository: keine Passwörter, Tokens, echten Adressen oder
  Namen. Beispiele mit `example.de`, `192.168.1.x` und erfundenen Geräten.
- **Nichts fest auf ein Repository schreiben:** Downloads, Images und Links folgen `repo` in `home-net.json`
  (`set_repo` in `core/home_net.py`). Forks funktionieren so ohne Änderung.
- **App-Kennung und Signierschlüssel** (`de.archonw.netmon`) bleiben unverändert, sonst kommen keine Updates mehr an.
  Forks mit eigenem Schlüssel siehe [docs/APP-UPDATES.md](docs/APP-UPDATES.md#eigener-fork).

## Versionen

Releases heißen `vX.Y.Z` und entstehen über **Actions → Release** (baut alle Container für amd64 und arm64 nach
`ghcr.io/<repo>`). Die App-Releases `app-NNN` entstehen automatisch bei jedem Merge auf `main`, der die App ändert.

## Lizenz

Mit einem Pull Request stimmst du zu, dass dein Beitrag unter der [Apache License 2.0](LICENSE) steht.
