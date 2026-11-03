"""Reads Nginx Proxy Manager (https://nginxproxymanager.com) through its REST API and judges what each address shows.

Nginx Proxy Manager has no API keys: the module signs in with the e-mail and password of an NPM user and gets a token
for a day. A user that may only view is enough for the overview; renewing certificates and switching hosts on or off
needs a user that may also change.

Whether an address can be reached from outside depends on the access list of the host: a list that only lets in
addresses of the home network (and no password) keeps it internal. A host without list is open to everyone.
"""
import ipaddress
import json
import random
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

TIMEOUT = 15
SOON_DAYS = 21          # shown as "läuft bald ab"
WARN_DAYS = 14          # notification; Let's Encrypt certificates are renewed by NPM 30 days ahead
PUBLIC_DNS = ("1.1.1.1", "9.9.9.9")
DNS_CACHE_S = 600
TARGET_CACHE_S = 60
# Home networks, VPNs (100.64.0.0/10 as used by Tailscale) and the server itself. Python's is_private would also
# count documentation and other special ranges, so the list is explicit.
INTERNAL = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10"))

EXPOSURE_LABELS = {
    "public": "Öffentlich",
    "password": "Mit Passwort",
    "restricted": "Nur bestimmte Adressen",
    "internal": "Nur intern",
    "off": "Abgeschaltet",
}


class NpmError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


# --- Bewertung -----------------------------------------------------------------------------------

def is_internal(address):
    """True for a rule address that only covers the home network, VPN or the server itself."""
    text = str(address or "").strip()
    if text in ("", "all"):
        return False
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return False  # a host name: nginx takes it as is, we cannot tell
    if net.prefixlen == 0:
        return False
    return any(net.version == inner.version and net.subnet_of(inner) for inner in INTERNAL)


def covers_all(address):
    text = str(address or "").strip()
    if text == "all":
        return True
    try:
        return ipaddress.ip_network(text, strict=False).prefixlen == 0
    except ValueError:
        return False


def exposure(enabled, access):
    """(kind, note) for a host. access: None or {"name", "satisfy_any", "clients": [...], "users": n}."""
    if not enabled:
        return "off", ""
    if not access:
        return "public", "ohne Zugriffsliste"
    # nginx checks the rules in order and NPM appends "deny all", so only allows before a deny-all count
    outside_any, outside_some = False, False
    for rule in access.get("clients") or []:
        address, directive = rule.get("address"), rule.get("directive")
        if directive == "deny" and covers_all(address):
            break
        if directive == "allow" and not is_internal(address):
            if covers_all(address):
                outside_any = True
            else:
                outside_some = True
    by_address = outside_any or outside_some
    has_password = access.get("users", 0) > 0
    name = access.get("name") or "Zugriffsliste"
    if access.get("satisfy_any"):
        # address OR password lets in
        if outside_any:
            return "public", f"Zugriffsliste „{name}“ lässt alle Adressen zu"
        if has_password:
            return "password", f"Zugriffsliste „{name}“: von außen mit Passwort"
        if outside_some:
            return "restricted", f"Zugriffsliste „{name}“: nur bestimmte Adressen von außen"
        return "internal", f"Zugriffsliste „{name}“"
    # address AND password needed
    if not by_address:
        return "internal", f"Zugriffsliste „{name}“"
    if has_password:
        return "password", f"Zugriffsliste „{name}“: von außen mit Passwort"
    if outside_any:
        return "public", f"Zugriffsliste „{name}“ lässt alle Adressen zu"
    return "restricted", f"Zugriffsliste „{name}“: nur bestimmte Adressen von außen"


