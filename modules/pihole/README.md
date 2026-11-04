# Pi-hole

Zeigt in Home-Net und in der App, ob der Werbefilter läuft, pausiert ihn kurz und zeigt die Statistik der letzten 24 Stunden.
Gedacht für ein Netz mit einem oder mehreren Pi-holes, die gemeinsam arbeiten (zum Beispiel gleich gehalten mit
[nebula-sync](https://github.com/lovelaze/nebula-sync) und mit einer gemeinsamen Adresse über keepalived):

- **Pausieren** geht immer an alle eingetragenen Pi-holes gleichzeitig, denn der Schalter wird nicht mit synchronisiert.
  Zur Wahl stehen 30 Sekunden, 5 Minuten, 30 Minuten und 1 Stunde; danach schalten sich die Pi-holes von selbst wieder ein.
  Ohne Ende pausieren dürfen nur Eigentümer und Admins von Home-Net.
- **Statistik:** Anfragen, geblockte Anfragen, Verlauf, häufigste geblockte und erlaubte Domains und die aktivsten Geräte,
  zusammengezählt über alle Pi-holes. Ist eins nicht erreichbar, steht das dabei.

- **Ein Pi-hole antippen** zeigt seine eigenen Zahlen, die Version (mit Hinweis auf Updates), die Blocklisten und das
  Gerät: Modell, Laufzeit, Temperatur, Prozessorlast und Arbeitsspeicher, dazu die Hinweise von Pi-hole selbst.
- **Meldungen** in der App: wenn ein Pi-hole drei Minuten lang nicht antwortet, wenn es wieder da ist, und wenn es
  eine neue Pi-hole-Version gibt (alle sechs Stunden geprüft, nur für Eigentümer und Admins). Jedes Konto wählt unter
  **Mein Konto** oder in der App unter **Einstellungen → Benachrichtigungen**, welche es haben möchte.
- **Aktualisieren** (nur Eigentümer und Admins): ein Knopf je Pi-hole und „Alle aktualisieren“. Home-Net meldet sich
  dafür per SSH an und startet `pihole -up`; mehrere Pi-holes kommen nacheinander dran, damit immer welche antworten.
  Ob es geklappt hat, steht beim Pi-hole und kommt als Meldung.

Wer das Modul sieht, legt der Zugang fest (Rechte je Dienst unter **Benutzer**).

## Einrichten

1. Auf dem Server: `./home-net add pihole`
2. In Home-Net im Browser das Modul **Pi-hole** öffnen und unter **Einstellungen** jedes Pi-hole eintragen: Name,
   Adresse (z. B. `http://192.168.1.2`) und Passwort. Besser als das normale Passwort ist ein App-Passwort: in Pi-hole unter
   **Einstellungen → Web-Oberfläche / API → App-Passwort** erzeugen (Expertenmodus). Es bleibt nur auf dem Server.

Nötig ist Pi-hole 6, ältere Versionen haben diese Schnittstelle nicht. Pi-hole mit https und eigenem Zertifikat geht auch;
das Zertifikat wird im Heimnetz nicht geprüft.

### Aktualisieren einrichten

Die Schnittstelle von Pi-hole kann sich nicht selbst aktualisieren, darum geht das über SSH. Home-Net legt dafür beim
ersten Start einen eigenen Schlüssel an (`<Daten>/pihole/config/ssh`). Unter **Einstellungen → Aktualisieren** steht eine
fertige Zeile; die kommt auf jedem Pi-hole in `~/.ssh/authorized_keys` des SSH-Benutzers, meist `root`:

```sh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '<die Zeile aus den Einstellungen>' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```

Die Zeile legt fest, dass der Schlüssel nur `pihole -up` (und die Prüfung `pihole -up --check-only`) starten darf, kein
Terminal, keine Weiterleitung. Benutzer und Port lassen sich je Pi-hole in den Einstellungen ändern; der Benutzer muss
`pihole -up` ausführen dürfen, also `root` sein. Pi-hole im Docker-Container wird nicht so aktualisiert, sondern über ein
neues Image.

Ein Pi-hole ohne Passwort funktioniert, lässt sich dann aber von jedem im Netz umstellen. Ein Passwort ist darum ratsam.
