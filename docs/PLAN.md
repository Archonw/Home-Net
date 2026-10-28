# Netzwerkmonitor – Plan

Stand: 28.10.2026 · Status: Entwurf, offene Fragen siehe Abschnitt 8

## 1. Ziel

Eine App, die das Heimnetz überwacht, **neue Geräte erkennt**, mich darüber **benachrichtigt** und mich entscheiden lässt, ob das Gerät **erlaubt oder gesperrt** wird.

Kernfunktionen (MVP):

1. Geräteinventar: alle Geräte im Netz mit MAC, IP, Hostname, Hersteller (aus der MAC-OUI), erstes/letztes Mal gesehen.
2. Erkennung neuer Geräte innerhalb weniger Sekunden.
3. Benachrichtigung (Push aufs Handy, optional E-Mail/Telegram).
4. Entscheidung „Erlauben“ / „Sperren“ pro Gerät, mit Namen und Notiz.
5. Durchsetzung der Sperre (siehe Abschnitt 4 – das ist der schwierige Teil).

Später: Online/Offline-Verlauf, Geräte-Gruppen, Zeitpläne (z. B. Kinder-Geräte nachts sperren), Port-Scan neuer Geräte, Export.

## 2. Wichtigste Erkenntnis vorweg

**Erkennen** ist einfach und funktioniert in jedem Netz. **Sperren** hängt fast vollständig davon ab, was der Router kann:

- Ein Gerät, das sich ins WLAN einloggt (WPA2/WPA3 mit Passwort), *hat* bereits Zugriff, bevor wir es sehen. Eine echte „Vorab-Freigabe“ gibt es nur mit Router-Unterstützung (z. B. FritzBox „Neue Geräte sperren“, UniFi, OpenWrt) oder mit 802.1X/RADIUS.
- Ohne Router-Integration bleibt nur ARP-Spoofing als Notlösung (Abschnitt 4.3) – funktioniert, ist aber fragil.

Deshalb ist die Wahl des Routers bzw. der Integration die wichtigste Architekturentscheidung.

## 3. Erkennung neuer Geräte

Mehrere Quellen, kombinierbar. Die App läuft dafür auf einem Gerät, das dauerhaft im Netz ist (Raspberry Pi, NAS, Heimserver, Docker-Host).

| Methode | Wie | Geschwindigkeit | Vorteile | Nachteile |
|---|---|---|---|---|
| **Passives ARP/DHCP-Lauschen** | Rohsocket/pcap lauscht auf ARP-Pakete und DHCP-Requests | sofort (< 1 s) | erkennt Geräte beim ersten Paket, inkl. DHCP-Hostname | braucht Root/`CAP_NET_RAW`, nur im eigenen L2-Segment |
| **Aktiver ARP-Scan** | regelmäßig ARP-Request an jede IP im Subnetz | Intervall (z. B. 30–60 s) | findet auch stille Geräte | Last minimal, aber Intervall-Verzögerung |
| **Router-API** | DHCP-Leases/Hostliste vom Router abfragen (TR-064 bei FritzBox, UniFi-API, OpenWrt ubus/LuCI) | Polling (10–60 s) oder Events | zuverlässig, auch Geräte in anderen Segmenten/WLAN-Bändern | routerabhängig |
| **mDNS/SSDP/NetBIOS** | Multicast-Ankündigungen mitlesen | sofort | bessere Gerätenamen und -typen | nur als Zusatzinfo |
| **Eigener DHCP-Server** | App bzw. Pi-hole/dnsmasq übernimmt DHCP | sofort | volle Kontrolle über Adressvergabe | Router-DHCP abschalten nötig |

**Empfehlung:** Passives ARP/DHCP-Lauschen + aktiver ARP-Scan als Basis (funktioniert überall), Router-API als zusätzliche Quelle, sobald der Router bekannt ist. mDNS/OUI-Datenbank zur Anreicherung.

Stolperstein **MAC-Randomisierung**: iOS, Android und Windows verwenden pro WLAN zufällige („private“) MAC-Adressen, die sich ändern können. Folge: ein bekanntes Handy kann als „neues Gerät“ auftauchen. Gegenmaßnahmen: Hinweis in der App, wenn das „locally administered“-Bit gesetzt ist, Zuordnung über DHCP-Hostname/Fingerprint vorschlagen, und Nutzer bitten, die private Adresse fürs Heimnetz auf „fest“ zu stellen.

