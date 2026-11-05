# Nextcloud

Das Modul **nextcloud** zeigt in Home-Net und in der App, wie es einer [Nextcloud](https://nextcloud.com) geht, und
verwaltet ihre Benutzer:

- **Zustand:** Version, Wartungsmodus, freier Platz auf dem Datenträger, Dateien, Freigaben, aktive Benutzer
  (5 Minuten, Stunde, Tag), PHP und Datenbank
- **Updates:** neue Version von Nextcloud und App-Updates (installiert werden sie weiter in Nextcloud bzw. über das
  Docker-Image)
- **Benutzer:** anlegen (mit Passwort oder mit Link per E-Mail), Anzeigename, E-Mail, Speicherplatz und Passwort ändern,
  Gruppen an- und abwählen, sperren, entsperren, Willkommens-Mail schicken, löschen (nur nach Eintippen des
  Benutzernamens, weil die Dateien mit gelöscht werden)
- **Gruppen:** anlegen

Den Zustand sieht jeder mit Zugriff auf das Modul. Die Benutzerliste, alle Änderungen und die Einstellungen nur
Eigentümer und Admins von Home-Net. Das Konto, mit dem sich Home-Net anmeldet, lässt sich hier weder sperren noch
löschen noch aus der Gruppe admin nehmen.

**Meldungen** (in Home-Net unter *Mein Konto* wählbar): Nextcloud antwortet zweimal hintereinander nicht (und wieder,
wenn sie zurück ist), weniger als 20 GB frei, neue Version von Nextcloud oder einer App. Geprüft wird alle 10 Minuten,
jede Version wird nur einmal gemeldet.

## Installieren

```bash
./home-net add nextcloud
```

Home-Net meldet sich mit einem Admin-Konto der Nextcloud und einem **App-Passwort** an:

1. In Nextcloud mit einem Admin-Konto unter *Persönliche Einstellungen → Sicherheit* ganz unten bei *Geräte & Sitzungen*
   einen Namen eingeben (z. B. „Home-Net“) und *Neues App-Passwort erstellen*. Das geht auch, wenn sich das Konto sonst
   über OIDC (z. B. Pocket ID) anmeldet. Den angezeigten Benutzernamen und das Passwort notieren.
2. In Home-Net beim Modul Nextcloud unter *Einstellungen* die Adresse, den Benutzernamen und das App-Passwort eintragen.
   Das App-Passwort bleibt auf dem Server (`<Datenordner>/nextcloud/config/settings.json`, Rechte 600) und lässt sich in
   Nextcloud jederzeit wieder löschen.

Die Adresse ist die, unter der der Server Nextcloud erreicht: die öffentliche (z. B. `https://cloud.example.org`) oder
die im Heimnetz (z. B. `https://192.168.1.10:444`). Sie muss in der `config.php` von Nextcloud unter `trusted_domains`
stehen. Hat Nextcloud im Heimnetz nur ein eigenes Zertifikat, *Zertifikat nicht prüfen* wählen.

### Bestätigung mit dem Passwort

Ab Nextcloud 32 verlangt die API für jede Änderung an Benutzern (anlegen, ändern, sperren, Gruppen, löschen) eine
Bestätigung mit dem Passwort, die ein App-Passwort nicht geben kann. Das Modul fragt dann einmal nach dem normalen
Passwort des Kontos, prüft es bei Nextcloud und nutzt es 30 Minuten lang für Änderungen (wie Nextcloud selbst). Es
bleibt nur im Arbeitsspeicher des Dienstes, nie auf der Platte.

Hat das Konto eine Zwei-Faktor-Anmeldung, geht das nicht. Dann in der `config.php` von Nextcloud nur die Adressen
eintragen, von denen Home-Net kommt:

```php
'allowed_no_password_confirmation_ranges' => ['172.18.0.0/16'],
```

Spricht das Modul Nextcloud direkt an (z. B. `https://192.168.1.10:444`), ist das das Docker-Netz von Home-Net
(`docker network inspect home-net_default`). Über einen Reverse Proxy sieht Nextcloud nur den Proxy, und die Freigabe
gälte für alle, die über ihn kommen.

Für Speicher, aktive Benutzer und Updates braucht Nextcloud die mitgelieferte App *Monitoring* (`serverinfo`). Ohne sie
zeigt das Modul nur Version und Benutzer.

## Hinweise

- Wer sich über OIDC (z. B. Pocket ID mit `user_oidc`) anmeldet, bekommt sein Konto meist beim ersten Anmelden. Name und
  E-Mail solcher Konten überschreibt Nextcloud bei jeder Anmeldung mit den Angaben von Pocket ID; ein Passwort haben sie
  nicht. Neue Personen legt man darum besser in Pocket ID an (Modul `pocket-id`) und gibt ihnen dort die App Nextcloud.
- Ohne Passwort schickt Nextcloud beim Anlegen eine Mail mit einem Link zum Festlegen des Passworts. Dafür muss in
  Nextcloud der Mailversand eingerichtet sein.
- Speicherplatz: Zahl mit Einheit (`5 GB`, `500 MB`), leer für den Standard der Nextcloud, `unbegrenzt` für keine Grenze.

## Schnittstelle

Über den Zugang unter `/m/nextcloud/`:

| Methode und Pfad | Wer | Was |
| --- | --- | --- |
| `GET /api/overview` | alle | Zustand, Speicher, Updates; für Admins auch `users`, `groups`, `account` |
| `GET/PUT /api/settings` | Admins | Adresse, Benutzer, App-Passwort (nur `password_set` kommt zurück), `verify_tls` |
| `POST /api/users` | Admins | `{id, name, email, password, quota, groups}` |
| `PUT /api/users/<id>` | Admins | `{name, email, quota, password, enabled}`, jedes Feld freiwillig |
| `PUT /api/users/<id>/groups` | Admins | `{group, member}` |
| `POST /api/users/<id>/welcome` | Admins | Willkommens-Mail |
| `DELETE /api/users/<id>?confirm=<id>` | Admins | Löschen mit allen Dateien |
| `POST /api/groups` | Admins | `{name}` |
| `POST /api/confirm` | Admins | `{password}`: Passwort des Kontos für 30 Minuten (nur im Speicher) |

Verlangt Nextcloud die Bestätigung, antwortet eine Änderung mit 403 und `{"confirm": true}`.
