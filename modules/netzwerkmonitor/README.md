# Networkmonitor

Überwacht das Heimnetz über die FritzBox, erkennt neue Geräte und lässt sie erlauben oder sperren.

Plan und Architektur: [docs/PLAN.md](docs/PLAN.md) · Zugriff von unterwegs: [docs/FERNZUGRIFF.md](docs/FERNZUGRIFF.md) · App-Updates: [docs/APP-UPDATES.md](../../docs/APP-UPDATES.md)

## Stand

- Liest alle 30 s die Geräteliste der FritzBox (TR-064) und merkt sich jedes Gerät.
- Geräte aus dem ersten Scan gelten als **erlaubt**, alle später auftauchenden als **unbekannt**.
- Weboberfläche (Port 8085) im Stil der Android-App: Designs „Soft“ und „Kacheln“, hell/dunkel, Akzentfarben, Suche, Filter, Sortierung, Einzelgeräte-Seite mit „Erlauben“, „Sperren“, „Umbenennen“ und kopierbaren Werten. Funktioniert auf Handy und Desktop.
- **Sperren** trennt das Gerät vom **Internet** (FritzBox-Kindersicherung per TR-064). Im Heimnetz bleibt es erreichbar, denn den WLAN-MAC-Filter kann die FritzBox über TR-064 nicht pflegen.
- **WLAN-Repeater** schreiben MAC-Adressen um und lassen dabei die letzten drei Bytes stehen. Einträge ohne IP mit lokal verwalteter MAC, deren letzte drei Bytes zu einem bekannten Gerät mit IP passen, gelten als Kopie und werden nicht angelegt. Bekommt so ein Eintrag eine IP, wird er normal gemeldet. Vor dem Sperren einer möglichen Kopie fragen Weboberfläche und App nach, und eine Meldung weist darauf hin, wenn ein neues Gerät zu einem gesperrten passt.
- Optional: `QUARANTINE_UNKNOWN=true` trennt unbekannte Geräte sofort vom Internet, bis sie erlaubt werden.

## 1. FritzBox vorbereiten

1. **Heimnetz → Netzwerk → Netzwerkeinstellungen**: „Zugriff für Anwendungen zulassen“ und „Statusinformationen über UPnP übertragen“ aktivieren.
2. **System → FRITZ!Box-Benutzer → Benutzer hinzufügen**: z. B. `networkmonitor` mit eigenem Passwort und der Berechtigung **„FRITZ!Box Einstellungen“** (ohne Zugang aus dem Internet).

## 2. Installieren und einrichten

Eine `.env` ist nicht nötig. Alles wird in der Weboberfläche eingerichtet.

**Mit Home-Net** (empfohlen): `home-net add netzwerkmonitor`. Der Monitor ist dann über den gemeinsamen Zugang erreichbar.

**Allein**, ohne Home-Net, auf einem beliebigen Docker-Rechner:

```
docker build -t networkmonitor:local modules/netzwerkmonitor
docker run -d --name networkmonitor --network host --cap-add NET_ADMIN --cap-add NET_RAW \
  -v /opt/networkmonitor/data:/data --restart unless-stopped networkmonitor:local
```

oder in `modules/netzwerkmonitor` mit `docker compose up -d --build`. Die Schritte unten gelten für den Betrieb allein.

**Ersteinrichtung im Browser:**
1. `http://<server-ip>:8085/` öffnen und mit dem Standardpasswort **`netzwerkmonitor`** anmelden (Benutzername beliebig).
2. Ein eigenes Passwort festlegen (mindestens 8 Zeichen). Vorher ist nichts anderes erreichbar, auch nicht für die App.
3. Der Browser fragt erneut: jetzt mit dem neuen Passwort anmelden.
4. Auf der Einstellungsseite den FritzBox-Zugang aus Schritt 1 eintragen, „Verbindung testen“, speichern. Erst dann beginnt die Überwachung; der erste Scan übernimmt die vorhandenen Geräte als erlaubt.

Einstellungen liegen in der Datenbank unter `/data`. Wer den Container neu anlegt und `/data` behält, muss nichts erneut einrichten. Werte aus einer `.env` (siehe `.env.example`) sind weiterhin möglich und dienen als Startwerte; in der Weboberfläche gespeicherte Werte haben Vorrang.

