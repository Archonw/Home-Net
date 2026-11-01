# Zusammenhänge

Home-Net ist ein Baukasten: `android/` ist die App, `modules/` enthält die Dienste. Der Plan steht in `docs/PLAN.md`.

- `modules/netzwerkmonitor`: REST-API aus `src/networkmonitor/api.py`, genutzt in `android/.../Api.kt`.
- `modules/pi-wartung`: Dienst aus `service/server.py`, genutzt in `android/.../PiApi.kt` und `PiScreens.kt`.

Ändert sich eine Schnittstelle, wird die App im selben Commit mit angepasst.

Ein Push auf `main`, der `android/` ändert, erzeugt das Release `app-<200 + Laufnummer>` mit `netzwerkmonitor.apk`, und der
Monitor gibt es ans Handy weiter. Paketkennung `de.archonw.netmon` und Signierschlüssel dürfen sich nicht ändern. Wird `android.yml` umbenannt, beginnt die Laufnummer neu: dann den Sockel erhöhen, sonst kommen keine Updates mehr an.

Der Kern (`core/`) baut aus den `module.json` der Module die Compose-Datei und die Liste für den Zugang. Die Sicherung von
Pi-Wartung läuft im Container `pi-wartung-engine`, der Dienst `pi-wartung-service` spricht sie über einen Socket an. Nur die
Sicherung hat erhöhte Rechte. Der Ordner `modules/pi-wartung/piwartung` enthält die alte GTK-App, sie wird vom Dienst nur noch
wegen `core.py` gebraucht.

Der Zugang erkennt Zugriffe von außen am Header `X-Forwarded-For` oder an einer öffentlichen Absenderadresse. Konten, Passkeys und Geräte liegen in `security.json` im Gateway-Ordner, das Protokoll in `audit.jsonl`. Module erfahren Benutzer, Rolle und erlaubte Kategorien über die Header `X-Home-Net-*`, die nur der Zugang setzt; Module müssen diese Rechte selbst anwenden. Module dürfen darum nur über den Zugang erreichbar sein.

Das Repo ist öffentlich: keine Passwörter, Tokens oder persönlichen Adressen einchecken.
