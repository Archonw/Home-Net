# Zugriff von unterwegs (Portfreigabe + Reverse Proxy)

Ziel: Weboberfläche und Android-App funktionieren auch außerhalb des Heimnetzes, ohne VPN.

```
Handy (Mobilfunk) ──https──▶ FRITZ!Box :443 ──▶ Reverse Proxy (Unraid) ──http──▶ Monitor 192.168.178.207:8085
```

Der Monitor selbst bleibt unverändert im Heimnetz auf Port 8085. Nur der Reverse Proxy ist von außen
erreichbar, und nur über HTTPS.

## Was der Monitor von sich aus absichert

- **Anmeldesperre:** Nach 5 falschen Passwörtern innerhalb von 15 Minuten wird der Absender für
  15 Minuten gesperrt (HTTP 429). Hinter dem Proxy zählt die echte Adresse aus `X-Forwarded-For`.
  Andere Absender, etwa das Heimnetz, sind davon nicht betroffen.
- **Protokoll für fail2ban:** Jeder Fehlversuch steht im Container-Log als
  `Anmeldung fehlgeschlagen von <IP>`.
- **Ersteinrichtung nur im Heimnetz:** Solange noch das Standardpasswort gilt, lässt sich das eigene
  Passwort nicht über den Proxy setzen.

Das Passwort der Weboberfläche ist der einzige Schutz vor dem Sperren-Knopf. Es sollte lang und
zufällig sein (mindestens 16 Zeichen) und nirgends sonst verwendet werden.

## 1. Adresse im Internet

Du brauchst einen Namen, der auf deine öffentliche IP zeigt:

- **MyFRITZ-Adresse** (z. B. `abc123xyz.myfritz.net`, zu finden unter *Internet → MyFRITZ!-Konto*).
  Kostet nichts und wird von der FRITZ!Box selbst aktuell gehalten.
- **Eigene Domain** mit DynDNS (z. B. über `ddns-updater`), etwa `netmon.example.de`.

Hinweis Kabel/Vodafone: Hat der Anschluss nur DS-Lite (keine eigene öffentliche IPv4), funktioniert
eine IPv4-Portfreigabe nicht. Das siehst du unter *Internet → Online-Monitor*. Dann geht es nur über
IPv6 oder einen Tunnel.

## 2. Portfreigabe in der FRITZ!Box

*Internet → Freigaben → Portfreigaben → Gerät für Freigaben hinzufügen* → Unraid (192.168.178.207):

| Extern | Intern (Proxy) | Wofür |
|---|---|---|
| TCP 443 | HTTPS-Port des Proxys | Zugriff |
| TCP 80 | HTTP-Port des Proxys | nur für das Let's-Encrypt-Zertifikat |

Port 8085 (Monitor) und 8091 (ntfy) werden **nicht** freigegeben.

Falls der FRITZ!Box-Fernzugang selbst Port 443 nutzt (*Internet → Freigaben → FRITZ!Box-Dienste*),
muss er auf einen anderen Port oder ausgeschaltet werden.

## 3. Reverse Proxy (Beispiel Nginx Proxy Manager)

*Hosts → Proxy Hosts → Add Proxy Host*:

- Domain: deine Adresse aus Schritt 1
- Scheme `http`, Forward Hostname `192.168.178.207`, Port `8085`
- *Block Common Exploits* an
- Reiter SSL: neues Let's-Encrypt-Zertifikat, *Force SSL* und *HSTS* an

**Keine** zusätzliche Access List mit Benutzer/Passwort davorschalten: Monitor und App melden sich
selbst per Basic Auth an, eine zweite Basic-Auth-Abfrage am Proxy würde die App aussperren.

Andere Proxys (SWAG, Traefik, Zoraxy) funktionieren genauso; wichtig ist nur, dass sie
`X-Forwarded-For` setzen, was alle standardmäßig tun.

## 4. Monitor einstellen

Weboberfläche → *Einstellungen* → **Adresse dieser Weboberfläche (für Links in Nachrichten)** auf die neue `https://…`-Adresse setzen.
Dann führen die Links in den Benachrichtigungen auch unterwegs zum Gerät.

## 5. Android-App

*Einstellungen → Verbindung*:

- **Adresse im Heimnetz:** `http://192.168.178.207:8085` (wie bisher)
- **Adresse unterwegs:** `https://<deine Adresse>`

Die App probiert zuerst die Adresse, die zuletzt funktioniert hat, und weicht bei Bedarf auf die
andere aus. Unterwegs dauert die allererste Anfrage deshalb ein paar Sekunden länger.

## 6. Benachrichtigungen unterwegs (ntfy)

ntfy läuft nur im Heimnetz, deshalb kommen Pushs unterwegs nicht an. Damit das klappt:

1. Zweiten Proxy-Host für ntfy anlegen (z. B. `ntfy.example.de` → `192.168.178.207:8091`,
   mit Websockets-Unterstützung an).
2. ntfy absichern, damit niemand Fremdes mitliest oder Nachrichten schickt (`server.yml`):

   ```yaml
   base-url: "https://ntfy.example.de"
   behind-proxy: true
   auth-file: "/var/lib/ntfy/user.db"
   auth-default-access: "deny-all"
   ```

3. Im ntfy-Container einen Benutzer und ein Token anlegen:

   ```sh
   ntfy user add jan
   ntfy access jan netzwerkmonitor rw
   ntfy token add jan
   ```

4. Im Monitor unter *Einstellungen* das Token bei ntfy unter **Zugriffstoken** eintragen.
5. In der ntfy-App auf dem Handy den Server `https://ntfy.example.de` mit Benutzer `jan` hinzufügen
   und das Thema `netzwerkmonitor` dort abonnieren.

## 7. Prüfen

Mit dem Handy im Mobilfunk (WLAN aus):

- `https://<deine Adresse>` öffnet die Anmeldung, das Zertifikat ist gültig.
- `http://<öffentliche IP>:8085` ist **nicht** erreichbar.
- Fünfmal ein falsches Passwort → danach meldet die Seite „Zu viele falsche Passwörter“.
