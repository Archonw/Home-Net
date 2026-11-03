"""Talks to the REST API of Pi-hole 6 (https://docs.pi-hole.net/api/), for several Pi-holes at once.

Several Pi-holes usually serve the same network (e.g. kept equal by nebula-sync and sharing an address via keepalived),
so pausing goes to all of them and the numbers are added up. Blocking state is not part of the synced settings, which
is why every instance is told separately.
"""
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 8
TOP = 10


class PiholeError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _context(url):
    # Pi-hole serves https only with its own certificate; in the home network that is accepted as is
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


class Instance:
    """One Pi-hole. Keeps its session so not every request opens a new one (Pi-hole allows only a few)."""

    def __init__(self, name, url, password):
        self.name = name
        self.url = url.rstrip("/")
        self.password = password
        self.sid = ""
        self.lock = threading.Lock()

    def _call(self, method, path, body=None, query=None, sid=""):
        url = self.url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if sid:
            headers["X-FTL-SID"] = sid
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_context(url)) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PiholeError(f"{self.name} ist nicht erreichbar ({reason}).")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise PiholeError(f"{self.name} antwortet nicht wie Pi-hole 6. Läuft dort eine ältere Version?")

    def login(self):
        try:
            out = self._call("POST", "/api/auth", {"password": self.password})
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise PiholeError(f"{self.name} lehnt das Passwort ab.", 400)
            if exc.code == 404:
                raise PiholeError(f"{self.name} hat keine API unter /api. Home-Net braucht Pi-hole 6.", 400)
            raise PiholeError(f"{self.name} antwortet bei der Anmeldung mit Fehler {exc.code}.")
        session = out.get("session") or {}
        if not session.get("valid"):
            raise PiholeError(f"{self.name} lehnt das Passwort ab.", 400)
        return session.get("sid") or ""  # empty when the Pi-hole has no password

    def request(self, method, path, body=None, query=None):
        with self.lock:
            for attempt in (0, 1):
                if not self.sid and attempt == 0 and self.password:
                    self.sid = self.login()
                try:
                    return self._call(method, path, body, query, self.sid)
                except urllib.error.HTTPError as exc:
                    if exc.code == 401 and attempt == 0:
                        self.sid = self.login()  # session ran out (or the Pi-hole has a password after all)
                        continue
                    raise PiholeError(f"{self.name} antwortet mit Fehler {exc.code}.")
        raise PiholeError(f"{self.name} lehnt die Anmeldung ab.", 400)

    # --- single instance ------------------------------------------------------------------------

    def status(self):
        blocking = self.request("GET", "/api/dns/blocking")
        try:
            version = self.request("GET", "/api/info/version").get("version") or {}
            core = ((version.get("core") or {}).get("local") or {}).get("version") or ""
            ftl = ((version.get("ftl") or {}).get("local") or {}).get("version") or ""
        except PiholeError:
            core = ftl = ""
        return {"blocking": blocking.get("blocking", "unknown"), "timer": blocking.get("timer"),
                "version": core, "ftl": ftl}

    def stats(self):
        return {
            "summary": self.request("GET", "/api/stats/summary"),
            "domains": self.request("GET", "/api/stats/top_domains", query={"blocked": "true", "count": TOP}),
            "permitted": self.request("GET", "/api/stats/top_domains", query={"count": TOP}),
            "clients": self.request("GET", "/api/stats/top_clients", query={"count": TOP}),
            "history": self.request("GET", "/api/history"),
        }

    def set_blocking(self, on, seconds=None):
        body = {"blocking": bool(on), "timer": None if on or not seconds else int(seconds)}
        out = self.request("POST", "/api/dns/blocking", body)
        return {"blocking": out.get("blocking", "unknown"), "timer": out.get("timer")}