## 4. Erlauben / Sperren

### 4.1 Über den Router (bevorzugt)

| Router | Schnittstelle | Was geht |
|---|---|---|
| **AVM FritzBox** | TR-064 (SOAP), Benutzer mit Passwort | Hostliste lesen, Gerät sperren/entsperren (`X_AVM-DE_HostFilter`: `DisallowWANAccessByIP`). Zusätzlich in der FritzBox-Oberfläche: „Neue Geräte zunächst ohne Internetzugang“ – dann gibt die App nur noch frei. Sperre betrifft **Internet**, nicht das lokale Netz. |
| **UniFi (Ubiquiti)** | Controller-API (inoffiziell, aber stabil) | Clients listen, `block-sta`/`unblock-sta` – echte WLAN-Sperre. Gastnetz mit Freigabe möglich. |
| **OpenWrt** | SSH/ubus, LuCI-RPC | MAC-Filter im WLAN (`hostapd` deny-list), Firewall-Regeln, DHCP-Ausschluss – volle Kontrolle. |
| **MikroTik** | RouterOS-API | Access-List im WLAN, Firewall – volle Kontrolle. |
| **Speedport / Vodafone-Box / o2-Box** | meist keine offizielle API | Nur Web-Oberfläche „fernsteuern“ (fragil) oder Alternative 4.3/4.4. |

### 4.2 Quarantäne-Ansatz (das „echte“ Vorab-Freigeben)

Neue Geräte landen zuerst in einem **Gast-/Quarantäne-Netz** (eigenes WLAN oder VLAN) ohne Zugriff aufs Heimnetz bzw. Internet. Nach „Erlauben“ verschiebt die App sie ins Hauptnetz (bzw. hebt die Sperre auf). Machbar mit FritzBox (Option „neue Geräte ohne Internet“), UniFi, OpenWrt, MikroTik. Mit WPA3/802.1X+RADIUS (z. B. FreeRADIUS) wäre sogar eine Freigabe *vor* dem WLAN-Login möglich – für ein Heimnetz aber meist übertrieben.

### 4.3 ARP-Spoofing (ohne Router-Integration)

Die App gibt sich gegenüber dem gesperrten Gerät als Gateway aus und verwirft dessen Pakete (Prinzip von Tools wie NetCut/„Pi.Alert“-Varianten). Vorteile: funktioniert mit jedem Router. Nachteile:

- Gerät muss dauerhaft laufen; fällt es aus, ist die Sperre weg.
- Geräte mit statischem ARP oder IPv6 können es umgehen; IPv6 bräuchte zusätzlich NDP-Spoofing.
- Kann das Netz instabil machen, manche Router/Switches erkennen es als Angriff.
- Im lokalen Netz kommuniziert das Gerät u. U. weiter.

→ Nur als Notlösung bzw. für Router ohne API.

### 4.4 DNS-/DHCP-Ebene

Wenn die App (oder Pi-hole/AdGuard Home) DHCP und DNS übernimmt: unbekannte Geräte bekommen keine Adresse bzw. kein DNS. Einfach, aber leicht zu umgehen (statische IP, eigener DNS). Gut als „weiche“ Sperre in Kombination.

## 5. Plattform & Architektur

Die App braucht einen **Dienst, der ständig im Netz läuft** (Erkennung + Durchsetzung). Ein reines Handy-App-Konzept funktioniert nicht, weil Handys kein dauerhaftes Rohpaket-Lauschen erlauben und nicht immer zuhause sind.

```
┌────────────────────────── Heimnetz ──────────────────────────┐
│                                                               │
│  Router (FritzBox/UniFi/…) ◄── API ──┐                        │
│                                       │                       │
│  ┌──────────── Backend-Dienst (Docker auf Pi/NAS) ─────────┐  │
│  │ Scanner: ARP/DHCP-Sniffer, ARP-Scan, Router-Poller       │  │
│  │ Geräte-DB (SQLite)                                       │  │
│  │ Regel-Engine: neu → Status „unbekannt“ → Benachrichtigen │  │
│  │ Enforcer: Router-Adapter | ARP-Block | DNS                │  │
│  │ REST-API + WebSocket                                     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                         ▲                                     │
└─────────────────────────┼─────────────────────────────────────┘
                          │ (lokal oder per VPN/Tailscale)
              Web-UI (PWA) / Handy-App  ◄── Push (ntfy / Telegram / FCM)
```

