# Pocket ID

[Pocket ID](https://pocket-id.org) ist ein Anmeldedienst (OIDC) mit Passkeys: Eine Person meldet sich einmal mit
Fingerabdruck oder Gesichtserkennung an und kommt damit in alle angebundenen Apps (Nextcloud, Immich, Paperless …).

Das Modul **pocket-id** verwaltet die Benutzer von Pocket ID aus Home-Net und der App heraus:

- Benutzer ansehen, anlegen, umbenennen, sperren und löschen
- Einladungslink erzeugen (oder per E-Mail schicken), mit dem die Person ihren Passkey anlegt
- je Benutzer die Apps an- und abhaken
- sehen, wer welche App nutzen darf

Verwalten dürfen nur Eigentümer und Admins von Home-Net. Admins von Pocket ID selbst lassen sich hier nicht sperren
oder löschen, das geht nur in Pocket ID.

## Installieren

```bash
./home-net add pocket-id              # fragt, ob schon ein Pocket ID läuft
./home-net add pocket-id --existing   # vorhandenes Pocket ID nutzen
./home-net add pocket-id --with-server --url https://id.example.org   # Pocket ID gleich mitinstallieren
```

### Mit vorhandenem Pocket ID

1. In Pocket ID unter *Einstellungen → Admin → API-Schlüssel* einen Schlüssel anlegen.
2. In Home-Net beim Modul Pocket ID unter *Einstellungen* eintragen. Alternativ die Datei `api-key` mit dem Schlüssel
   in `<Datenordner>/pocket-id/config/` legen (Rechte 600).
3. Die Adresse ist vorbelegt mit `http://host.docker.internal:1411`, also Pocket ID auf demselben Rechner mit Port
   1411. Läuft es woanders, die Adresse im Heimnetz eintragen.

### Pocket ID mitinstallieren (Modul pocket-id-server)

Home-Net startet dann den Container `pocket-id-server` (`ghcr.io/pocket-id/pocket-id:v2`, Port 1411, Daten in
`<Datenordner>/pocket-id-server`). Den Schlüssel zum Verschlüsseln und einen API-Schlüssel erzeugt Home-Net selbst
(`pocket-id-server/secrets`, Rechte 600) und gibt den API-Schlüssel an das Modul weiter. Es muss also nichts von Hand
eingetragen werden.

Danach:

1. **Reverse-Proxy:** Passkeys funktionieren nur über https. Im Reverse-Proxy (z. B. Nginx Proxy Manager) die Adresse
   aus `--url` auf `http://<server>:1411` leiten, mit Let's-Encrypt-Zertifikat und „Force SSL“.
2. **Erster Admin:** `https://id.example.org/setup` öffnen und den ersten Admin mit Passkey anlegen.
3. Optional **E-Mail** in Pocket ID unter *Einstellungen → E-Mail* (SMTP), damit Einladungen per E-Mail gehen.
4. Optional **fail2ban**: ein Jail auf das Zugriffsprotokoll des Proxy-Hosts gegen viele Fehlversuche.

Auf Unraid gehört die Datenbank von Pocket ID (SQLite) auf einen Pool wie `/mnt/cache/...`, nicht auf `/mnt/user`.
Home-Net nutzt seinen Datenordner, der auf Unraid bereits auf dem Pool liegt.

## Apps anbinden

Für jede App wird in Pocket ID unter *Einstellungen → Admin → OIDC-Clients* ein Client angelegt und eine Gruppe, die
ihn nutzen darf. Bewährt hat sich **eine Gruppe je App** (`nextcloud_users`, `immich_users` …) und beim Client
„Auf Gruppen beschränken“. Dann entsprechen die Schalter im Modul genau den Apps. Clients ohne Beschränkung darf jeder
nutzen; das Modul zeigt sie unter *Apps* an.

Damit neue Personen in den Apps sofort ein Konto bekommen, wird in jeder App **„Konto beim ersten Login anlegen“**
eingeschaltet. Das ist sicher, weil Pocket ID nur Mitglieder der Gruppe durchlässt. Die Anmeldung mit Passwort bleibt
in den Apps als Notzugang an, die Registrierung mit Passwort aus.

Für alle unten: Issuer ist `https://id.example.org`, `<app>` steht für die Adresse der App.

| App | Callback-URL(s) in Pocket ID | PKCE | Wo die Einstellung in der App liegt |
| --- | --- | --- | --- |
| Nextcloud (App *user_oidc*) | `https://<app>/apps/user_oidc/code`; Backchannel-Logout `https://<app>/apps/user_oidc/backchannel-logout/PocketID` | ja | `occ user_oidc:provider PocketID --clientid=… --clientsecret=… --discoveryuri=https://id.example.org/.well-known/openid-configuration --mapping-uid=nextcloud_username --unique-uid=0`; `occ config:system:set user_oidc auto_provision --type=boolean --value=true`; `occ config:system:set user_oidc allow_multiple_user_backends --type=integer --value=1` |
| Immich | `https://<app>/auth/login`, `https://<app>/user-settings`, `app.immich:///oauth-callback` | ja | *Administration → Einstellungen → OAuth*: Issuer, Client-ID, Secret, Scope `openid email profile`, *Auto Register* an |
| Audiobookshelf | `https://<app>/auth/openid/callback`, `https://<app>/auth/openid/mobile-redirect` | nein | *Einstellungen → Authentifizierung → OpenID Connect*: Issuer, „Automatisch ausfüllen“, Client-ID, Secret, *Match existing users by* Benutzername, *Auto Register* an, Mobile-Redirect `audiobookshelf://oauth` |
| Paperless-ngx | `https://<app>/accounts/oidc/pocketid/login/callback/` | ja | Umgebung: `PAPERLESS_APPS=allauth.socialaccount.providers.openid_connect`, `PAPERLESS_SOCIALACCOUNT_PROVIDERS` (oder `…_FILE`) = `{"openid_connect":{"OAUTH_PKCE_ENABLED":true,"APPS":[{"provider_id":"pocketid","name":"Pocket ID","client_id":"…","secret":"…","settings":{"server_url":"https://id.example.org"}}]}}`, `PAPERLESS_SOCIALACCOUNT_ALLOW_SIGNUPS=true`, `PAPERLESS_SOCIAL_AUTO_SIGNUP=true`, `PAPERLESS_URL` |
| Dawarich | `https://<app>/users/auth/openid_connect/callback` | ja | Umgebung: `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ISSUER`, `OIDC_REDIRECT_URI`, `OIDC_PKCE_ENABLED=true`, `OIDC_AUTO_REGISTER=true`, `OIDC_PROVIDER_NAME=Pocket ID` |
| Linkwarden | `https://<app>/api/v1/auth/callback/authentik` | nein | `.env`: `NEXT_PUBLIC_AUTHENTIK_ENABLED=true`, `AUTHENTIK_CUSTOM_NAME=Pocket ID`, `AUTHENTIK_ISSUER=https://id.example.org`, `AUTHENTIK_CLIENT_ID`, `AUTHENTIK_CLIENT_SECRET`, `NEXTAUTH_URL=https://<app>/api/v1/auth`, `DISABLE_NEW_SSO_USERS=false`, `NEXT_PUBLIC_DISABLE_REGISTRATION=true` |

Hinweise:

- **Nextcloud** nimmt den Benutzernamen aus dem Zusatzwert (Custom Claim) `nextcloud_username`. Das Modul setzt bei
  neuen Benutzern jeden Zusatzwert, der bei allen anderen Benutzern gleich ihrem Benutzernamen ist, ebenfalls auf den
  Benutzernamen. Hat jemand in Nextcloud einen anderen Namen, den Wert in Pocket ID beim Benutzer ändern.
- **Vorhandene Konten** in einer App verbindet man mit Pocket ID, indem man sich einmal mit gleichem Benutzernamen
  bzw. gleicher E-Mail anmeldet (Audiobookshelf, Immich, Paperless), sonst in der App unter dem Konto verknüpfen.
- Secrets der Clients nie ins Repo, sondern in Dateien mit Rechten 600 neben den Daten der App.

## Wie das Modul mit Pocket ID spricht

Über die REST-API von Pocket ID mit dem API-Schlüssel (Header `X-API-Key`): `/api/users`, `/api/user-groups`,
`/api/oidc/clients`, `/api/users/<id>/user-groups`, `/api/users/<id>/one-time-access-token` und
`/api/custom-claims/user/<id>`. Eine App im Modul ist eine Gruppe, die mindestens einen Client freischaltet; ein
Schalter nimmt den Benutzer in diese Gruppe auf oder heraus. Die öffentliche Adresse für Einladungslinks liest das
Modul aus `/.well-known/openid-configuration` (Issuer). Getestet mit Pocket ID 2.19.