class Piholes:
    def __init__(self, settings):
        self.settings = settings  # callable returning {"instances": [{"name", "url", "password"}]}
        self.cache = {}

    def instances(self):
        out = []
        for i, item in enumerate(self.settings().get("instances") or []):
            key = (item.get("url"), item.get("password"))
            inst = self.cache.get(key)
            if inst is None:
                inst = self.cache[key] = Instance(item.get("name") or f"Pi-hole {i + 1}", item["url"],
                                                  item.get("password") or "")
            inst.name = item.get("name") or inst.name
            out.append(inst)
        return out

    @staticmethod
    def each(instances, work):
        """Runs work on every instance at once; returns (instance, result or None, error or "")."""
        def one(inst):
            try:
                return inst, work(inst), ""
            except PiholeError as exc:
                return inst, None, str(exc)
        if not instances:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(instances))) as pool:
            return list(pool.map(one, instances))

    def overview(self):
        rows = []
        for inst, st, err in self.each(self.instances(), Instance.status):
            row = {"name": inst.name, "url": inst.url, "online": not err, "error": err}
            row.update(st or {"blocking": "unknown", "timer": None, "version": "", "ftl": ""})
            rows.append(row)
        return {"instances": rows, "blocking": combined(rows)}

    def stats(self):
        results = self.each(self.instances(), Instance.stats)
        good = [r for _, r, err in results if not err]
        errors = [{"name": inst.name, "error": err} for inst, _, err in results if err]
        return dict(merge(good), errors=errors, instances=len(results), answered=len(good))

    def set_blocking(self, on, seconds=None):
        results = self.each(self.instances(), lambda inst: inst.set_blocking(on, seconds))
        rows = [{"name": inst.name, "ok": not err, "error": err, **(r or {})} for inst, r, err in results]
        return {"instances": rows, "until": int(time.time()) + int(seconds) if not on and seconds else None}


def combined(rows):
    """One word for all: enabled, disabled, mixed or unknown (none reachable)."""
    states = {r["blocking"] for r in rows if r["online"]}
    if not states:
        return "unknown"
    if len(states) == 1:
        return states.pop()
    return "mixed"


def merge(results):
    """Adds up the numbers of several Pi-holes."""
    total = blocked = cached = forwarded = 0
    active = 0
    gravity = 0
    gravity_update = 0
    domains, permitted, clients, history = {}, {}, {}, {}
    for r in results:
        q = (r.get("summary") or {}).get("queries") or {}
        total += int(q.get("total") or 0)
        blocked += int(q.get("blocked") or 0)
        cached += int(q.get("cached") or 0)
        forwarded += int(q.get("forwarded") or 0)
        active = max(active, int(((r.get("summary") or {}).get("clients") or {}).get("active") or 0))
        g = (r.get("summary") or {}).get("gravity") or {}
        gravity = max(gravity, int(g.get("domains_being_blocked") or 0))
        gravity_update = max(gravity_update, int(g.get("last_update") or 0))
        for d in (r.get("domains") or {}).get("domains") or []:
            domains[d["domain"]] = domains.get(d["domain"], 0) + int(d.get("count") or 0)
        for d in (r.get("permitted") or {}).get("domains") or []:
            permitted[d["domain"]] = permitted.get(d["domain"], 0) + int(d.get("count") or 0)
        for c in (r.get("clients") or {}).get("clients") or []:
            key = c.get("ip") or c.get("name")
            entry = clients.setdefault(key, {"ip": c.get("ip") or "", "name": c.get("name") or "", "count": 0})
            entry["count"] += int(c.get("count") or 0)
            entry["name"] = entry["name"] or c.get("name") or ""
        for h in (r.get("history") or {}).get("history") or []:
            slot = history.setdefault(int(h.get("timestamp") or 0), {"total": 0, "blocked": 0})
            slot["total"] += int(h.get("total") or 0)
            slot["blocked"] += int(h.get("blocked") or 0)

    def top(counts):
        return [{"domain": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:TOP]]

    return {
        "total": total, "blocked": blocked, "cached": cached, "forwarded": forwarded,
        "percent": round(100 * blocked / total, 1) if total else 0.0,
        "clients_active": active, "gravity": gravity, "gravity_update": gravity_update,
        "top_blocked": top(domains), "top_permitted": top(permitted),
        "top_clients": sorted(clients.values(), key=lambda c: -c["count"])[:TOP],
        "history": [{"time": t, **v} for t, v in sorted(history.items()) if t],
    }
