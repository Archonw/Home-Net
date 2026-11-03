# Proxy Manager

Das Modul **proxy-manager** zeigt in Home-Net und in der App, was ein [Nginx Proxy Manager](https://nginxproxymanager.com)
(NPM) nach außen freigibt:

- welche Adressen **öffentlich** sind, welche nur **mit Passwort** oder nur für **bestimmte Adressen** von außen gehen und
  welche **nur intern** erreichbar sind (über eine Zugriffsliste, die nur das Heimnetz und VPN zulässt)
- wann die **Zertifikate** ablaufen, und welche von keiner Adresse mehr genutzt werden
- ob eine öffentliche Adresse im Internet überhaupt eingetragen ist (gefragt wird direkt bei 1.1.1.1, damit lokale
  DNS-Einträge nicht täuschen), und ob sie dort nur per IPv6 erreichbar ist
- ob das Ziel hinter einer Adresse noch antwortet
- weitergeleitete Ports (Streams)

Dazu kann man Zertifikate von Let's Encrypt sofort erneuern und Adressen ab- und wieder einschalten.

Sehen und ändern dürfen nur Eigentümer und Admins von Home-Net, denn die Liste zeigt, was von außen erreichbar ist.

**Meldungen** (in Home-Net unter *Mein Konto* wählbar): Zertifikat läuft in weniger als 14 Tagen ab, Zertifikat
abgelaufen, und (aus, wenn nicht gewählt) Ziel einer Adresse antwortet zweimal hintereinander nicht. Geprüft wird alle
30 Minuten, gemeldet wird jedes Zertifikat nur einmal je Stufe.

## Installieren

```bash
./home-net add proxy-manager
```

NPM kennt keine API-Schlüssel. Home-Net meldet sich darum mit einem eigenen NPM-Benutzer an:

1. Im Proxy Manager unter *Benutzer → Benutzer hinzufügen* einen Benutzer anlegen, z. B. „Home-Net“ mit einer
   E-Mail-Adresse, die es nicht geben muss. Kein Administrator.
2. Bei *Berechtigungen*: Sichtbarkeit „Alle Einträge“, bei Proxy-Hosts, Weiterleitungen, 404-Hosts, Streams,
   Zugriffslisten und Zertifikaten „Nur ansehen“. Wer aus Home-Net Zertifikate erneuern oder Adressen abschalten will,
   gibt dort „Verwalten“.
3. Über *Passwort ändern* ein Passwort setzen.
4. In Home-Net beim Modul Proxy Manager unter *Einstellungen* E-Mail und Passwort eintragen. Das Passwort bleibt auf dem
   Server (`<Datenordner>/proxy-manager/config/settings.json`, Rechte 600).

Die Adresse ist vorbelegt mit `http://host.docker.internal:81`, also NPM auf demselben Rechner mit der Verwaltung auf
Port 81. Läuft NPM woanders oder mit eigener IP (z. B. `br0` auf Unraid), dort die Adresse im Heimnetz eintragen.
Eine Zwei-Faktor-Anmeldung darf der Benutzer für Home-Net nicht haben.

## Was „nur intern“ bedeutet

NPM schreibt die Regeln einer Zugriffsliste der Reihe nach in nginx und hängt `deny all` an. Das Modul wertet sie so aus:

| Zugriffsliste | Ergebnis |
| --- | --- |
| keine | Öffentlich |
| erlaubt nur Heimnetz/VPN (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7 …), kein Passwort | Nur intern |
| erlaubt `all` | Öffentlich |
| erlaubt einzelne öffentliche Adressen | Nur bestimmte Adressen |
| Passwort und „Jede erfüllt“ | Mit Passwort |

Ob der Router die Ports 80 und 443 überhaupt weiterleitet, sieht Home-Net nicht.
