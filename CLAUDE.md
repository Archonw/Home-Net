# Zusammenhänge

Home-Net ist die Android-App für die Dienste im Heimnetz. Die Dienste liegen in eigenen Repositories:

- [Archonw/Networkmonitor](https://github.com/Archonw/Networkmonitor): REST-API aus `src/networkmonitor/api.py`, genutzt in `Api.kt`
- Archonw/Pi-Wartung (privat): Dienst aus `service/server.py`, genutzt in `PiApi.kt`

Ändert sich eine Schnittstelle, muss die jeweils andere Seite mit angepasst werden. Geht es um eine Änderung an einem
Dienst, gehört sie in dessen Repository, die App nur, soweit sie davon betroffen ist.

Releases entstehen durch einen Push auf `main` (`app-<100 + Laufnummer>`), der Monitor gibt sie ans Handy weiter. Die
Paketkennung `de.archonw.netmon` und der Signierschlüssel dürfen sich nicht ändern, sonst gehen Updates nicht mehr.