## 3. FritzBox-Fähigkeiten prüfen

```
docker exec -it networkmonitor networkmonitor-probe
docker exec -it networkmonitor networkmonitor-probe --test-block 192.168.1.50   # IP eines eigenen Testgeräts
```

Zeigt Modell und FritzOS-Version, ob Hostliste und Internet-Sperre funktionieren, welche WLAN-Aktionen die Schnittstelle anbietet (MAC-Filter?) und relevante Zeilen aus dem Ereignisprotokoll.

## 4. Benachrichtigungen

Meldungen über neue Geräte, Geräte im Blick und eine nicht erreichbare FritzBox kommen über Home-Net in die App. Welche du bekommst, wählst du auf der Home-Net-Seite unter „Mein Konto“. Eigene Kanäle wie ntfy, Telegram oder Home Assistant hat der Monitor nicht mehr.

## 5. Android-App

Die App-Updates verteilt Home-Net selbst, auch ohne den Netzwerkmonitor. GitHub-Repository und Token stellst du auf der Home-Net-Seite unter „Einstellungen“ ein.

Die App heißt jetzt **Home-Net** und hat ein [eigenes Repository](https://github.com/Archonw/Home-Net). Dort baut GitHub Actions bei jedem Push die APK; nach einem Push auf `main` liegt sie unter **Releases** als `netzwerkmonitor.apk`.

- APK aufs Handy laden und installieren („Installation aus unbekannten Quellen“ für den Browser erlauben). Updates werden einfach drüber installiert.
- Beim ersten Start: Adresse des Monitors (`http://192.168.178.20:8085`) und das Passwort der Weboberfläche eintragen. Beides wird verschlüsselt auf dem Handy gespeichert.
- „Offline entfernen“ (App) bzw. „Offline-Geräte entfernen“ (Weboberfläche) löscht alle Geräte, die gerade offline sind, aus dem Monitor. Kommen sie wieder ins Netz, gelten sie als neu und müssen erneut bestätigt werden. Gesperrte Geräte bleiben stehen, damit ihre Sperre erhalten bleibt. Die Einträge in der FritzBox selbst ändern sich dadurch nicht.
- Jeder Start, jede Benachrichtigung und jede Rückkehr nach mehr als einer Minute verlangt eine Anmeldung. Nach der Einrichtung ist das das Passwort der Weboberfläche; unter Einstellungen → „Entsperren mit“ lässt sich auf Fingerabdruck (oder Geräte-PIN) umstellen. Das Passwort funktioniert immer als Ausweg.
- Zwei Designs zur Wahl unter Einstellungen → „Design“: **Soft** (weiche Karten, Filter-Pillen) und **Kacheln** (Zählkacheln als Filter, Festbreitenschrift für IP/MAC). Beide gibt es hell und dunkel, jeweils mit vier Akzentfarben. Die Liste lässt sich durchsuchen (Name, IP, MAC) und durch Herunterziehen aktualisieren.
- Auf der Geräteseite lassen sich Werte wie IP- oder MAC-Adresse durch langes Drücken markieren und kopieren.
- Die APKs aus den Releases sind immer mit demselben Schlüssel signiert, damit Updates ohne Neuinstallation klappen. Der Schlüssel liegt nicht im Repository, sondern als Secret in GitHub Actions. Wer die App selbst baut, bekommt eine mit dem Debug-Schlüssel signierte APK; die lässt sich nicht über eine offizielle installieren (vorher deinstallieren).

## Entwicklung

```
pip install -e '.[dev]'
ruff check . && ruff format --check . && pytest
```

## Lizenz

[Apache License 2.0](LICENSE). Die Android-App enthält Bibliotheken von AndroidX und Kotlin, ebenfalls unter Apache-2.0; ihre Hinweise zeigt die App unter Einstellungen → „Über die App“. Der Monitor nutzt FastAPI (MIT), Uvicorn (BSD-3-Clause) und fritzconnection (MIT).

FRITZ!Box ist eine Marke der AVM GmbH. Dieses Projekt steht in keiner Verbindung zu AVM.
