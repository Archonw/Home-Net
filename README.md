# Pi-Wartung

Kleine Linux-App, um Raspberry Pis zu verwalten und als Image auf dem Unraid zu sichern.
Die Images lassen sich mit Raspberry Pi Imager und Balena Etcher direkt auf eine SD-Karte schreiben.

## Starten

```bash
./install.sh
pi-wartung
```

Voraussetzungen auf dem Rechner: Python 3, GTK 3 (`python3-gi`, unter Linux Mint vorhanden) und
SSH-Zugang zum Unraid als root. Eingestellt wird das alles unter **Einstellungen** in der App.

## So funktioniert die Sicherung

Die App steuert nur. Gesichert wird auf dem Unraid, darum laufen Sicherungen weiter, wenn dein Rechner
aus ist. Auf dem Pi wird nichts installiert, er braucht nur ssh, sudo ohne Passwort und rsync.

1. **Abgleich:** rsync holt die Dateien des Pi in einen Spiegel auf dem Unraid. Beim ersten Mal kommt alles,
   danach nur noch Änderungen. Bricht die Verbindung ab, macht der nächste Versuch dort weiter.
2. **Neues Dateisystem:** Aus dem Spiegel entsteht ein frisches ext4 mit gleicher UUID, gleichem Label und
   gleichen Funktionen wie auf dem Pi, ohne freien Platz und ohne Reste gelöschter Dateien.
3. **Image:** Boot-Partition und neues Dateisystem werden mit der originalen Partitions-ID zusammengesetzt.
4. **Komprimieren und prüfen:** Das Ergebnis wird mit höchster Stufe als `.img.xz` gespeichert und vor dem Ablegen getestet.
   Ein fehlgeschlagener Lauf lässt vorhandene Images unberührt.

Beim ersten Start eines zurückgeschriebenen Image vergrößert ein einmaliger Dienst die Partition auf die
ganze Karte und entfernt sich dann selbst.

Nicht im Image: Swap-Datei, Paket-Cache, `/tmp`, Benutzer-Caches und alles, was nicht auf der SD-Karte liegt,
zum Beispiel NFS-Freigaben. Weitere Ausschlüsse gehen pro Pi in der Konfiguration.

## Wichtig zu wissen

- Die Sicherung läuft im Betrieb. Jede Datei wird einzeln gelesen, Änderungen während des Laufs können also
  zwischen Dateien auseinanderlaufen. Für Datenbanken gibt es pro Pi **Befehl vorher** und **Befehl nachher**,
  um Dienste kurz zu stoppen.
- Unterstützt wird das übliche Layout: MBR, Boot als vfat, Root als ext4. Andere Layouts bricht die App mit einer
  klaren Meldung ab.
- Der Spiegel braucht auf dem Unraid etwa so viel Platz wie die belegten Daten des Pi.

## Pis außerhalb des eigenen Netzes

Das Unraid baut die Verbindung auf, der Pi muss also von dort erreichbar sein: per DynDNS mit Portweiterleitung,
per Tailscale oder über einen Sprunghost. Für langsame Leitungen gibt es Komprimierung und ein Bandbreitenlimit.

## SSH-Schlüssel

Beim Hinzufügen eines Pi und unter **Neuen Schlüssel einrichten** erzeugt die App auf dem Unraid einen eigenen
Schlüssel für diesen Pi und trägt ihn dort ein:

- **Mit Passwort:** Benutzer und Passwort werden einmalig für die Übertragung genutzt und nirgends gespeichert.
  Das Unraid meldet sich dabei selbst am Pi an.
- **Ohne Passwort:** Die App nutzt deine eigene SSH-Anmeldung von diesem Rechner und richtet den Schlüssel
  auch auf einem Sprunghost ein. Bei Pis hinter einem Sprunghost ist das der vorgesehene Weg.

Danach prüft die App die Anmeldung und ob sudo ohne Passwort möglich ist.

## Ablage auf dem Unraid

```text
PiWartung/
  inventory.json             Liste aller Pis und Kategorien
  .engine/pi-backup.sh       Sicherungsprogramm, die App hält es aktuell
  Familie/Wohnzimmer/
    pi.conf                  Einstellungen dieses Pi
    Wohnzimmer_2026-10-31_2000.img.xz
    Wohnzimmer_2026-10-31_2000.img.xz.info
    logs/
    .state/                  Spiegel und Arbeitsdateien
```

## Zeitplan

Wird bei einem Pi ein Zeitplan gewählt, legt die App das User Script `PiWartung-Geplant` an. Es läuft stündlich
und sichert jeden Pi, der fällig ist. Ist ein Pi gerade nicht erreichbar, folgt der nächste Versuch eine Stunde später.

## Benachrichtigungen

Pro Pi lässt sich wählen: nur bei Fehlern, auch bei Erfolg oder aus. Die Nachricht geht über das Benachrichtigungssystem
des Unraid, wohin sie geht, stellst du dort unter *Settings → Notifications* ein. Bei einem dauerhaften Problem kommt eine
Nachricht beim ersten Fehler, dann höchstens eine pro Tag und eine, sobald es wieder klappt.

Zusätzlich kann die App über einen eigenen ntfy-Server benachrichtigen. Server, Thema und optional ein Token stehen unter
**Einstellungen**, dort gibt es auch eine Testnachricht. Das Thema musst du einmal in der ntfy-App abonnieren.

## Handy-Dienst

`service/install.sh` installiert auf dem Unraid den Container `pi-wartung-service` (Port 8092) für die App
[Home-Net](https://github.com/Archonw/Home-Net). Er läuft vom PC aus. Sein SSH-Schlüssel kann nur die in `cmd_api` der Engine
erlaubten Befehle ausführen. Das Passwort setzt du in der PC-App unter Einstellungen → Handy-Zugang.

`service/set-templates.sh` gibt den Containern Unraid-Vorlagen, damit Logos und der Autostart-Regler funktionieren.

## Entwicklung

```bash
python3 -m unittest discover tests
```
