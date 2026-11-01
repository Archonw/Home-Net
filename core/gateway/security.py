"""Accounts, passkeys, paired devices, recovery codes, sessions and the audit log of the gateway."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

SESSION_SECONDS = 12 * 3600
CHALLENGE_SECONDS = 120
CODE_SECONDS = 600
RECOVERY_CODES = 8
DEVICE_PREFIX = "hnd_"
PBKDF2_ROUNDS = 200_000
ROLES = ("owner", "admin", "user")
NAME_RE = re.compile(r"^[\w][\w .@-]{1,39}$")
AUDIT_KEEP = 2000
# Compared against when the name is unknown, so that a wrong name takes as long as a wrong password.
DUMMY = {"salt": "00" * 16, "hash": "00" * 32, "rounds": PBKDF2_ROUNDS}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def password_record(password, rounds=PBKDF2_ROUNDS):
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex(),
            "rounds": rounds}


def password_matches(record, password):
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), record["rounds"])
    return hmac.compare_digest(value.hex(), record["hash"])


def public_user(user):
    return {k: user[k] for k in ("id", "name", "role", "active", "modules", "categories")}


class Security:
    _shared = {}

    @classmethod
    def shared(cls, config_dir):
        """One object per folder, since sessions and one-time values live in memory."""
        if config_dir not in cls._shared:
            cls._shared[config_dir] = cls(config_dir)
        return cls._shared[config_dir]

    def __init__(self, config_dir):
        self.dir = config_dir
        self.path = os.path.join(config_dir, "security.json")
        self.audit_path = os.path.join(config_dir, "audit.jsonl")
        self.lock = threading.RLock()
        self.sessions = {}
        self.pending = {}
        self.codes = {}

    # ---- storage
    def load(self):
        with self.lock:
            try:
                with open(self.path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            data.setdefault("external_url", "")
            data.setdefault("passkeys", [])
            data.setdefault("devices", [])
            if not data.get("users"):
                self.first_owner(data)
            return data

    def first_owner(self, data):
        """Older installations had one password; it becomes the owner, along with its passkeys and devices."""
        record = DUMMY
        name = "admin"
        try:
            with open(os.path.join(self.dir, "auth.json")) as f:
                record = json.load(f)
            with open(os.path.join(self.dir, "gateway.json")) as f:
                name = json.load(f).get("owner", name)
        except (OSError, ValueError):
            pass
        owner = {"id": secrets.token_hex(6), "name": name, "role": "owner", "active": True, "pw": record,
                 "modules": ["*"], "categories": ["*"], "recovery": data.pop("recovery", [])}
        data["users"] = [owner]
        for item in data["passkeys"] + data["devices"]:
            item.setdefault("user", owner["id"])
        self.save(data)

    def save(self, data):
        tmp = self.path + ".new"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    # ---- audit log
    def audit(self, who, action, detail="", ip=""):
        line = json.dumps({"time": int(time.time()), "who": who, "action": action, "detail": detail, "ip": ip},
                          ensure_ascii=False)
        with self.lock:
            with open(self.audit_path, "a") as f:
                f.write(line + "\n")
            os.chmod(self.audit_path, 0o600)
            if os.path.getsize(self.audit_path) > 2_000_000:
                with open(self.audit_path) as f:
                    keep = f.readlines()[-AUDIT_KEEP:]
                with open(self.audit_path, "w") as f:
                    f.writelines(keep)

    def audit_tail(self, count=200):
        try:
            with open(self.audit_path) as f:
                lines = f.readlines()[-count:]
        except OSError:
            return []
        return [json.loads(line) for line in reversed(lines)]

    # ---- accounts
    def find_user(self, user_id):
        return next((u for u in self.load()["users"] if u["id"] == user_id), None)

    def user_by_name(self, name):
        wanted = str(name).strip().casefold()
        return next((u for u in self.load()["users"] if u["name"].casefold() == wanted), None)

    def check_login(self, name, password):
        """The active user with this name and password, or None. Always takes about the same time."""
        user = self.user_by_name(name)
        matches = password_matches(user["pw"] if user else DUMMY, str(password))
        return user if user and matches and user.get("active") else None

    def users(self):
        return [public_user(u) for u in self.load()["users"]]

    @staticmethod
    def check_name(data, name, ignore=None):
        name = str(name).strip()
        if not NAME_RE.match(name):
            raise ValueError("Der Name braucht 2 bis 40 Zeichen: Buchstaben, Ziffern, Leerzeichen, . @ _ oder -")
        if any(u["name"].casefold() == name.casefold() and u["id"] != ignore for u in data["users"]):
            raise ValueError("Diesen Namen gibt es schon")
        return name

    @staticmethod
    def check_password(password):
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("Das Passwort braucht mindestens 8 Zeichen")
        return password

    @staticmethod
    def clean_rights(values):
        if not isinstance(values, list):
            raise ValueError("Ungültige Rechte")
        return [str(v) for v in values]

    def create_user(self, actor, form):
        role = form.get("role", "user")
        self.check_role_change(actor, None, role)
        with self.lock:
            data = self.load()
            user = {"id": secrets.token_hex(6), "name": self.check_name(data, form.get("name", "")), "role": role,
                    "active": True, "pw": password_record(self.check_password(form.get("password"))),
                    "modules": self.clean_rights(form.get("modules", [])),
                    "categories": self.clean_rights(form.get("categories", [])), "recovery": []}
            data["users"].append(user)
            self.save(data)
        return public_user(user)

    @staticmethod
    def check_role_change(actor, target, new_role):
        if new_role not in ROLES:
            raise ValueError("Unbekannte Rolle")
        if actor["role"] == "owner":
            return
        if new_role != "user" or (target and target["role"] != "user"):
            raise PermissionError("Das darf nur der Eigentümer")

    @staticmethod
    def owners_left(data, without):
        return [u for u in data["users"] if u["role"] == "owner" and u.get("active") and u["id"] != without]

    def update_user(self, actor, user_id, form):
        with self.lock:
            data = self.load()
            user = next((u for u in data["users"] if u["id"] == user_id), None)
            if not user:
                raise LookupError
            new_role = form.get("role", user["role"])
            self.check_role_change(actor, user, new_role)
            active = bool(form.get("active", user["active"]))
            if user["role"] == "owner" and (new_role != "owner" or not active) and not self.owners_left(data, user_id):
                raise ValueError("Es muss mindestens ein aktiver Eigentümer bleiben")
            if "name" in form:
                user["name"] = self.check_name(data, form["name"], user_id)
            if form.get("password"):
                user["pw"] = password_record(self.check_password(form["password"]))
                self.drop_sessions(user_id)
            user["role"] = new_role
            if active != user["active"]:
                self.drop_sessions(user_id)
            user["active"] = active
            if "modules" in form:
                user["modules"] = self.clean_rights(form["modules"])
            if "categories" in form:
                user["categories"] = self.clean_rights(form["categories"])
            self.save(data)
        return public_user(user)

    def delete_user(self, actor, user_id):
        with self.lock:
            data = self.load()
            user = next((u for u in data["users"] if u["id"] == user_id), None)
            if not user:
                raise LookupError
            self.check_role_change(actor, user, "user")
            if user["role"] == "owner" and not self.owners_left(data, user_id):
                raise ValueError("Es muss mindestens ein aktiver Eigentümer bleiben")
            data["users"] = [u for u in data["users"] if u["id"] != user_id]
            data["passkeys"] = [p for p in data["passkeys"] if p["user"] != user_id]
            data["devices"] = [d for d in data["devices"] if d["user"] != user_id]
            self.save(data)
            self.drop_sessions(user_id)
        return user

    def change_own(self, user_id, form):
        """A user's own name and password; the current password is needed for both."""
        with self.lock:
            data = self.load()
            user = next(u for u in data["users"] if u["id"] == user_id)
            if not password_matches(user["pw"], str(form.get("old_password", ""))):
                raise PermissionError("Das aktuelle Passwort stimmt nicht")
            if form.get("name"):
                user["name"] = self.check_name(data, form["name"], user_id)
            if form.get("new_password"):
                user["pw"] = password_record(self.check_password(form["new_password"]))
            self.save(data)
        return public_user(user)

    @staticmethod
    def may_use(user, module_id):
        return user["role"] in ("owner", "admin") or "*" in user["modules"] or module_id in user["modules"]

    @staticmethod
    def categories_of(user):
        return ["*"] if user["role"] in ("owner", "admin") else user["categories"]

    # ---- address for passkeys
    def origin_and_rp(self):
        parts = urllib.parse.urlsplit(self.load().get("external_url", ""))
        if parts.scheme != "https" or not parts.hostname:
            return None, None
        return f"{parts.scheme}://{parts.netloc}", parts.hostname

    def set_external_url(self, url):
        url = str(url).strip().rstrip("/")
        parts = urllib.parse.urlsplit(url)
        if url and (parts.scheme != "https" or not parts.hostname or parts.path):
            raise ValueError("Bitte die Adresse in der Form https://name.example.de angeben")
        with self.lock:
            data = self.load()
            if data.get("external_url") != url and data["passkeys"]:
                raise ValueError("Passkeys gehören zur alten Adresse. Bitte erst alle entfernen.")
            data["external_url"] = url
            self.save(data)

    # ---- browser sessions
    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            self.sessions[digest(token)] = (user_id, now + SESSION_SECONDS)
        return token

    def session_user(self, token):
        with self.lock:
            entry = self.sessions.get(digest(token))
        if not entry or entry[1] < time.time():
            return None
        user = self.find_user(entry[0])
        return user if user and user.get("active") else None

    def drop_session(self, token):
        with self.lock:
            self.sessions.pop(digest(token), None)

    def drop_sessions(self, user_id):
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v[0] != user_id}

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

    def new_code(self, kind, user_id):
        code = f"{secrets.randbelow(10 ** 8):08d}"
        with self.lock:
            self.codes[(kind, user_id)] = (digest(code), time.time() + CODE_SECONDS)
        return code

    def take_code(self, kind, user_id, code):
        with self.lock:
            hashed, expires = self.codes.get((kind, user_id), ("", 0))
            if expires < time.time() or not hmac.compare_digest(hashed, digest(str(code))):
                return False
            self.codes.pop((kind, user_id), None)
        return True

    # ---- passkeys
    def passkeys_of(self, user_id):
        return [p for p in self.load()["passkeys"] if p["user"] == user_id]

    def add_passkey(self, user_id, credential_id, x, y, count, name):
        with self.lock:
            data = self.load()
            if any(p["id"] == credential_id for p in data["passkeys"]):
                raise ValueError("Dieser Passkey ist schon eingetragen")
            data["passkeys"].append({"id": credential_id, "user": user_id, "x": x, "y": y, "count": count,
                                     "name": name, "added": int(time.time())})
            user = next(u for u in data["users"] if u["id"] == user_id)
            recovery = []
            if not user.get("recovery"):
                recovery = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
                user["recovery"] = [digest(c) for c in recovery]
            self.save(data)
        return recovery

    def find_passkey(self, user_id, credential_id):
        return next((p for p in self.passkeys_of(user_id) if p["id"] == credential_id), None)

    def update_count(self, credential_id, count):
        with self.lock:
            data = self.load()
            for p in data["passkeys"]:
                if p["id"] == credential_id:
                    p["count"] = count
            self.save(data)

    def use_recovery(self, user_id, code):
        hashed = digest(str(code).strip().lower())
        with self.lock:
            data = self.load()
            user = next(u for u in data["users"] if u["id"] == user_id)
            if hashed not in user.get("recovery", []):
                return False
            user["recovery"].remove(hashed)
            self.save(data)
        return True

    # ---- paired devices
    def add_device(self, user_id, name):
        token = DEVICE_PREFIX + secrets.token_urlsafe(32)
        with self.lock:
            data = self.load()
            data["devices"].append({"id": secrets.token_hex(6), "user": user_id, "name": name, "hash": digest(token),
                                    "added": int(time.time())})
            self.save(data)
        return token

    def device_user(self, token):
        hashed = digest(token)
        for device in self.load()["devices"]:
            if hmac.compare_digest(device["hash"], hashed):
                user = self.find_user(device["user"])
                return user if user and user.get("active") else None
        return None

    def remove_item(self, kind, item_id, actor):
        """Removes a passkey or device of the actor; admins may remove those of others as well."""
        with self.lock:
            data = self.load()
            item = next((i for i in data[kind] if i["id"] == item_id), None)
            if not item:
                raise LookupError
            target = next((u for u in data["users"] if u["id"] == item["user"]), None)
            own = item["user"] == actor["id"]
            if not own and actor["role"] == "user":
                raise PermissionError("Das darf nur ein Admin")
            if not own and actor["role"] == "admin" and target and target["role"] != "user":
                raise PermissionError("Das darf nur der Eigentümer")
            data[kind] = [i for i in data[kind] if i["id"] != item_id]
            if kind == "passkeys" and target and not any(p["user"] == target["id"] for p in data["passkeys"]):
                target["recovery"] = []
            self.save(data)
        return item

    def account(self, user):
        data = self.load()
        strip = lambda items: [{k: i[k] for k in ("id", "name", "added")} for i in items if i["user"] == user["id"]]  # noqa: E731
        return dict(public_user(user), recovery_left=len(user.get("recovery", [])), passkeys=strip(data["passkeys"]),
                    devices=strip(data["devices"]), external_url=data.get("external_url", ""),
                    can_manage=user["role"] in ("owner", "admin"))

    def all_items(self):
        data = self.load()
        names = {u["id"]: u["name"] for u in data["users"]}
        strip = lambda items: [{"id": i["id"], "name": i["name"], "added": i["added"], "user": names.get(i["user"], "?")} for i in items]  # noqa: E731
        return {"passkeys": strip(data["passkeys"]), "devices": strip(data["devices"])}