### Optionen

| Option | Backend | Frontend | Bewertung |
|---|---|---|---|
| **A (Empfehlung)** | Python (FastAPI, scapy, `fritzconnection`) in Docker | Web-UI als PWA (am Handy installierbar), Push über ntfy oder Telegram | schnellster Weg, beste Bibliotheken für Netzwerk & FritzBox, läuft auf Raspberry Pi |
| B | Go (gopacket) – ein einzelnes Binary | gleiche PWA | schlanker, sehr robust; etwas mehr Eigenbau bei Router-APIs |
| C | wie A oder B | native Handy-App (Flutter/React Native) | schönere Push-Integration, aber deutlich mehr Aufwand – erst später sinnvoll |
| D | Home-Assistant-Integration | HA-Dashboard + HA-Companion-App | ideal, falls bereits Home Assistant läuft |

Datenmodell (Kern):

- `device`: mac, vendor, name, notes, status (`unknown` | `allowed` | `blocked`), first_seen, last_seen, is_random_mac
- `sighting`: device, ip, hostname, source (arp/dhcp/router/mdns), timestamp
- `event`: device, type (new/online/offline/allowed/blocked), timestamp

Sicherheit: Weboberfläche nur im LAN oder über VPN erreichbar, Login Pflicht, Router-Zugangsdaten nur als Umgebungsvariablen/Secrets, eigener eingeschränkter Router-Benutzer.

## 6. Umsetzungsschritte

1. **Grundgerüst**: Repo-Struktur, Docker-Compose, SQLite, FastAPI, CI (Lint + Tests).
2. **Erkennung**: ARP-/DHCP-Sniffer + periodischer ARP-Scan, OUI-Herstellerdatenbank, Geräteinventar.
3. **Web-UI**: Geräteliste, Status „unbekannt/erlaubt/gesperrt“, Umbenennen, Verlauf.
4. **Benachrichtigung**: ntfy oder Telegram-Bot mit Buttons „Erlauben“/„Sperren“ direkt in der Nachricht.
5. **Router-Adapter** für Jans Router (z. B. FritzBox via TR-064), inkl. Quarantäne-Modus.
6. **Fallback-Enforcer**: ARP-Block (optional, standardmäßig aus).
7. **Feinschliff**: MAC-Randomisierung, Online/Offline-Verlauf, Zeitpläne, Backup.

Schritte 1–4 sind routerunabhängig und können sofort beginnen.

## 7. Risiken & Grenzen

- Ohne Router-Unterstützung keine echte Vorab-Sperre – ein Gerät mit WLAN-Passwort ist zunächst im Netz.
- Zufällige MAC-Adressen erzeugen „falsche“ neue Geräte.
- Mesh-Repeater, Gast-WLAN und VLANs können Geräte vor dem Sniffer verstecken → Router-API als zweite Quelle.
- Inoffizielle APIs (UniFi, Web-Oberflächen) können sich mit Firmware-Updates ändern.
- ARP-Spoofing nur im eigenen Netz und mit Bedacht einsetzen.

## 8. Offene Fragen an Jan

1. **Welcher Router?** (Modell, z. B. FritzBox 7590, UniFi, Speedport, Vodafone-Station, OpenWrt) Gibt es Mesh-Repeater oder ein separates Access-Point-System?
2. **Wo soll der Dienst laufen?** Raspberry Pi, NAS (Synology/QNAP), Heimserver mit Docker, oder läuft schon Home Assistant?
3. **Wie willst du entscheiden?** Web-Oberfläche/PWA am Handy, Telegram-Bot, native App?
4. **Was bedeutet „sperren“ für dich?** Nur kein Internet (einfach, FritzBox-tauglich) oder komplett aus dem Netz (braucht WLAN-Sperre/VLAN)?
5. **Bevorzugte Programmiersprache**, falls du selbst mitentwickeln willst?
