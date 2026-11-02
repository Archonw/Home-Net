# Unraid

Verwaltet den Unraid-Server, auf dem Home-Net läuft: Freigaben, Benutzer, wichtige Einstellungen, Paritätsprüfung,
Docker-Container und VMs. Dazu ein Dateibrowser zum Ansehen und Herunterladen und die Weitergabe der Meldungen von Unraid
an die App.

```bash
./home-net add unraid
```

Das Modul läuft nur direkt auf einem Unraid-Server, das Installationsprogramm prüft das.

## Wie es mit Unraid spricht

| Weg | Wofür | Hinweis |
| --- | --- | --- |
| Statusdateien in `/var/local/emhttp` (nur lesen) | Freigaben, Benutzer, Platten, Einstellungen | |
| Socket von emhttpd | Freigaben, Benutzer und Einstellungen ändern, Paritätsprüfung, Mover | Dieselben Formulare wie die Unraid-Oberfläche. Nicht offiziell beschrieben, darum nur auf geprüften Versionen (7.2, 7.3) erlaubt; der Eigentümer kann andere Versionen freigeben |
| Offizielle Unraid-API (Socket) | Docker-Container und VMs | Braucht einen API-Schlüssel: in Unraid unter *Einstellungen → Management Access → API Keys* mit der Rolle `admin` anlegen und im Modul unter *Einstellungen* eintragen |
| `/boot/config/plugins/dynamix/dynamix.cfg` | Temperatur-Warnungen | Wie die Unraid-Oberfläche: nur die Schlüssel `hot`, `max`, `hotssd`, `maxssd` im Abschnitt `[display]` |
| `/tmp/notifications` (nur lesen) | Meldungen von Unraid | Neue ungelesene Meldungen gehen als Alarm, Warnung oder sonstige Meldung an die App |
| `/mnt/user` (nur lesen) | Dateibrowser | Herunterladen bis 200 MB |

Der Container hat kein Netzwerk; er spricht nur über Sockets. Bindet Unraid einen Socket nach einem Neustart des Dienstes
neu, beendet sich das Modul beim nächsten Fehlversuch und Docker startet es mit dem neuen Socket.

## Rechte

Benutzer mit Zugriff auf das Modul sehen nur die Übersicht. Freigaben, Benutzer, Dateien, Einstellungen, Docker und VMs
sind Eigentümern und Admins vorbehalten. Ungeprüfte Unraid-Versionen freigeben darf nur der Eigentümer. Jede Änderung steht
im Protokoll des Zugangs.

Bewusst nicht enthalten: Array stoppen oder starten (das würde Home-Net selbst stoppen), Home-Net-Container stoppen,
Zeitzone und Active Directory. Das bleibt in der Unraid-Oberfläche.
