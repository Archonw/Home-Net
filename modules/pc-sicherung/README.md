# PC-Sicherung

Sichert ganze PCs, auch mit Windows und Linux nebeneinander, auf den Server und schreibt sie bei Bedarf komplett
oder teilweise zurück.

## So funktioniert es

- Zur Sicherung startet der PC nicht sein eigenes System, sondern ein kleines Sicherungssystem vom Server, über den
  Netzwerkstart (PXE) oder vom Stick. Dadurch sind alle Systeme ausgeschaltet und alle Partitionen werden in einem
  Zug gesichert: EFI-Partition, Windows, Linux, Bootloader und Partitionstabelle.
- Gespeichert werden nur belegte Bereiche (partclone). Unbekannte oder verschlüsselte Partitionen wie BitLocker
  werden vollständig gelesen.
- Die Daten gehen in ein restic-Archiv je PC. Gleiche Blöcke liegen nur einmal darin. Jede Sicherung lässt sich für
  sich zurückschreiben, braucht auf der Platte aber meist nur, was sich seit der letzten geändert hat.
- Der Server antwortet zusätzlich zur FritzBox auf Startanfragen. Fremden PCs nennt er nur die Startdatei (Proxy-DHCP).
  PCs, die im Modul angelegt sind, bekommen ein vollständiges Angebot mit der Adresse, die ihnen die FritzBox gerade
  angeboten hat, denn manche Firmware (Dell Latitude) übergeht reine Proxy-Angebote. Die Dateien liefert ein eigener
  TFTP-Server; fragt der Startlader nach einem Namen aus der Firmware (Dell: »Onboard«), bekommt er GRUB. Hat ein PC
  keinen Auftrag, schickt der Server ihn sofort zu seiner eigenen Platte weiter.
- Unraid: Anfragen an die Server-Adresse kommen teils über shim-br0 an. Port 4011 und TFTP hören darum auf die Adresse,
  nicht auf die Netzwerkkarte.
- UEFI-PCs starten über den signierten shim und GRUB von Ubuntu, Secure Boot darf an bleiben. Alte BIOS-PCs starten
  über iPXE.
- Nächtliche Sicherungen: Der Server weckt den PC per Wake-on-LAN, der PC startet übers Netz, sichert und schaltet
  sich wieder aus.
- Ein Auftrag von Hand wartet, bis der PC das nächste Mal übers Netz startet, auch über Tage; ein laufender PC wird
  nicht unterbrochen. Mit Wartungsfenster (etwa 23 bis 6 Uhr) wartet er auf dessen Beginn: Dann wird der PC geweckt,
  und nur ein Start im Fenster führt den Auftrag aus. Beim Auftrag lässt sich stattdessen »beim nächsten Start« wählen.
  Geplante Sicherungen geben auf, wenn der PC nicht kommt: ohne Fenster nach 20 Minuten, mit Fenster an dessen Ende.

## Teile

| Teil | Aufgabe |
| --- | --- |
| `service/server.py` | Weboberfläche über die Socket-Datei des Zugangs, Startdateien und Aufträge auf Port 8395, Zeitplan, Meldungen, Aufräumen |
| `service/store.py` | PCs, Aufträge, Verlauf, wartende PCs |
| `live/` | Das Sicherungssystem: `init` startet Geräte und Netz, `hn-agent` sichert oder schreibt zurück |
| `Dockerfile` | Baut das Sicherungssystem, die Startdateien und den Dienst; restic und rest-server mit Prüfsumme |

Ports im Heimnetz, nicht über den Zugang: 67 und 69 (UDP, Netzwerkstart), 8394 (rest-server, Zugang je PC, ohne
Recht zum Löschen) und 8395 (Startdateien, Stick-Abbild, Aufträge mit Einmal-Kennwort).

## Ablage

Im Ordner `PC_DIR` liegt je PC ein restic-Archiv (Ordnername = Kennung des PCs). `schluessel.json` daneben enthält die
Archiv-Passwörter, damit sich die Sicherungen auch ohne Home-Net lesen lassen:

```sh
export RESTIC_REPOSITORY=/mnt/user/…/PC-Sicherung/<kennung> RESTIC_PASSWORD=<aus schluessel.json>
restic snapshots                      # Schnappschüsse mit Etikett run=<Zeitpunkt>
restic dump <id> /d0p2.img | partclone.restore -s - -o /dev/sdX2
```

Ein Lauf besteht aus einem Schnappschuss je Partition (`d<platte>p<nummer>.img` oder `.raw`) und `meta.tar` mit
Partitionstabellen, dem ersten MiB jeder Platte und den EFI-Starteinträgen. `meta.tar` wird zuletzt geschrieben; ein Lauf
ohne sie ist unvollständig und wird beim Aufräumen entfernt.

## Am PC einmal einstellen

1. Im BIOS/UEFI den Netzwerkstart (IPv4 PXE) einschalten und an die erste Stelle der Startreihenfolge setzen. IPv6-PXE
   und HTTP-Boot ausschalten, sonst wartet der PC ohne Auftrag unnötig.
2. Wake-on-LAN einschalten, unter Windows den Schnellstart ausschalten.
3. Verweigert der PC mit Secure Boot den Start, die Microsoft-Drittanbieter-Zertifikate (»Microsoft UEFI CA«) erlauben.
4. LAN-Kabel, über WLAN geht der Netzwerkstart nicht.

## Prüfen

```sh
TZ=Europe/Berlin python3 -m unittest discover tests
```
