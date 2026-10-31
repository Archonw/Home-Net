# Zusammenhänge

Pi-Wartung besteht aus der PC-App (`piwartung/`), der Sicherung auf dem Unraid (`engine/pi-backup.sh`) und dem Dienst für
das Handy (`service/`). Die Handy-Oberfläche liegt in [Archonw/Home-Net](https://github.com/Archonw/Home-Net), `PiApi.kt`
und `PiScreens.kt`. Ändert sich die Antwort von `service/server.py`, muss Home-Net mit angepasst werden.

Die App aktualisiert `engine/pi-backup.sh` auf dem Unraid von selbst. Der Dienst nicht: Nach Änderungen an `service/` oder
`piwartung/core.py` muss `service/install.sh` und danach `service/set-templates.sh` auf dem PC laufen.
