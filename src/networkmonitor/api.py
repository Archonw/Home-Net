"""REST-API und einfache Weboberfläche."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .appupdate import AppFolder, update_from_github
from .auth import LoginGuard, client_ip, via_proxy
from .config import EDITABLE, Settings, apply_overrides, coerce, public_view
from .fritzbox import FritzBox, Router
from .monitor import Monitor
from .notify import TEST_DEVICE, Notifier, TelegramNotifier, build_notifiers
from .store import Status, Store

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
UI_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
DEVICE_HTML = (STATIC / "device.html").read_text(encoding="utf-8")
SETTINGS_HTML = (STATIC / "settings.html").read_text(encoding="utf-8")
SETUP_HTML = (STATIC / "setup.html").read_text(encoding="utf-8")
# Gemeinsames Aussehen und Hilfsfunktionen aller Seiten; nichts Geheimes, daher ohne Anmeldung
ASSETS = {
    "app.css": ((STATIC / "app.css").read_text(encoding="utf-8"), "text/css"),
    "app.js": ((STATIC / "app.js").read_text(encoding="utf-8"), "text/javascript"),
}

# Gilt, solange kein eigenes Passwort gesetzt ist; muss beim ersten Anmelden geändert werden
DEFAULT_PASSWORD = "netzwerkmonitor"
MIN_PASSWORD_LENGTH = 8
# So oft schaut der Monitor nach einer neuen App-Version
APP_UPDATE_INTERVAL = 6 * 60 * 60
APK_TYPE = "application/vnd.android.package-archive"


class Runtime:
    """Aktuelle Einstellungen samt daraus gebauter Router-Anbindung und Benachrichtigungen.

    Wird bei Änderungen über die Einstellungsseite neu aufgebaut, ohne Neustart.
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        router: Router | None,
        notifiers: list[Notifier] | None,
    ) -> None:
        self._fixed_router = router
        self._fixed_notifiers = notifiers
        self.settings = settings
        self.router: Router = router or self._make_router(settings)
        self.notifiers: list[Notifier] = (
            notifiers if notifiers is not None else build_notifiers(settings)
        )
        self.monitor = Monitor(self.router, store, settings)

    @property
    def setup_pending(self) -> bool:
        """Noch kein eigenes Passwort gesetzt: nur die Ersteinrichtung ist erreichbar."""
        return not self.settings.ui_password

    @property
    def fritzbox_missing(self) -> bool:
        return self._fixed_router is None and not self.settings.fritz_password

    @staticmethod
    def _make_router(settings: Settings) -> Router:
        return FritzBox(settings.fritz_address, settings.fritz_user, settings.fritz_password)

    def reload(self, settings: Settings) -> None:
        old = self.settings
        fritz_changed = (old.fritz_address, old.fritz_user, old.fritz_password) != (
            settings.fritz_address,
            settings.fritz_user,
            settings.fritz_password,
        )
        if self._fixed_router is None and fritz_changed:
            self.router = self._make_router(settings)
        if self._fixed_notifiers is None:
            self.notifiers = build_notifiers(settings)
        self.settings = settings
        self.monitor.router = self.router
        self.monitor.settings = settings


class DeviceUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


class NewPassword(BaseModel):
    password: str


