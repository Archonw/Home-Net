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
- `modules/pocket-id`: Dienst aus `service/server.py`, der Zugriff auf Pocket ID (REST-API mit API-Schlüssel) in
  `service/pocketid.py`. Eine App ist eine Gruppe in Pocket ID, die mindestens einen OIDC-Client freischaltet; die
  Schalter nehmen Benutzer in diese Gruppen auf. Genutzt in `android/.../PocketIdApi.kt` und `PocketIdScreens.kt`.
  `modules/pocket-id-server` installiert Pocket ID selbst; seine Schlüssel erzeugt der Kern (`Install.pocket_id_server`)
  und gibt den API-Schlüssel an `pocket-id` weiter. Die Anleitung zum Anbinden der Apps steht in `modules/pocket-id/README.md`.
- `modules/wireguard`: Dienst aus `service/server.py`, der Zugriff auf WireGuard-Easy ab Version 15 (REST-API mit
  Basic-Anmeldung, Quelle `src/server/api` im Repo `wg-easy/wg-easy`) in `service/wgeasy.py`. Die App zeichnet den
  QR-Code selbst aus `/api/clients/<id>/config` (zxing). Genutzt in `android/.../WireGuardApi.kt` und
  `WireGuardScreens.kt`. `modules/wireguard-server` installiert WireGuard-Easy selbst; das Passwort des ersten Kontos
  erzeugt der Kern (`Install.wireguard_server`) und legt die Anmeldung als `login.json` für `wireguard` ab.
- `modules/pihole`: Dienst aus `service/server.py`, der Zugriff auf Pi-hole 6 (REST-API, Anmeldung mit Passwort oder
  App-Passwort, Sitzung wird wiederverwendet) in `service/pihole.py`. Mehrere Pi-holes gelten als ein Netz: Pausieren geht
  an alle, die Statistik wird zusammengezählt. Ansehen und Pausieren mit Ablaufzeit darf jeder mit Zugriff auf das Modul;
  Pausieren ohne Ende und die Einstellungen nur Eigentümer und Admins. Genutzt in `android/.../PiholeApi.kt` und
  `PiholeScreens.kt`. Meldungen (nicht erreichbar, wieder da, Update) kommen aus `Watcher` in `service/server.py`, der Stand
  liegt in `watch.json`, damit nichts doppelt gemeldet wird.

Ändert sich eine Schnittstelle, wird die App im selben Commit mit angepasst.

Ein Push auf `main`, der `android/` ändert, erzeugt das Release `app-<200 + Laufnummer>` mit `netzwerkmonitor.apk`, und der
Monitor gibt es ans Handy weiter. Paketkennung `de.archonw.netmon` und Signierschlüssel dürfen sich nicht ändern. Wird `android.yml` umbenannt, beginnt die Laufnummer neu: dann den Sockel erhöhen, sonst kommen keine Updates mehr an.

Der Kern (`core/`) baut aus den `module.json` der Module die Compose-Datei und die Liste für den Zugang. Auf Unraid hält er dabei den Ordner „Home-Net“ des Plugins folder.view2 (`/boot/config/plugins/folder.view2/docker.json`) auf genau den Containern von Home-Net. Die Sicherung von
Pi-Wartung läuft im Container `pi-wartung-engine`, der Dienst `pi-wartung-service` spricht sie über einen Socket an. Nur die
Sicherung hat erhöhte Rechte. Der Ordner `modules/pi-wartung/piwartung` enthält die alte GTK-App, sie wird vom Dienst nur noch
wegen `core.py` gebraucht.

Der Zugang erkennt Zugriffe von außen am Header `X-Forwarded-For` oder an einer öffentlichen Absenderadresse. Konten, Passkeys und Geräte liegen in `security.json` im Gateway-Ordner, das Protokoll in `audit.jsonl`. Module erfahren Benutzer, Rolle und erlaubte Kategorien über die Header `X-Home-Net-*`, die nur der Zugang setzt; Module müssen diese Rechte selbst anwenden. Module dürfen darum nur über den Zugang erreichbar sein. Im Monitor dürfen nur Eigentümer und Admins ändern (`require_manager`), Benutzer sehen nur an. Ein Modul im Host-Netz wie der Monitor nennt in `module.json` dafür `api.socket` statt eines Ports: Es lauscht auf einer Socket-Datei, deren Ordner sich der Zugang mit ihm teilt. Ohne `LISTEN_SOCKET` öffnet der Monitor wie zuvor Port 8085, das gilt nur für den Betrieb allein.

Meldungen: Ein Modul nennt in `module.json` unter `events` seine Arten und legt je Meldung eine JSON-Datei in seinem Ordner unter `${DATA_DIR}/events/<modul>` ab. Der Zugang (`core/gateway/events.py`) übernimmt sie, prüft Wahl und Rechte beim Lesen und liefert sie über `/auth/stream` an die App. Die Prüfung `App-Probelauf` startet die App auf Zweigen im Emulator gegen den echten Zugang; Änderungen an der App gehören erst danach auf `main`.

Das Repo ist öffentlich: keine Passwörter, Tokens oder persönlichen Adressen einchecken.
