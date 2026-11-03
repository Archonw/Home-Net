# WireGuard

[WireGuard-Easy](https://github.com/wg-easy/wg-easy) ist ein VPN-Server mit Weboberfläche: Wer unterwegs ist, kommt
damit sicher ins Heimnetz, als säße er zu Hause.

Das Modul **wireguard** verwaltet die Zugänge von WireGuard-Easy aus Home-Net und der App heraus:

- Zugänge ansehen: wer gerade verbunden ist, wann zuletzt, wie viel übertragen wurde
- neuen Zugang anlegen und den QR-Code gleich in der App zeigen; auf dem anderen Handy mit der App WireGuard scannen
- Zugang sperren, entsperren oder löschen
- im Browser zusätzlich die Konfiguration als Datei laden (für Laptops)

Verwalten dürfen nur Eigentümer und Admins von Home-Net, denn ein Zugang öffnet das ganze Heimnetz. Der QR-Code
enthält den geheimen Schlüssel des Geräts; die App holt ihn nur zum Anzeigen und speichert ihn nicht.

Nötig ist WireGuard-Easy ab Version 15.

## Installieren

```bash
./home-net add wireguard              # fragt, ob schon WireGuard-Easy läuft
./home-net add wireguard --existing   # vorhandenes WireGuard-Easy nutzen
./home-net add wireguard --with-server --host vpn.example.org   # WireGuard-Easy gleich mitinstallieren
```

### Mit vorhandenem WireGuard-Easy

1. In Home-Net im Browser beim Modul WireGuard unter *Einstellungen* den Benutzernamen und das Passwort der
   Weboberfläche von WireGuard-Easy eintragen. Sie bleiben nur auf dem Server.
2. Für dieses Konto darf keine Zwei-Faktor-Anmeldung eingeschaltet sein, sonst nimmt WireGuard-Easy die Anmeldung
   über die Schnittstelle nicht an.
3. Die Adresse ist vorbelegt mit `http://host.docker.internal:51821`, also WireGuard-Easy auf demselben Rechner mit
   Port 51821. Läuft es woanders, die Adresse im Heimnetz eintragen.

### WireGuard-Easy mitinstallieren (`wireguard-server`)

Das Installationsprogramm fragt nach der Adresse, unter der das Heimnetz von außen erreichbar ist (DynDNS-Name oder
feste IP). Es legt beim ersten Start ein Konto `home-net` mit einem zufälligen Passwort an und gibt die Anmeldung an
das Modul weiter, eingetragen werden muss nichts. Das Passwort steht in
`<Datenordner>/wireguard-server/secrets/password`, damit kommst du auch in die Weboberfläche unter Port 51821.

Danach im Router den Port **51820 (UDP)** an den Server weiterleiten. Die Weboberfläche (51821) bleibt im Heimnetz.

## Schnittstelle

Nur über den Zugang (`/m/wireguard/...`), nur für Eigentümer und Admins:

| Aufruf | Zweck |
| --- | --- |
| `GET /api/overview` | Zugänge mit Zustand und Datenmenge, Version von WireGuard-Easy |
| `POST /api/clients` `{"name"}` | Zugang anlegen, Antwort `{"id", "name"}` |
| `PUT /api/clients/<id>` `{"enabled"}` | sperren oder entsperren |
| `DELETE /api/clients/<id>` | löschen |
| `GET /api/clients/<id>/config` | Konfiguration als Text (für den QR-Code in der App) |
| `GET /api/clients/<id>/qrcode.svg` | QR-Code als Bild (für die Weboberfläche) |
| `GET/PUT /api/settings` | Adresse und Anmeldung |