def create_app(
    settings: Settings,
    *,
    router: Router | None = None,
    store: Store | None = None,
    start_poller: bool = True,
    notifiers: list[Notifier] | None = None,
) -> FastAPI:
    store = store or Store(settings.db_path)
    try:
        settings = apply_overrides(settings, store.get_config())
    except ValueError as exc:
        log.warning("Gespeicherte Einstellungen ungültig, nutze Umgebung: %s", exc)
    rt = Runtime(settings, store, router, notifiers)
    monitor = rt.monitor
    state = {"last_error": None, "last_poll": None}
    app_folder = AppFolder(settings.app_dir)
    app_state: dict = {"checked": None, "message": None, "ok": None}

    def check_app_update() -> dict:
        repo = rt.settings.app_update_repo
        if not repo:
            app_state.update(ok=None, message="Automatische Suche aus")
        else:
            try:
                message = update_from_github(app_folder, repo, rt.settings.app_update_token)
                app_state.update(ok=True, message=message)
            except Exception as exc:  # noqa: BLE001 – Monitor läuft auch ohne App-Update
                log.warning("App-Update von GitHub fehlgeschlagen: %s", exc)
                app_state.update(ok=False, message=f"GitHub: {exc}")
        app_state["checked"] = datetime.now(UTC).isoformat(timespec="seconds")
        return app_status()

    def app_status() -> dict:
        latest = app_folder.latest()
        return {
            "version": latest.version if latest else None,
            "file": latest.filename if latest else None,
            **app_state,
        }

    async def app_update_loop() -> None:
        while True:
            await asyncio.to_thread(check_app_update)
            await asyncio.sleep(APP_UPDATE_INTERVAL)

    def notify_new(devices) -> None:
        for device in devices:
            for notifier in rt.notifiers:
                try:
                    notifier.new_device(device)
                except Exception as exc:  # noqa: BLE001 – Benachrichtigung darf nicht blockieren
                    log.warning("Benachrichtigung über %s fehlgeschlagen: %s", device.mac, exc)

    async def telegram_loop() -> None:
        while True:
            notifier = next((n for n in rt.notifiers if isinstance(n, TelegramNotifier)), None)
            if notifier is None:
                await asyncio.sleep(5)
                continue
            try:
                await asyncio.to_thread(notifier.process_updates, monitor.set_status)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram-Abfrage fehlgeschlagen: %s", exc)
                await asyncio.sleep(10)

    async def poll_loop() -> None:
        while True:
            if rt.fritzbox_missing:
                state["last_error"] = "FritzBox noch nicht eingerichtet (Einstellungen)"
                await asyncio.sleep(5)
                continue
            try:
                result = await asyncio.to_thread(monitor.poll_once)
                state["last_error"] = "; ".join(result.errors) or None
                state["last_poll"] = datetime.now(UTC).isoformat(timespec="seconds")
                if result.new_devices:
                    await asyncio.to_thread(notify_new, result.new_devices)
            except Exception as exc:  # noqa: BLE001 – Schleife muss weiterlaufen
                log.exception("Abfrage der FritzBox fehlgeschlagen")
                state["last_error"] = str(exc)
                if isinstance(rt.router, FritzBox):
                    rt.router.reset()
            await asyncio.sleep(rt.settings.poll_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        tasks = []
        if start_poller:
            tasks.append(asyncio.create_task(poll_loop()))
            tasks.append(asyncio.create_task(telegram_loop()))
            tasks.append(asyncio.create_task(app_update_loop()))
        yield
        for task in tasks:
            task.cancel()

    # Keine öffentliche API-Beschreibung, der Monitor ist per Reverse Proxy von außen erreichbar
    app = FastAPI(
        title="Netzwerkmonitor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.monitor = monitor
    app.state.notify_new = notify_new
    app.state.runtime = rt

    security = HTTPBasic(auto_error=False)

    guard = LoginGuard()
    app.state.login_guard = guard

    def require_password(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),  # noqa: B008
    ) -> None:
        ip = client_ip(request)
        wait = guard.retry_after(ip)
        if wait:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Zu viele falsche Passwörter, bitte später erneut versuchen",
                headers={"Retry-After": str(wait)},
            )
        password = rt.settings.ui_password or DEFAULT_PASSWORD
        ok = credentials is not None and secrets.compare_digest(
            credentials.password.encode(), password.encode()
        )
        if ok:
            guard.succeeded(ip)
            return
        if credentials is not None:
            # Feste Formulierung, damit fail2ban die Zeile erkennen kann
            log.warning("Anmeldung fehlgeschlagen von %s", ip)
            if guard.failed(ip):
                log.warning("Anmeldung für %s gesperrt nach zu vielen Fehlversuchen", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="Netzwerkmonitor"'},
        )

    def require_login(request: Request, _: None = Depends(require_password)) -> None:  # noqa: B008
        if not rt.setup_pending:
            return
        if request.url.path.startswith("/api/"):
            raise HTTPException(
                403, "Ersteinrichtung: bitte zuerst im Browser ein eigenes Passwort festlegen"
            )
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/setup"})

    def require_setup_access(request: Request, _: None = Depends(require_password)) -> None:  # noqa: B008
        # Mit dem bekannten Standardpasswort darf niemand von außen das eigene Passwort setzen
        if rt.setup_pending and via_proxy(request):
            raise HTTPException(403, "Ersteinrichtung nur im Heimnetz möglich")

    @app.get("/setup", response_class=HTMLResponse, dependencies=[Depends(require_setup_access)])
    def setup_page():
        if not rt.setup_pending:
            return RedirectResponse("/settings", status.HTTP_303_SEE_OTHER)
        return SETUP_HTML

    @app.post("/api/setup/password", dependencies=[Depends(require_setup_access)])
    def setup_password(body: NewPassword) -> dict:
        if not rt.setup_pending:
            raise HTTPException(409, "Passwort ist schon gesetzt, bitte die Einstellungen nutzen")
        password = body.password.strip()
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(400, f"Mindestens {MIN_PASSWORD_LENGTH} Zeichen")
        if password == DEFAULT_PASSWORD:
            raise HTTPException(400, "Bitte ein anderes als das Standardpasswort wählen")
        store.set_config({"ui_password": password})
        rt.reload(apply_overrides(rt.settings, {"ui_password": password}))
        return {"ok": True}

    @app.get("/assets/{name}")
    def asset(name: str) -> Response:
        if name not in ASSETS:
            raise HTTPException(404, "Nicht gefunden")
        content, media_type = ASSETS[name]
        return Response(
            content,
            media_type=f"{media_type}; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": state["last_error"] is None,
            "error": state["last_error"],
            "last_poll": state["last_poll"],
        }

    # Ohne Anmeldung, damit Obtainium die App laden kann; die APK enthält keine Zugangsdaten
    @app.get("/app", response_class=HTMLResponse)
    def app_page() -> str:
        latest = app_folder.latest()
        if latest is None:
            body = "<p>Auf diesem Monitor liegt noch keine App.</p>"
        else:
            name = html.escape(latest.filename)
            body = (
                f'<p><a href="/app/{name}">{name}</a></p>'
                "<p>Für automatische Updates diese Seite in Obtainium als Quelle hinzufügen.</p>"
            )
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Netzwerkmonitor-App</title></head><body><h1>Netzwerkmonitor-App</h1>{body}"
            "</body></html>"
        )

    @app.get("/app/latest.json")
    def app_latest() -> dict:
        latest = app_folder.latest()
        if latest is None:
            raise HTTPException(404, "Keine App bereitgestellt")
        return {
            "version": latest.version,
            "version_code": latest.version_code,
            "url": f"/app/{latest.filename}",
        }

    @app.get("/app/{filename}")
    def app_download(filename: str) -> FileResponse:
        found = app_folder.get(filename)
        if found is None:
            raise HTTPException(404, "Nicht gefunden")
        return FileResponse(found.path, media_type=APK_TYPE, filename=found.filename)

    @app.get("/api/app", dependencies=[Depends(require_login)])
    def get_app_status() -> dict:
        return app_status()

    @app.post("/api/app/check", dependencies=[Depends(require_login)])
    def post_app_check() -> dict:
        return check_app_update()

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def index() -> str:
        return UI_HTML

    @app.get("/device/{mac}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def device_page(mac: str) -> str:
        return DEVICE_HTML

    @app.get("/api/devices/{mac}", dependencies=[Depends(require_login)])
    def get_device(mac: str) -> dict:
        device = store.get(mac)
        if device is None:
            raise HTTPException(404, "Gerät nicht gefunden")
        return asdict(device)

    @app.get("/api/devices", dependencies=[Depends(require_login)])
    def list_devices() -> list[dict]:
        return [asdict(d) for d in store.list()]

    def _set(mac: str, new_status: Status) -> dict:
        try:
            device = monitor.set_status(mac, new_status)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"FritzBox-Fehler: {exc}") from exc
        if device is None:
            raise HTTPException(404, "Gerät nicht gefunden")
        return asdict(device)

    @app.post("/api/devices/remove-offline", dependencies=[Depends(require_login)])
    def remove_offline() -> dict:
        removed = store.remove_offline()
        return {"removed": len(removed)}

    @app.post("/api/devices/{mac}/allow", dependencies=[Depends(require_login)])
    def allow(mac: str) -> dict:
        return _set(mac, Status.ALLOWED)

    @app.post("/api/devices/{mac}/block", dependencies=[Depends(require_login)])
    def block(mac: str) -> dict:
        return _set(mac, Status.BLOCKED)

    @app.patch("/api/devices/{mac}", dependencies=[Depends(require_login)])
    def update(mac: str, body: DeviceUpdate) -> dict:
        device = store.update_info(mac, name=body.name, notes=body.notes)
        if device is None:
            raise HTTPException(404, "Gerät nicht gefunden")
        return asdict(device)

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def settings_page() -> str:
        return SETTINGS_HTML

    @app.get("/api/settings", dependencies=[Depends(require_login)])
    def get_settings() -> dict:
        return public_view(rt.settings)

    @app.put("/api/settings", dependencies=[Depends(require_login)])
    def put_settings(body: dict) -> dict:
        changes: dict = {}
        for field, value in body.items():
            if field not in EDITABLE:
                raise HTTPException(400, f"Unbekannte Einstellung: {field}")
            if EDITABLE[field] == "secret":
                if value == "":
                    continue  # leer = unverändert lassen
                if value is None:
                    if field == "ui_password":
                        raise HTTPException(400, "Das Passwort kann nicht entfernt werden")
                    value = ""  # null = löschen
                if field == "ui_password" and len(str(value).strip()) < MIN_PASSWORD_LENGTH:
                    raise HTTPException(400, f"Passwort: mindestens {MIN_PASSWORD_LENGTH} Zeichen")
            try:
                changes[field] = coerce(field, value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, f"{field}: {exc}") from exc
        if changes:
            new_settings = apply_overrides(rt.settings, changes)
            store.set_config(
                {k: list(v) if isinstance(v, tuple) else v for k, v in changes.items()}
            )
            rt.reload(new_settings)
        return public_view(rt.settings)

    @app.post("/api/settings/test-fritzbox", dependencies=[Depends(require_login)])
    def test_fritzbox() -> dict:
        try:
            hosts = rt.router.list_hosts()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"Keine Verbindung: {exc}"}
        active = sum(h.active for h in hosts)
        return {"ok": True, "message": f"Verbunden: {len(hosts)} Geräte bekannt, {active} aktiv"}

    @app.post("/api/settings/test-notify", dependencies=[Depends(require_login)])
    def test_notify() -> dict:
        if not rt.notifiers:
            return {"ok": False, "results": [], "message": "Kein Kanal eingerichtet"}
        results = []
        for notifier in rt.notifiers:
            name = type(notifier).__name__.removesuffix("Notifier")
            try:
                notifier.new_device(TEST_DEVICE)
                results.append({"channel": name, "ok": True, "message": "gesendet"})
            except Exception as exc:  # noqa: BLE001
                results.append({"channel": name, "ok": False, "message": str(exc)})
        return {"ok": all(r["ok"] for r in results), "results": results}

    @app.get("/api/events", dependencies=[Depends(require_login)])
    def events(limit: int = 100) -> list[dict]:
        return store.events(limit)

    return app
