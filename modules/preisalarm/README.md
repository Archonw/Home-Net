# Preisalarm

Beobachtet die Preise von Produkten über lange Zeit und meldet sich in der App, wenn ein Preis wirklich gut ist:

- **Zielpreis erreicht:** der Preis liegt auf oder unter dem Preis, den du eingetragen hast.
- **Deutlich unter Durchschnitt:** der Preis liegt mindestens so viele Prozent unter dem bisherigen Durchschnitt, wie
  beim Produkt eingetragen (Standard 15 %). Zählt ab 5 Messungen.
- **Neuer Tiefstpreis:** günstiger als je zuvor gemessen, ebenfalls ab 5 Messungen.

Derselbe Preis wird nur einmal gemeldet. Fällt er weiter, kommt eine neue Meldung; steigt er wieder, gilt der nächste
gute Preis wieder als neu.

Dazu liest das Modul einen **Deal-Feed** (Standard: neue Deals von mydealz) und meldet Deals, deren Titel zu den
Suchbegriffen eines Produkts passen. Das fängt Gutscheincodes und Blitzangebote, die Preisvergleiche nicht kennen.
Deals zählen nicht in den Durchschnitt.

## Einrichten

1. Auf dem Server: `./home-net add preisalarm`
2. In Home-Net das Modul **Preisalarm** öffnen und mit **Produkt hinzufügen** ein Produkt eintragen:
   - **Produktseite:** am besten die Seite des genauen Produkts bei geizhals.de oder idealo.de, denn dort steht der
     günstigste Preis aller Shops. Eine Shopseite geht auch. Wichtig ist das genaue Produkt (mit Artikelnummer), nicht
     eine Suche, sonst mischen sich verschiedene Varianten im Durchschnitt.
   - **Zielpreis** (optional), **Alarm ab % unter Durchschnitt** und **Suchbegriffe für Deals**, z. B. `Einhell 4,0 Ah`.
     Ein Deal passt, wenn alle Wörter eines Begriffs im Titel stehen.
3. **Jetzt prüfen** liest sofort alle Preise. Danach prüft das Modul alle 6 Stunden (unter **Einstellungen** änderbar).
4. In der App unter **Einstellungen → Benachrichtigungen** die Meldungen „Ein Produkt hat einen guten Preis“ und „Ein
   Deal passt zu einem Produkt“ wählen.

Ansehen darf jeder mit Zugriff auf das Modul, Produkte und Einstellungen ändern nur Eigentümer und Admins.

## Wie der Preis gelesen wird

Das Modul liest die strukturierten Produktdaten der Seite (JSON-LD `offers.lowPrice` oder `price`, sonst Microdata
und Meta-Tags). Die nutzen geizhals, idealo und die meisten Shops, weil Suchmaschinen sie auswerten. Findet es keinen
Preis, steht die Fehlermeldung beim Produkt. Alles läuft auf dem eigenen Server und über den eigenen Anschluss; es gibt
keinen Dienst dazwischen.

Daten: `${DATA_DIR}/preisalarm/config/state.json` (Produkte, Verlauf, zuletzt gemeldete Preise, gesehene Deals).
