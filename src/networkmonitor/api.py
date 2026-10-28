"""REST-API und einfache Weboberfläche."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .config import Settings
from .fritzbox import FritzBox, Router
from .monitor import Monitor
from .store import Status, Store

log = logging.getLogger(__name__)

UI_HTML = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


class DeviceUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


def create_app(
    settings: Settings,
    *,
    router: Router | None = None,
    store: Store | None = None,
    start_poller: bool = True,
) -> FastAPI:
    router = router or FritzBox(
        settings.fritz_address, settings.fritz_user, settings.fritz_password
    )
    store = store or Store(settings.db_path)
    monitor = Monitor(router, store, settings)
    state = {"last_error": None, "last_poll": None}

    async def poll_loop() -> None:
        while True:
            try:
                result = await asyncio.to_thread(monitor.poll_once)
                state["last_error"] = "; ".join(result.errors) or None
                state["last_poll"] = datetime.now(UTC).isoformat(timespec="seconds")
            except Exception as exc:  # noqa: BLE001 – Schleife muss weiterlaufen
                log.exception("Abfrage der FritzBox fehlgeschlagen")
                state["last_error"] = str(exc)
                if isinstance(router, FritzBox):
                    router.reset()
            await asyncio.sleep(settings.poll_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(poll_loop()) if start_poller else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Netzwerkmonitor", lifespan=lifespan)
    app.state.monitor = monitor

    security = HTTPBasic(auto_error=False)

    def require_login(
        credentials: HTTPBasicCredentials | None = Depends(security),  # noqa: B008
    ) -> None:
        if not settings.ui_password:
            return
        ok = credentials is not None and secrets.compare_digest(
            credentials.password.encode(), settings.ui_password.encode()
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Basic realm="Netzwerkmonitor"'},
            )

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": state["last_error"] is None,
            "error": state["last_error"],
            "last_poll": state["last_poll"],
        }

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_login)])
    def index() -> str:
        return UI_HTML

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

    @app.get("/api/events", dependencies=[Depends(require_login)])
    def events(limit: int = 100) -> list[dict]:
        return store.events(limit)

    return app
