# Networkmonitor

Überwacht das Heimnetz über die FritzBox, erkennt neue Geräte und lässt sie erlauben oder sperren.

Plan und Architektur: [docs/PLAN.md](docs/PLAN.md) · Zugriff von unterwegs: [docs/FERNZUGRIFF.md](docs/FERNZUGRIFF.md) · App-Updates: [docs/APP-UPDATES.md](docs/APP-UPDATES.md)

## Stand

- Liest alle 30 s die Geräteliste der FritzBox (TR-064) und merkt sich jedes Gerät.
- Geräte aus dem ersten Scan gelten als **erlaubt**, alle später auftauchenden als **unbekannt**.
- Weboberfläche (Port 8085) im Stil der Android-App: Designs „Soft“ und „Kacheln“, hell/dunkel, Akzentfarben, Suche, Filter, Sortierung, Einzelgeräte-Seite mit „Erlauben“, „Sperren“, „Umbenennen“ und kopierbaren Werten. Funktioniert auf Handy und Desktop.
- **Sperren** trennt das Gerät vom **Internet** (FritzBox-Kindersicherung per TR-064). Im Heimnetz bleibt es erreichbar, denn den WLAN-MAC-Filter kann die FritzBox über TR-064 nicht pflegen.
- Optional: `QUARANTINE_UNKNOWN=true` trennt unbekannte Geräte sofort vom Internet, bis sie erlaubt werden.

## 1. FritzBox vorbereiten

1. **Heimnetz → Netzwerk → Netzwerkeinstellungen**: „Zugriff für Anwendungen zulassen“ und „Statusinformationen über UPnP übertragen“ aktivieren.
2. **System → FRITZ!Box-Benutzer → Benutzer hinzufügen**: z. B. `networkmonitor` mit eigenem Passwort und der Berechtigung **„FRITZ!Box Einstellungen“** (ohne Zugang aus dem Internet).

## 2. Installieren und einrichten

Eine `.env` ist nicht nötig. Alles wird in der Weboberfläche eingerichtet.

**Container starten**, z. B. auf Unraid mit dem Code unter `/mnt/user/appdata/networkmonitor/src` (jeder andere Docker-Host geht genauso):

```
docker build -t networkmonitor:local /mnt/user/appdata/networkmonitor/src
docker run -d --name networkmonitor --network host --cap-add NET_ADMIN --cap-add NET_RAW \
  -v /mnt/user/appdata/networkmonitor/data:/data --restart unless-stopped networkmonitor:local
```

Alternativ die Vorlage `unraid/networkmonitor.xml` nach `/boot/config/plugins/dockerMan/templates-user/my-networkmonitor.xml` kopieren und in Unraid unter **Docker → Add Container** wählen, oder `docker compose up -d --build`.

**Ersteinrichtung im Browser:**
1. `http://<unraid-ip>:8085/` öffnen und mit dem Standardpasswort **`netzwerkmonitor`** anmelden (Benutzername beliebig).
2. Ein eigenes Passwort festlegen (mindestens 8 Zeichen). Vorher ist nichts anderes erreichbar, auch nicht für die App.
3. Der Browser fragt erneut: jetzt mit dem neuen Passwort anmelden.
4. Auf der Einstellungsseite den FritzBox-Zugang aus Schritt 1 eintragen, „Verbindung testen“, speichern. Erst dann beginnt die Überwachung; der erste Scan übernimmt die vorhandenen Geräte als erlaubt.
5. Optional Benachrichtigungen einrichten (siehe unten) und „Testnachricht senden“.

Einstellungen liegen in der Datenbank unter `/data`. Wer den Container neu anlegt und `/data` behält, muss nichts erneut einrichten. Werte aus einer `.env` (siehe `.env.example`) sind weiterhin möglich und dienen als Startwerte; in der Weboberfläche gespeicherte Werte haben Vorrang.

## 3. FritzBox-Fähigkeiten prüfen

```
docker exec -it networkmonitor networkmonitor-probe
docker exec -it networkmonitor networkmonitor-probe --test-block 192.168.178.50   # IP eines eigenen Testgeräts
```

Zeigt Modell und FritzOS-Version, ob Hostliste und Internet-Sperre funktionieren, welche WLAN-Aktionen die Schnittstelle anbietet (MAC-Filter?) und relevante Zeilen aus dem Ereignisprotokoll.

## 4. Benachrichtigungen

**Alles aus diesem Abschnitt lässt sich auch in der Weboberfläche unter „Einstellungen“ (`/settings`) eintragen**, inklusive FritzBox-Zugang, Abfrageintervall und UI-Passwort. Dort gespeicherte Werte gelten sofort und haben Vorrang vor der `.env`; die `.env` liefert nur die Startwerte.

In der `.env` mit `NOTIFY` wählen: `ntfy`, `telegram`, `homeassistant` oder mehrere mit Komma (z. B. `ntfy,homeassistant`). Leer = aus. Danach den Container neu starten.

**Telegram** (mit Buttons „Erlauben“/„Sperren“ direkt in der Nachricht):
1. In Telegram mit `@BotFather` einen Bot anlegen (`/newbot`), Token in `TELEGRAM_BOT_TOKEN` eintragen.
2. Dem neuen Bot eine beliebige Nachricht schicken.
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen und die Zahl bei `"chat":{"id":` in `TELEGRAM_CHAT_ID` eintragen.

Nur Klicks aus diesem Chat werden ausgeführt.

**Home Assistant** (Nachricht mit „Öffnen“-Knopf zur Weboberfläche):
1. In Home Assistant unter Profil → Sicherheit ein langlebiges Zugriffstoken erstellen → `HA_TOKEN`.
2. `HA_URL` auf die Adresse von Home Assistant setzen.
3. `HA_NOTIFY_SERVICE` auf den notify-Dienst der Companion-App setzen (Entwicklerwerkzeuge → Aktionen, `notify.mobile_app_…`).

**ntfy + Android-App** (Fingerabdruck, dann nur das eine Gerät mit „Erlauben“/„Sperren“):
1. Einen eigenen ntfy-Server starten, z. B. auf Port 8090:
   `docker run -d --name ntfy --restart unless-stopped -p 8090:80 -v /mnt/user/appdata/ntfy:/var/cache/ntfy binwiederhier/ntfy serve --cache-file /var/cache/ntfy/cache.db`
2. In der `.env`: `NOTIFY=ntfy` (oder zusätzlich zu anderen), `NTFY_URL=http://192.168.178.20:8090`, `NTFY_TOPIC=netzwerkmonitor`.
3. Auf dem Handy die **ntfy-App** installieren (Play Store oder F-Droid), Standardserver auf `http://192.168.178.20:8090` setzen und das Thema `netzwerkmonitor` abonnieren.
4. Die **Netzwerkmonitor-App** installieren (siehe unten). Antippen der ntfy-Nachricht öffnet sie über `netmon://device/<MAC>`.

Test: `docker exec networkmonitor networkmonitor-notify-test` schickt eine Testnachricht an alle eingestellten Kanäle.

## 5. Android-App

Der Quellcode liegt in `android/`. GitHub Actions baut bei jedem Push die APK; nach einem Merge auf `main` liegt sie unter **Releases** als `netzwerkmonitor.apk`.

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
