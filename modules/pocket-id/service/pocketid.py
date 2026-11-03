"""Talks to the REST API of Pocket ID (https://pocket-id.org) with an admin API key.

Apps are the OIDC clients of Pocket ID. Who may use a client is decided by the user groups allowed for it, so giving a
user an app means putting the user into one of those groups.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE_LIMIT = 100
TIMEOUT = 15


class PocketIdError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class PocketId:
    def __init__(self, settings):
        self.settings = settings  # callable returning {"url": ..., "api_key": ...}
        self._issuer = ("", "", 0)

    # --- HTTP ------------------------------------------------------------------------------------

    def base(self):
        url = (self.settings().get("url") or "").rstrip("/")
        if not url:
            raise PocketIdError("Die Adresse von Pocket ID fehlt. Bitte unter Einstellungen eintragen.", 400)
        return url

    def request(self, method, path, body=None, query=None):
        key = self.settings().get("api_key") or ""
        if not key:
            raise PocketIdError("Der API-Schlüssel von Pocket ID fehlt. Bitte unter Einstellungen eintragen.", 400)
        url = self.base() + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-API-Key": key, "Accept": "application/json",
                                              **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise PocketIdError(self.explain(exc), 400 if 400 <= exc.code < 500 and exc.code not in (401, 403) else 502)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PocketIdError(f"Pocket ID ist nicht erreichbar ({reason}).")
        return json.loads(raw) if raw else {}

    @staticmethod
    def explain(exc):
        try:
            detail = json.loads(exc.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        if exc.code in (401, 403):
            return "Pocket ID lehnt den API-Schlüssel ab. Ist er abgelaufen oder gelöscht?"
        if exc.code == 404:
            return detail or "In Pocket ID nicht gefunden."
        return f"Pocket ID meldet: {detail}" if detail else f"Pocket ID antwortet mit Fehler {exc.code}."

    def all_pages(self, path):
        out, page = [], 1
        while True:
            data = self.request("GET", path, query={"pagination[page]": page, "pagination[limit]": PAGE_LIMIT})
            out.extend(data.get("data") or [])
            pages = (data.get("pagination") or {}).get("totalPages", 1)
            if page >= pages or not data.get("data"):
                return out
            page += 1

    # --- Lesen -----------------------------------------------------------------------------------

    def issuer(self):
        """The public address of Pocket ID, for invitation links. Comes from the OIDC discovery document."""
        url, value, at = self._issuer
        if url == self.base() and value and time.time() - at < 3600:
            return value
        try:
            with urllib.request.urlopen(self.base() + "/.well-known/openid-configuration", timeout=TIMEOUT) as resp:
                value = json.loads(resp.read()).get("issuer", "").rstrip("/")
        except (OSError, ValueError):
            value = ""
        value = value or self.base()
        self._issuer = (self.base(), value, time.time())
        return value

    def users(self):
        return self.all_pages("/api/users")

    def user(self, user_id):
        return self.request("GET", f"/api/users/{q(user_id)}")

    def groups(self):
        """All groups with their allowed apps (needs one request per group; families have only a few)."""
        out = []
        for g in self.all_pages("/api/user-groups"):
            detail = self.request("GET", f"/api/user-groups/{q(g['id'])}")
            out.append({"id": g["id"], "name": g.get("name", ""), "label": g.get("friendlyName") or g.get("name", ""),
                        "clients": [{"id": c["id"], "name": c.get("name", "")}
                                    for c in detail.get("allowedOidcClients") or []]})
        return out

    def clients(self):
        return [{"id": c["id"], "name": c.get("name", ""), "restricted": bool(c.get("isGroupRestricted")),
                 "launch": c.get("launchURL") or ""} for c in self.all_pages("/api/oidc/clients")]

    def passkeys(self, user_id):
        return len(self.request("GET", f"/api/users/{q(user_id)}/webauthn-credentials") or [])

    def overview(self):
        users, groups, clients = self.users(), self.groups(), self.clients()
        apps = apps_of(groups, clients)
        rows = []
        for u in users:
            try:
                keys = self.passkeys(u["id"])
            except PocketIdError:
                keys = None
            rows.append(public_user(u, keys))
        rows.sort(key=lambda r: (r["disabled"], r["name"].lower()))
        return {"users": rows, "apps": apps, "groups": [{"id": g["id"], "label": g["label"], "name": g["name"],
                                                          "apps": [c["name"] for c in g["clients"]]} for g in groups],
                "issuer": self.issuer()}

    # --- Ändern ----------------------------------------------------------------------------------

    def create_user(self, form):
        username = str(form.get("username") or "").strip().lower()
        first = str(form.get("first_name") or "").strip()
        if not username:
            raise PocketIdError("Bitte einen Benutzernamen angeben.", 400)
        if not first:
            raise PocketIdError("Bitte einen Vornamen angeben.", 400)
        email = str(form.get("email") or "").strip() or None
        last = str(form.get("last_name") or "").strip()
        groups = [str(g) for g in form.get("groups") or []]
        existing = self.users()
        if any(u.get("username", "").lower() == username for u in existing):
            raise PocketIdError(f"Den Benutzernamen „{username}“ gibt es schon.", 400)
        created = self.request("POST", "/api/users", {
            "username": username, "firstName": first, "lastName": last,
            "displayName": f"{first} {last}".strip(), "email": email, "emailVerified": bool(email),
            "isAdmin": False, "disabled": False, "userGroupIds": groups})
        claims = username_claims(existing, username)
        if claims:
            self.request("PUT", f"/api/custom-claims/user/{q(created['id'])}", claims)
        return created

    def update_user(self, user_id, form):
        u = self.user(user_id)
        if u.get("isAdmin") and form.get("disabled"):
            raise PocketIdError("Admins von Pocket ID lassen sich hier nicht sperren.", 400)
        body = {"username": u["username"], "firstName": u.get("firstName") or "", "lastName": u.get("lastName") or "",
                "displayName": u.get("displayName") or "", "email": u.get("email"),
                "emailVerified": bool(u.get("emailVerified")), "isAdmin": bool(u.get("isAdmin")),
                "locale": u.get("locale"), "disabled": bool(u.get("disabled"))}
        for key, field in (("first_name", "firstName"), ("last_name", "lastName")):
            if key in form:
                body[field] = str(form[key] or "").strip()
        if "first_name" in form or "last_name" in form:
            body["displayName"] = f"{body['firstName']} {body['lastName']}".strip()
        if "email" in form:
            body["email"] = str(form["email"] or "").strip() or None
            if body["email"] != u.get("email"):
                body["emailVerified"] = bool(body["email"])
        if "disabled" in form:
            body["disabled"] = bool(form["disabled"])
        if not body["firstName"]:
            raise PocketIdError("Bitte einen Vornamen angeben.", 400)
        return self.request("PUT", f"/api/users/{q(user_id)}", body)

    def set_groups(self, user_id, groups):
        known = {g["id"] for g in self.all_pages("/api/user-groups")}
        groups = [str(g) for g in groups]
        if any(g not in known for g in groups):
            raise PocketIdError("Unbekannte Gruppe.", 400)
        self.request("PUT", f"/api/users/{q(user_id)}/user-groups", {"userGroupIds": groups})

    def set_app(self, user_id, group_id, on):
        u = self.user(user_id)
        current = [g["id"] for g in u.get("userGroups") or []]
        wanted = [g for g in current if g != group_id] + ([group_id] if on else [])
        self.set_groups(user_id, wanted)

    def delete_user(self, user_id):
        u = self.user(user_id)
        if u.get("isAdmin"):
            raise PocketIdError("Admins von Pocket ID lassen sich hier nicht löschen.", 400)
        self.request("DELETE", f"/api/users/{q(user_id)}")
        return u

    def invite(self, user_id, hours=72, email=False):
        hours = max(1, min(int(hours or 72), 24 * 31))
        ttl = f"{hours}h"
        if email:
            self.request("POST", f"/api/users/{q(user_id)}/one-time-access-email", {"ttl": ttl})
            return {"sent": True, "expires": int(time.time()) + hours * 3600}
        token = self.request("POST", f"/api/users/{q(user_id)}/one-time-access-token", {"ttl": ttl}).get("token")
        if not token:
            raise PocketIdError("Pocket ID hat keinen Einladungscode geliefert.")
        return {"link": f"{self.issuer()}/lc/{token}", "expires": int(time.time()) + hours * 3600}


def q(value):
    return urllib.parse.quote(str(value), safe="")


def apps_of(groups, clients):
    """One app per group that unlocks something: the switches in the app. Unrestricted clients are open to everyone."""
    apps = []
    for g in groups:
        if g["clients"]:
            apps.append({"group": g["id"], "label": ", ".join(sorted(c["name"] for c in g["clients"])),
                         "group_name": g["label"]})
    apps.sort(key=lambda a: a["label"].lower())
    open_to_all = sorted(c["name"] for c in clients if not c["restricted"])
    return {"switches": apps, "open": open_to_all}


def public_user(u, passkeys=None):
    name = (u.get("displayName") or f"{u.get('firstName', '')} {u.get('lastName') or ''}").strip()
    return {"id": u["id"], "username": u.get("username", ""), "name": name or u.get("username", ""),
            "first_name": u.get("firstName") or "", "last_name": u.get("lastName") or "",
            "email": u.get("email") or "", "admin": bool(u.get("isAdmin")), "disabled": bool(u.get("disabled")),
            "groups": [g["id"] for g in u.get("userGroups") or []], "passkeys": passkeys}


def username_claims(existing, username):
    """Custom claims that hold the username for every user that has them (e.g. nextcloud_username) are set for new
    users as well, so the new user can sign in to such an app right away."""
    seen = {}
    for u in existing:
        for c in u.get("customClaims") or []:
            same = c.get("value") == u.get("username")
            seen[c.get("key")] = seen.get(c.get("key"), True) and same
    return [{"key": k, "value": username} for k, ok in sorted(seen.items()) if k and ok]
