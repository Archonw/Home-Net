"""Passkeys, paired devices, recovery codes and sessions of the gateway."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse

SESSION_SECONDS = 12 * 3600
CHALLENGE_SECONDS = 120
CODE_SECONDS = 600
RECOVERY_CODES = 8
DEVICE_PREFIX = "hnd_"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Security:
    _shared = {}

    @classmethod
    def shared(cls, config_dir):
        """One object per folder, since sessions and one-time values live in memory."""
        if config_dir not in cls._shared:
            cls._shared[config_dir] = cls(config_dir)
        return cls._shared[config_dir]

    def __init__(self, config_dir):
        self.path = os.path.join(config_dir, "security.json")
        self.lock = threading.RLock()
        self.sessions = {}
        self.pending = {}
        self.codes = {}

    def load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"external_url": "", "passkeys": [], "devices": [], "recovery": []}

    def save(self, data):
        tmp = self.path + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def has_passkeys(self):
        return bool(self.load()["passkeys"])

    def origin_and_rp(self):
        url = self.load().get("external_url", "")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return None, None
        return f"{parts.scheme}://{parts.netloc}", parts.hostname

    # ---- browser sessions
    def new_session(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[digest(token)] = now + SESSION_SECONDS
        return token

    def session_ok(self, token):
        with self.lock:
            return self.sessions.get(digest(token), 0) > time.time()

    def drop_session(self, token):
        with self.lock:
            self.sessions.pop(digest(token), None)

    def drop_all_sessions(self):
        with self.lock:
            self.sessions = {}

    # ---- one-time values
    def new_challenge(self, kind, **extra):
        ticket, challenge = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
            self.pending[ticket] = dict(extra, kind=kind, challenge=challenge, expires=now + CHALLENGE_SECONDS)
        return ticket, challenge

    def take_challenge(self, ticket, kind):
        with self.lock:
            item = self.pending.pop(str(ticket), None)
        if not item or item["kind"] != kind or item["expires"] < time.time():
            return None
        return item

    def new_code(self, kind):
        code = f"{secrets.randbelow(10 ** 8):08d}"
        with self.lock:
            self.codes[kind] = (digest(code), time.time() + CODE_SECONDS)
        return code

    def take_code(self, kind, code):
        with self.lock:
            hashed, expires = self.codes.get(kind, ("", 0))
            if expires < time.time() or not hmac.compare_digest(hashed, digest(str(code))):
                return False
            self.codes.pop(kind, None)
        return True

    # ---- passkeys
    def add_passkey(self, credential_id, x, y, count, name):
        with self.lock:
            data = self.load()
            if any(p["id"] == credential_id for p in data["passkeys"]):
                raise ValueError("Dieser Passkey ist schon eingetragen")
            data["passkeys"].append({"id": credential_id, "x": x, "y": y, "count": count, "name": name,
                                     "added": int(time.time())})
            recovery = []
            if not data["recovery"]:
                recovery = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
                data["recovery"] = [digest(c) for c in recovery]
            self.save(data)
        return recovery

    def find_passkey(self, credential_id):
        return next((p for p in self.load()["passkeys"] if p["id"] == credential_id), None)

    def update_count(self, credential_id, count):
        with self.lock:
            data = self.load()
            for p in data["passkeys"]:
                if p["id"] == credential_id:
                    p["count"] = count
            self.save(data)

    def use_recovery(self, code):
        hashed = digest(str(code).strip().lower())
        with self.lock:
            data = self.load()
            if hashed not in data["recovery"]:
                return False
            data["recovery"].remove(hashed)
            self.save(data)
        return True

    # ---- paired devices
    def add_device(self, name):
        token = DEVICE_PREFIX + secrets.token_urlsafe(32)
        device_id = secrets.token_hex(6)
        with self.lock:
            data = self.load()
            data["devices"].append({"id": device_id, "name": name, "hash": digest(token), "added": int(time.time())})
            self.save(data)
        return token

    def device_ok(self, token):
        hashed = digest(token)
        return any(hmac.compare_digest(d["hash"], hashed) for d in self.load()["devices"])

    def remove(self, kind, item_id):
        with self.lock:
            data = self.load()
            before = len(data[kind])
            data[kind] = [i for i in data[kind] if i["id"] != item_id]
            if len(data[kind]) == before:
                return False
            if kind == "passkeys" and not data["passkeys"]:
                data["recovery"] = []
            self.save(data)
        return True

    def set_external_url(self, url):
        url = url.strip().rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if url and (parts.scheme != "https" or not parts.hostname or parts.path):
            raise ValueError("Bitte die Adresse in der Form https://name.example.de angeben")
        with self.lock:
            data = self.load()
            if data.get("external_url") != url and data["passkeys"]:
                raise ValueError("Passkeys gehören zur alten Adresse. Bitte erst alle entfernen.")
            data["external_url"] = url
            self.save(data)

    def summary(self):
        data = self.load()
        return {"external_url": data.get("external_url", ""), "recovery_left": len(data["recovery"]),
                "passkeys": [{"id": p["id"], "name": p["name"], "added": p["added"]} for p in data["passkeys"]],
                "devices": [{"id": d["id"], "name": d["name"], "added": d["added"]} for d in data["devices"]]}
