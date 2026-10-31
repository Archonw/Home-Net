# Plan

Home-Net wird ein Baukasten für jedes Linux mit Docker. Die Schritte:

1. Rücksicherung prüfen: erledigt, ein Image wurde auf SD-Karte geschrieben und gestartet
2. Bestand absichern und die Neuinstallation aufschreiben: `tools/backup-config.sh`, `docs/NEUINSTALLATION.md`
3. Repos zusammenführen
4. Baustein-Beschreibung je Modul
5. Pi-Wartung-Sicherung im Container, ohne Bindung an Unraid
6. Gemeinsamer Zugang: eine Adresse, eine Anmeldung, die App holt ihre Kacheln vom Server
7. Installationsprogramm: `home-net install`, `home-net add <modul>`
8. Weboberfläche ersetzt die PC-App
