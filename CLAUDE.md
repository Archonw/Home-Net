# Zusammenhänge

Home-Net ist ein Baukasten: `android/` ist die App, `modules/` enthält die Dienste. Der Plan steht in `docs/PLAN.md`.

- `modules/netzwerkmonitor`: REST-API aus `src/networkmonitor/api.py`, genutzt in `android/.../Api.kt`.
- `modules/pi-wartung`: Dienst aus `service/server.py`, genutzt in `android/.../PiApi.kt` und `PiScreens.kt`.

Ändert sich eine Schnittstelle, wird die App im selben Commit mit angepasst.

Ein Push auf `main`, der `android/` ändert, erzeugt das Release `app-<100 + Laufnummer>` mit `netzwerkmonitor.apk`, und der
Monitor gibt es ans Handy weiter. Paketkennung `de.archonw.netmon` und Signierschlüssel dürfen sich nicht ändern.

Die Pi-Wartung-App am PC aktualisiert `engine/pi-backup.sh` auf dem Unraid von selbst. Der Handy-Dienst nicht: Nach
Änderungen an `service/` oder `piwartung/core.py` müssen `service/install.sh` und `service/set-templates.sh` am PC laufen.

Das Repo ist öffentlich: keine Passwörter, Tokens oder persönlichen Adressen einchecken.
