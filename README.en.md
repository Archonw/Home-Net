# Home-Net

A modular toolkit for services in your home network, running on any Linux with Docker. You pick the modules you want
during installation or later. One login (password at home, password plus passkey from outside) covers all of them, and
an Android app shows one tile per module and delivers notifications from your own server, without Google services.

The web pages and the app are available in German and English; the app follows the phone's language.

*Deutsch: siehe [README.md](README.md).*

## Install

On a Linux machine with Docker, Compose and Python 3, including Raspberry Pi and Unraid (web terminal; install the
plugin "Docker Compose Manager" from **Apps** first):

```bash
curl -fsSL https://raw.githubusercontent.com/Archonw/Home-Net/main/install.sh | sh
```

The script puts Home-Net into `/mnt/user/appdata/home-net` on Unraid, `/opt/home-net` as root elsewhere
(`HOME_NET_DIR=<folder>` changes it), then asks for the modules, a user name and a password. Afterwards everything runs
at `http://<machine>:8093`. Running it again only fetches the new version. Without questions:
`… | sh -s -- --modules netzwerkmonitor,pihole --owner anna --password '…'`. The command `home-net` is then
`<folder>/repo/home-net`.
A fork installs from itself: updates, containers and the app then come from your fork
([docs/APP-UPDATES.md](docs/APP-UPDATES.md#eigener-fork)).

### Versions and updates

- **stabil** (default): the newest release `vX.Y.Z`. Containers come prebuilt for amd64 and arm64 from
  `ghcr.io/archonw/home-net`, so installing takes minutes and also works on a Raspberry Pi.
- **main**: the latest code, containers are built on your machine. For testers and developers.

| Command | Effect |
| --- | --- |
| `./home-net modules` | List available modules |
| `./home-net add <module>` / `remove <module>` | Add or stop a module; its data stays |
| `./home-net status` | Show what is running |
| `./home-net update` | Download the new version and restart; on failure the old one keeps running |
| `./home-net version` / `kanal [stabil\|main]` | Show the version, switch the channel |
| `./home-net password` | Change a password |
| `./home-net heimnetz` | Show or set which networks count as the home network |
| `./home-net backup` / `restore <archive>` | Back up or restore settings and secrets |

## Modules

| Module | What it does |
| --- | --- |
| `netzwerkmonitor` | Watches the devices in your network and reports new ones. With a FritzBox it can also cut devices off the internet; with any other router it finds devices on its own ([README](modules/netzwerkmonitor/README.md)) |
| `pi-wartung` | Backs up Raspberry Pis as images you can write to SD cards, and keeps their packages up to date |
| `pc-sicherung` | Full-disk backups of PCs (Windows, Linux, dual boot) via network boot, and restore ([README](modules/pc-sicherung/README.md)) |
| `unraid` | Manages an Unraid server: shares, users, Docker, VMs, User Scripts ([README](modules/unraid/README.md)) |
| `pocket-id` | Manages Pocket ID users (passkey login): create, invite, lock, assign apps ([README](modules/pocket-id/README.md)); `pocket-id-server` installs Pocket ID itself |
| `proxy-manager` | Shows which addresses Nginx Proxy Manager exposes publicly, which are internal, and when certificates expire ([README](modules/proxy-manager/README.md)) |
| `nextcloud` | Shows state, storage and updates of a Nextcloud and manages its users and groups ([README](modules/nextcloud/README.md)) |
| `wireguard` | Manages WireGuard-Easy VPN clients, QR code in the app ([README](modules/wireguard/README.md)); `wireguard-server` installs WireGuard-Easy itself |
| `pihole` | Pauses ad blocking on all Pi-holes at once and shows combined statistics ([README](modules/pihole/README.md)) |

Screenshots of the app are in [README.md](README.md#so-sieht-die-app-aus) (example data only).

## Accounts and login

- **Home network:** devices in the networks the server is connected to (and VPN users of a WireGuard container) log in
  with the password alone. Anything arriving through a reverse proxy or tunnel (Nginx Proxy Manager, Cloudflare Tunnel,
  Tailscale Funnel, ngrok) counts as "outside" and also needs a passkey.
- **Roles:** the owner may do everything, admins manage users, users only see the services and categories shared with
  them. Groups add rights to all their members.
- **App:** get a pairing code under **Mein Konto** (My account) and enter it in the app under Settings → Access. The app
  itself is downloaded from `https://<home-net>/app` and updates from there.
- **Lockout:** 5 wrong passwords lock that address for 15 minutes.

Never forward the Home-Net port to the internet directly, only through a reverse proxy with HTTPS.

## Contributing and security

Issues and pull requests are welcome, in English too: [CONTRIBUTING.md](CONTRIBUTING.md). Writing a module:
[docs/MODULE.md](docs/MODULE.md). Please report security issues privately: [SECURITY.md](SECURITY.md).

## License

[Apache License 2.0](LICENSE)
