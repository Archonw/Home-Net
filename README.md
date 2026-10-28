# Networkmonitor

Überwacht das Heimnetz über die FritzBox, erkennt neue Geräte und lässt sie erlauben oder sperren.

Plan und Architektur: [docs/PLAN.md](docs/PLAN.md)

## Stand

- Liest alle 30 s die Geräteliste der FritzBox (TR-064) und merkt sich jedes Gerät.
- Geräte aus dem ersten Scan gelten als **erlaubt**, alle später auftauchenden als **unbekannt**.
- Weboberfläche (Port 8085) mit „Erlauben“, „Sperren“, „Umbenennen“.
- **Sperren** trennt das Gerät derzeit nur vom **Internet** (FritzBox-Kindersicherung per TR-064). Die komplette Sperre (WLAN-MAC-Filter, ARP-Block) folgt, sobald das Prüfwerkzeug gezeigt hat, was die FritzBox zulässt.
- Optional: `QUARANTINE_UNKNOWN=true` trennt unbekannte Geräte sofort vom Internet, bis sie erlaubt werden.

## 1. FritzBox vorbereiten

1. **Heimnetz → Netzwerk → Netzwerkeinstellungen**: „Zugriff für Anwendungen zulassen“ und „Statusinformationen über UPnP übertragen“ aktivieren.
2. **System → FRITZ!Box-Benutzer → Benutzer hinzufügen**: z. B. `networkmonitor` mit eigenem Passwort und der Berechtigung **„FRITZ!Box Einstellungen“** (ohne Zugang aus dem Internet).

## 2. Auf Unraid installieren

Bis das Image automatisch gebaut wird (nach dem Merge über GitHub Actions nach `ghcr.io/archonw/networkmonitor`):

- Vorlage `unraid/networkmonitor.xml` nach `/boot/config/plugins/dockerMan/templates-user/my-networkmonitor.xml` kopieren.
- In Unraid unter **Docker → Add Container** die Vorlage „networkmonitor“ wählen, FritzBox-Passwort und ein Passwort für die Weboberfläche eintragen.
- Weboberfläche: `http://<unraid-ip>:8085/` (Benutzername beliebig, z. B. `admin`).

Da das Repo privat ist, muss das Paket auf ghcr.io entweder öffentlich geschaltet oder Unraid mit einem GitHub-Token angemeldet werden (`docker login ghcr.io`).

Alternativ per Compose: `.env.example` nach `.env` kopieren, ausfüllen, `docker compose up -d --build`.

## 3. FritzBox-Fähigkeiten prüfen

```
docker exec -it networkmonitor networkmonitor-probe
docker exec -it networkmonitor networkmonitor-probe --test-block 192.168.178.50   # IP eines eigenen Testgeräts
```

Zeigt Modell und FritzOS-Version, ob Hostliste und Internet-Sperre funktionieren, welche WLAN-Aktionen die Schnittstelle anbietet (MAC-Filter?) und relevante Zeilen aus dem Ereignisprotokoll.

## Entwicklung

```
pip install -e '.[dev]'
ruff check . && ruff format --check . && pytest
```
