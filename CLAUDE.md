# Zusammenhänge

Dieser Dienst gehört zu Home-Net. Die Android-App liegt seit 2026-10-31 im Repository
[Archonw/Home-Net](https://github.com/Archonw/Home-Net), nicht mehr hier.

Die App nutzt die REST-API aus `src/networkmonitor/api.py` und holt Updates über `/app`. Wer dort Felder oder
Endpunkte ändert, muss prüfen, ob `app/src/main/java/de/archonw/netmon/Api.kt` in Home-Net mit angepasst werden muss.
Neue Versionen erkennt der Monitor an Releases `app-<Nummer>` mit der Datei `netzwerkmonitor.apk`.
