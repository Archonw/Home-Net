# Pi-Wartung

Modul von Home-Net, um Raspberry Pis zu verwalten und als Image zu sichern.
Die Images lassen sich mit Raspberry Pi Imager und Balena Etcher direkt auf eine SD-Karte schreiben.

## Einrichten

`home-net add pi-wartung`, danach in der Weboberfläche unter Pi-Wartung die Pis anlegen. Wohin die Images kommen,
legt die Installation fest (`--images`). Auf dem Pi wird nichts installiert, er braucht nur ssh, sudo ohne Passwort
und rsync.

## So funktioniert die Sicherung

Gesichert wird im Container `pi-wartung-engine` auf dem Server, darum laufen Sicherungen weiter, wenn dein Rechner
aus ist.

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
- Unterstützt wird das übliche Layout: MBR, Boot als vfat, Root als ext4. Andere Layouts bricht Pi-Wartung mit einer
  klaren Meldung ab.
- Der Spiegel braucht auf dem Server etwa so viel Platz wie die belegten Daten des Pi.

## Pis außerhalb des eigenen Netzes

Der Server baut die Verbindung auf, der Pi muss also von dort erreichbar sein: per DynDNS mit Portweiterleitung,
per Tailscale oder über einen Sprunghost. Für langsame Leitungen gibt es Komprimierung und ein Bandbreitenlimit.

## SSH-Schlüssel

Beim Hinzufügen eines Pi und unter **Neuen Schlüssel einrichten** erzeugt Pi-Wartung auf dem Server einen eigenen
Schlüssel für diesen Pi und trägt ihn dort ein:

- **Mit Passwort:** Benutzer und Passwort werden einmalig für die Übertragung genutzt und nirgends gespeichert.
  Der Server meldet sich dabei selbst am Pi an.

Danach prüft Pi-Wartung die Anmeldung und ob sudo ohne Passwort möglich ist.

## Ablage im Image-Ordner

```text
PiWartung/
  inventory.json             Liste aller Pis und Kategorien
  .engine/pi-backup.sh       Sicherungsprogramm
  Familie/Wohnzimmer/
    pi.conf                  Einstellungen dieses Pi
    Wohnzimmer_2026-10-31_2000.img.xz
    Wohnzimmer_2026-10-31_2000.img.xz.info
    logs/
    .state/                  Spiegel und Arbeitsdateien
```

## Zeitplan

Wird bei einem Pi ein Zeitplan gewählt, sichert die Engine ihn, sobald er fällig ist. Sie schaut stündlich nach. Ist ein Pi gerade nicht erreichbar, folgt der nächste Versuch eine Stunde später.

## Benachrichtigungen

Pro Pi lässt sich wählen: nur bei Fehlern, auch bei Erfolg oder aus. Die Meldungen kommen in der Home-Net-App an,
jedes Konto wählt dort, welche es möchte. Bei einem dauerhaften Problem kommt eine Nachricht beim ersten Fehler, dann
höchstens eine pro Tag und eine, sobald es wieder klappt.

## Updates

Zweimal am Tag fragt der Dienst jeden Pi, welche Pakete ein `apt full-upgrade` installieren würde. Die Detailseite zeigt
den Software-Stand und die wartenden Pakete, die Übersicht wie viele Pis Updates haben. Pro Pi gibt es drei Stufen:

- **Bei „Alle aktualisieren“ dabei:** der Knopf in der Übersicht nimmt diesen Pi mit.
- **Nur einzeln aktualisieren:** nur über den Knopf auf seiner Detailseite. So steht jeder Pi, bis die Einstellung
  einmal gespeichert wurde.
- **Gesperrt:** nur einzeln, nachdem der Name des Pi eingetippt wurde. Die Prüfung lädt dort nicht einmal die
  Paketlisten neu. Die Engine verweigert ein Update auch selbst, wenn `pi.conf` keine Stufe nennt.

apt läuft abgelöst auf dem Pi (`/var/tmp/pi-wartung-update.*`), eine abreißende Verbindung bricht es also nicht ab.
Sicherung und Update schließen sich gegenseitig aus, neu gestartet wird der Pi nie. Updates starten dürfen nur Admins.

## Entwicklung

```bash
python3 -m unittest discover tests
```
