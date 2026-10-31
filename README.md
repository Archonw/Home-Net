# Home-Net

Android-App als gemeinsame Oberfläche für die Dienste im Heimnetz. Die Startseite zeigt eine Kachel je Dienst:

- **Netzwerk:** [Networkmonitor](https://github.com/Archonw/Networkmonitor), überwacht die Geräte im Netz
- **Pi-Wartung:** Pi-Wartung, sichert Raspberry Pis als Images auf dem Unraid

Die Dienste laufen auf dem Server, die App spricht über deren Schnittstellen mit ihnen. Der Code stammt aus dem
Ordner `android/` des Networkmonitor-Repos und wurde mit seiner Git-Geschichte übernommen. Die Paketkennung
`de.archonw.netmon` bleibt gleich, damit Updates über die bestehende Installation laufen.

## Installation und Updates

GitHub Actions baut bei jedem Push die APK. Nach einem Push auf `main` liegt sie unter **Releases** als
`netzwerkmonitor.apk`. Der Dateiname bleibt, weil der Monitor neue Versionen darüber erkennt und ans Handy weitergibt.

Damit Updates ankommen, muss im Networkmonitor unter *Einstellungen* das Repository für App-Updates auf
`Archonw/Home-Net` stehen. Die Versionsnummern laufen oberhalb der alten Releases weiter.

Signiert wird mit den Secrets `NETMON_KEYSTORE_BASE64`, `NETMON_KEYSTORE_PASSWORD`, `NETMON_KEY_ALIAS` und
`NETMON_KEY_PASSWORD`.

## Eine weitere App hinzufügen

Eine neue Kachel kommt in `HomeScreen`, dazu ein Eintrag in `Screen` und die Adresse in `Settings`.

## Lizenz

[Apache License 2.0](LICENSE)
