# Zusammenhänge

Home-Net ist ein Baukasten: `android/` ist die App, `modules/` enthält die Dienste. Der Plan steht in `docs/PLAN.md`.

- `modules/netzwerkmonitor`: REST-API aus `src/networkmonitor/api.py`, genutzt in `android/.../Api.kt`.
- `modules/pi-wartung`: Dienst aus `service/server.py`, genutzt in `android/.../PiApi.kt` und `PiScreens.kt`.
- `modules/pc-sicherung`: Dienst aus `service/server.py`, genutzt in `android/.../PcApi.kt` und `PcScreens.kt` (nur über den
  Zugang; PCs anlegen und Einstellungen bleiben in der Weboberfläche). Das
  Sicherungssystem für die PCs (`live/`) wird im Dockerfile gebaut; ändert sich die Schnittstelle `/live/*`, müssen
  `live/hn-agent` und `service/server.py` zusammen geändert werden. Der Dienst läuft im Host-Netz, weil er für den
  Netzwerkstart Proxy-DHCP und TFTP anbietet; das Archiv `PC_DIR` legt das Installationsprogramm fest.
- `modules/unraid`: Dienst aus `service/server.py`, der Zugriff auf Unraid in `service/host.py`. Nur auf einem Unraid-Server
  (`requires` in `module.json`, geprüft von `home-net add`). Änderungen schickt es als die Formulare der Unraid-Oberfläche an
  den Socket von emhttpd; die Feldnamen stammen aus `emhttp/plugins/dynamix/*.page` im Repo `unraid/webgui`. Neue
  Unraid-Versionen erst prüfen, dann in `TESTED` eintragen. Docker und VMs über die offizielle API mit API-Schlüssel.
  User Scripts in `service/scripts.py`: Starten, Abbrechen und Zeitplan gehen über das Skript „Home-Net Starter“, das der
  Dienst in User Scripts ablegt; ändert sich dessen Auftragsformat, `STARTER_VERSION` erhöhen.
  Genutzt in `android/.../UnraidApi.kt` und `UnraidScreens.kt` (Übersicht, Freigaben, Benutzer, Docker/VMs, User Scripts; Dateien und
  Einstellungen bleiben in der Weboberfläche).

Ändert sich eine Schnittstelle, wird die App im selben Commit mit angepasst.

Ein Push auf `main`, der `android/` ändert, erzeugt das Release `app-<200 + Laufnummer>` mit `netzwerkmonitor.apk`, und der
Monitor gibt es ans Handy weiter. Paketkennung `de.archonw.netmon` und Signierschlüssel dürfen sich nicht ändern. Wird `android.yml` umbenannt, beginnt die Laufnummer neu: dann den Sockel erhöhen, sonst kommen keine Updates mehr an.

Der Kern (`core/`) baut aus den `module.json` der Module die Compose-Datei und die Liste für den Zugang. Die Sicherung von
Pi-Wartung läuft im Container `pi-wartung-engine`, der Dienst `pi-wartung-service` spricht sie über einen Socket an. Nur die
Sicherung hat erhöhte Rechte. Der Ordner `modules/pi-wartung/piwartung` enthält die alte GTK-App, sie wird vom Dienst nur noch
wegen `core.py` gebraucht.

Der Zugang erkennt Zugriffe von außen am Header `X-Forwarded-For` oder an einer öffentlichen Absenderadresse. Konten, Passkeys und Geräte liegen in `security.json` im Gateway-Ordner, das Protokoll in `audit.jsonl`. Module erfahren Benutzer, Rolle und erlaubte Kategorien über die Header `X-Home-Net-*`, die nur der Zugang setzt; Module müssen diese Rechte selbst anwenden. Module dürfen darum nur über den Zugang erreichbar sein. Im Monitor dürfen nur Eigentümer und Admins ändern (`require_manager`), Benutzer sehen nur an. Ein Modul im Host-Netz wie der Monitor nennt in `module.json` dafür `api.socket` statt eines Ports: Es lauscht auf einer Socket-Datei, deren Ordner sich der Zugang mit ihm teilt. Ohne `LISTEN_SOCKET` öffnet der Monitor wie zuvor Port 8085, das gilt nur für den Betrieb allein.

Meldungen: Ein Modul nennt in `module.json` unter `events` seine Arten und legt je Meldung eine JSON-Datei in seinem Ordner unter `${DATA_DIR}/events/<modul>` ab. Der Zugang (`core/gateway/events.py`) übernimmt sie, prüft Wahl und Rechte beim Lesen und liefert sie über `/auth/stream` an die App. Die Prüfung `App-Probelauf` startet die App auf Zweigen im Emulator gegen den echten Zugang; Änderungen an der App gehören erst danach auf `main`.

Das Repo ist öffentlich: keine Passwörter, Tokens oder persönlichen Adressen einchecken.