def parse_time(value):
    """NPM returns '2026-12-01 10:00:00' (SQLite) or ISO text; both are UTC."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace("T", " ").rstrip("Z")
    text = text.split(".")[0].split("+")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


def cert_state(expires, now=None):
    if expires is None:
        return "unknown", None
    days = int((expires - (now or time.time())) // 86400)
    if expires <= (now or time.time()):
        return "expired", days
    return ("soon" if days < SOON_DAYS else "ok"), days


# --- Öffentliches DNS ----------------------------------------------------------------------------

def dns_query(name, server, timeout=3, qtype=1):
    """A (qtype 1) or AAAA (28) records of name, asked directly at a public resolver, so local DNS entries do not
    mislead."""
    ident = random.randrange(65536)
    question = b"".join(bytes([len(p)]) + p.encode("idna") for p in name.rstrip(".").split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(packet, (server, 53))
        data = s.recv(4096)
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != ident:
        raise OSError("falsche Antwort")
    rcode = flags & 0xF
    if rcode == 3:
        return []
    if rcode:
        raise OSError(f"DNS-Fehler {rcode}")
    pos = 12
    for _ in range(qd):
        pos = skip_name(data, pos) + 4
    out = []
    for _ in range(an):
        pos = skip_name(data, pos)
        rtype, _cls, _ttl, length = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 == qtype and length == 4:
            out.append(socket.inet_ntoa(data[pos:pos + 4]))
        elif rtype == 28 == qtype and length == 16:
            out.append(socket.inet_ntop(socket.AF_INET6, data[pos:pos + 16]))
        pos += length
    return out


def skip_name(data, pos):
    while True:
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + length


class Checks:
    """Public DNS and reachability of the targets, cached so the app can reload often."""

    def __init__(self, resolve=None, connect=None):
        self.resolve = resolve or self.public_dns
        self.connect = connect or self.tcp
        self.dns_cache, self.target_cache = {}, {}
        self.lock = threading.Lock()

    @staticmethod
    def public_dns(name):
        error = None
        for server in PUBLIC_DNS:
            try:
                return dns_query(name, server) + dns_query(name, server, qtype=28)
            except OSError as exc:
                error = exc
        raise error

    @staticmethod
    def tcp(host, port):
        """'up', 'down' or 'unknown' (a name only the proxy's own Docker network knows)."""
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            return "unknown"
        for family, kind, proto, _name, address in infos[:2]:
            try:
                with socket.socket(family, kind, proto) as s:
                    s.settimeout(3)
                    s.connect(address)
                    return "up"
            except OSError:
                continue
        return "down"

    def cached(self, cache, key, ttl, fn):
        with self.lock:
            hit = cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self.lock:
            cache[key] = (time.time(), value)
        return value

    def dns(self, domain):
        if domain.startswith("*."):
            return {"state": "wildcard", "ips": []}

        def ask():
            try:
                ips = self.resolve(domain)
            except OSError:
                return {"state": "error", "ips": []}
            if not ips:
                return {"state": "missing", "ips": []}
            if all(is_internal(ip) for ip in ips):
                return {"state": "private", "ips": ips}
            if all(":" in ip for ip in ips):
                return {"state": "ipv6", "ips": ips}  # unreachable from IPv4-only networks, and Let's Encrypt may fail
            return {"state": "ok", "ips": ips}
        return self.cached(self.dns_cache, domain, DNS_CACHE_S, ask)

    def target(self, host, port):
        if not host or not port:
            return "unknown"
        return self.cached(self.target_cache, (host, int(port)), TARGET_CACHE_S, lambda: self.connect(host, int(port)))


# --- API -----------------------------------------------------------------------------------------

class Npm:
    def __init__(self, settings, checks=None):
        self.settings = settings  # callable returning {"url", "email", "password"}
        self.checks = checks or Checks()
        self.token = ("", "", 0)  # (credentials key, token, valid until)
        self.lock = threading.Lock()

    def base(self):
        url = (self.settings().get("url") or "").rstrip("/")
        if not url:
            raise NpmError("Die Adresse des Proxy Managers fehlt. Bitte unter Einstellungen eintragen.", 400)
        return url

    def http(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base() + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise NpmError(self.explain(exc), exc.code)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NpmError(f"Der Proxy Manager ist nicht erreichbar ({reason}).")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise NpmError("Unter dieser Adresse antwortet kein Proxy Manager (Port 81?).")

    @staticmethod
    def explain(exc):
        try:
            payload = json.loads(exc.read() or b"{}")
            detail = (payload.get("error") or {}).get("message", "") if isinstance(payload, dict) else ""
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 404:
            return detail or "Im Proxy Manager nicht gefunden."
        return f"Der Proxy Manager meldet: {detail}" if detail else f"Der Proxy Manager antwortet mit Fehler {exc.code}."

    def login(self, force=False):
        s = self.settings()
        email, password = s.get("email") or "", s.get("password") or ""
        if not email or not password:
            raise NpmError("Die Anmeldung für den Proxy Manager fehlt. Bitte unter Einstellungen eintragen.", 400)
        key = f"{self.base()}|{email}|{hash(password)}"
        with self.lock:
            cached_key, token, until = self.token
            if not force and cached_key == key and token and time.time() < until:
                return token
            try:
                answer = self.http("POST", "/api/tokens", {"identity": email, "secret": password, "scope": "user"})
            except NpmError as exc:
                if exc.status in (400, 401, 403):
                    raise NpmError("Der Proxy Manager lehnt E-Mail oder Passwort ab.", 400)
                raise
            if not answer.get("token"):
                if answer.get("requires_2fa") or answer.get("challenge_token"):
                    raise NpmError("Für diesen Benutzer ist im Proxy Manager die Zwei-Faktor-Anmeldung an. Bitte einen "
                                   "eigenen Benutzer ohne Zwei-Faktor nur für Home-Net anlegen.", 400)
                raise NpmError("Der Proxy Manager hat keine Anmeldung zurückgegeben.")
            until = (parse_time(answer.get("expires")) or time.time() + 3600) - 300
            self.token = (key, answer["token"], until)
            return answer["token"]

    def request(self, method, path, body=None):
        try:
            return self.http(method, path, body, self.login())
        except NpmError as exc:
            if exc.status != 401:
                raise
        return self.http(method, path, body, self.login(force=True))

    def get(self, path):
        data = self.request("GET", path)
        return data if isinstance(data, list) else []

    def version(self):
        try:
            v = self.http("GET", "/api/").get("version") or ""
            if isinstance(v, dict):  # before 2.13: {"major": 2, "minor": 12, "revision": 3}
                return ".".join(str(v[k]) for k in ("major", "minor", "revision") if k in v)
            return str(v)
        except NpmError:
            return ""

    # --- Übersicht -------------------------------------------------------------------------------

    def overview(self):
        proxies = self.get("/api/nginx/proxy-hosts")
        redirects = self.get("/api/nginx/redirection-hosts")
        dead = self.get("/api/nginx/dead-hosts")
        streams = self.get("/api/nginx/streams")
        certs = self.get("/api/nginx/certificates")
        lists = self.get("/api/nginx/access-lists?expand=items,clients")
        version = self.version()
        return build_overview(proxies, redirects, dead, streams, certs, lists, self.checks, version)

    # --- Ändern ----------------------------------------------------------------------------------

    def renew(self, cert_id):
        cert = next((c for c in self.get("/api/nginx/certificates") if str(c.get("id")) == str(cert_id)), None)
        if not cert:
            raise LookupError
        if cert.get("provider") != "letsencrypt":
            raise NpmError("Nur Zertifikate von Let's Encrypt kann der Proxy Manager selbst erneuern. "
                           "Ein eigenes Zertifikat bitte dort neu hochladen.", 400)
        try:
            self.request("POST", f"/api/nginx/certificates/{int(cert_id)}/renew")
        except NpmError as exc:
            if exc.status == 403:
                raise NpmError("Der Benutzer für Home-Net darf im Proxy Manager nur ansehen. Für das Erneuern braucht "
                               "er dort bei Zertifikaten das Recht „Verwalten“.", 400)
            raise

    def set_enabled(self, kind, host_id, on):
        path = {"proxy": "proxy-hosts", "redirect": "redirection-hosts", "404": "dead-hosts"}.get(kind)
        if not path:
            raise LookupError
        try:
            self.request("POST", f"/api/nginx/{path}/{int(host_id)}/{'enable' if on else 'disable'}")
        except NpmError as exc:
            if exc.status == 403:
                raise NpmError("Der Benutzer für Home-Net darf im Proxy Manager nur ansehen. Zum An- und Abschalten "
                               "braucht er dort bei den Hosts das Recht „Verwalten“.", 400)
            if exc.status == 400 and "already" in str(exc).lower():
                return
            raise


def access_of(lists):
    out = {}
    for a in lists:
        out[a.get("id")] = {"name": a.get("name", ""), "satisfy_any": bool(a.get("satisfy_any")),
                            "clients": [{"address": c.get("address", ""), "directive": c.get("directive", "")}
                                        for c in a.get("clients") or []],
                            "users": len(a.get("items") or [])}
    return out


def build_overview(proxies, redirects, dead, streams, certs, lists, checks, version="", now=None):
    now = now or time.time()
    access = access_of(lists)
    cert_rows = {}
    for c in certs:
        expires = parse_time(c.get("expires_on"))
        state, days = cert_state(expires, now)
        cert_rows[c.get("id")] = {"id": c.get("id"), "name": c.get("nice_name") or ", ".join(c.get("domain_names") or []),
                                  "domains": c.get("domain_names") or [], "provider": c.get("provider", ""),
                                  "expires": expires, "days": days, "state": state, "hosts": []}

    hosts = []
    for kind, rows in (("proxy", proxies), ("redirect", redirects), ("404", dead)):
        for h in rows:
            enabled = bool(h.get("enabled"))
            acl = access.get(h.get("access_list_id")) if h.get("access_list_id") else None
            if h.get("access_list_id") and acl is None:
                acl = {"name": f"Nr. {h.get('access_list_id')}", "clients": [], "users": 0, "satisfy_any": False}
            kind_exposure, note = exposure(enabled, acl)
            cert = cert_rows.get(h.get("certificate_id")) if h.get("certificate_id") else None
            domains = h.get("domain_names") or []
            if cert:
                cert["hosts"].extend(domains)
            if kind == "proxy":
                target = f"{h.get('forward_scheme', 'http')}://{h.get('forward_host', '')}:{h.get('forward_port', '')}"
            elif kind == "redirect":
                target = f"{h.get('forward_scheme', 'https') if h.get('forward_scheme') != 'auto' else 'https'}://" \
                         f"{h.get('forward_domain_name', '')}"
            else:
                target = ""
            meta = h.get("meta") or {}
            hosts.append({
                "id": h.get("id"), "kind": kind, "domains": domains, "target": target, "enabled": enabled,
                "exposure": kind_exposure, "exposure_label": EXPOSURE_LABELS[kind_exposure], "note": note,
                "access_list": acl["name"] if acl else "",
                "ssl": {k: cert[k] for k in ("id", "name", "expires", "days", "state", "provider")} if cert else None,
                "ssl_forced": bool(h.get("ssl_forced")),
                "nginx_error": "" if meta.get("nginx_online", True) else (meta.get("nginx_err") or "Fehler in der Konfiguration"),
                "_forward": (h.get("forward_host"), h.get("forward_port")) if kind == "proxy" else None,
            })

    def check(h):
        if h["exposure"] not in ("off", "internal"):  # internal names (like *.lan) need no public DNS
            h["dns"] = [dict(checks.dns(d), domain=d) for d in h["domains"]]
        else:
            h["dns"] = []
        h["target_state"] = checks.target(*h["_forward"]) if h["_forward"] and h["enabled"] else ""
        del h["_forward"]
        return h

    with ThreadPoolExecutor(max_workers=8) as pool:
        hosts = list(pool.map(check, hosts))
    order = {"public": 0, "password": 1, "restricted": 2, "internal": 3, "off": 4}
    hosts.sort(key=lambda h: (order[h["exposure"]], (h["domains"] or [""])[0]))

    cert_list = sorted(cert_rows.values(), key=lambda c: (c["expires"] is None, c["expires"] or 0))
    for c in cert_list:
        c["hosts"] = sorted(set(c["hosts"]))
        c["unused"] = not c["hosts"]
    stream_rows = [{"id": s.get("id"), "port": s.get("incoming_port"),
                    "target": f"{s.get('forwarding_host', '')}:{s.get('forwarding_port', '')}",
                    "tcp": bool(s.get("tcp_forwarding")), "udp": bool(s.get("udp_forwarding")),
                    "enabled": bool(s.get("enabled"))} for s in streams]
    used = [c for c in cert_list if not c["unused"]]
    summary = {k: sum(1 for h in hosts if h["exposure"] == k) for k in order}
    summary.update(certs_soon=sum(1 for c in used if c["state"] == "soon"),
                   certs_expired=sum(1 for c in used if c["state"] == "expired"),
                   targets_down=sum(1 for h in hosts if h["target_state"] == "down"),
                   nginx_errors=sum(1 for h in hosts if h["nginx_error"]),
                   streams=sum(1 for s in stream_rows if s["enabled"]))
    return {"version": version, "hosts": hosts, "certs": cert_list, "streams": stream_rows, "summary": summary}
