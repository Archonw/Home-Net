"""Stellt die Android-App zum Herunterladen bereit, damit Obtainium sie vom Monitor laden kann.

Die APK liegt als ``netzwerkmonitor-<version>.apk`` im App-Ordner (Standard ``/data/app``).
Sie kommt entweder automatisch aus den GitHub-Releases oder wird von Hand dort abgelegt.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APK_NAME = re.compile(r"^netzwerkmonitor-(\d+(?:\.\d+)*)\.apk$")
# Die CI veröffentlicht Releases als app-<Laufnummer>, die App heißt dann 0.1.<Laufnummer>
RELEASE_TAG = re.compile(r"^app-(\d+)$")
RELEASE_ASSET = "netzwerkmonitor.apk"
MAX_APK_BYTES = 100 * 1024 * 1024


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@dataclass(frozen=True)
class AppFile:
    version: str
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def version_code(self) -> int:
        """Entspricht dem versionCode der App (letzte Zahl der Version)."""
        return _key(self.version)[-1]


class AppFolder:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def files(self) -> list[AppFile]:
        if not self.dir.is_dir():
            return []
        found = []
        for path in self.dir.iterdir():
            match = APK_NAME.match(path.name)
            if match and path.is_file():
                found.append(AppFile(match.group(1), path))
        return sorted(found, key=lambda f: _key(f.version))

    def latest(self) -> AppFile | None:
        files = self.files()
        return files[-1] if files else None

    def get(self, filename: str) -> AppFile | None:
        return next((f for f in self.files() if f.filename == filename), None)

    def save(self, version: str, data: bytes) -> AppFile:
        """Legt eine neue Version ab und entfernt ältere."""
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / f"netzwerkmonitor-{version}.apk"
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        for old in self.files():
            if old.path != target:
                old.path.unlink(missing_ok=True)
        return AppFile(version, target)


class RateLimited(Exception):
    """GitHub hat das Kontingent an Abfragen für diese Adresse (oder dieses Token) aufgebraucht."""

    def __init__(self, reset: float) -> None:
        self.reset = reset
        super().__init__(limit_message(reset))


def limit_message(reset: float, now: float | None = None) -> str:
    minutes = max(1, round((reset - (now or time.time())) / 60))
    return (
        "Abfrage-Limit von GitHub erreicht (ohne Token 60 pro Stunde für das ganze Heimnetz). "
        f"Nächster Versuch in etwa {minutes} Minuten. Mit einem GitHub-Token in den Einstellungen "
        "des Monitors steigt die Grenze auf 5000 pro Stunde."
    )


@dataclass
class ReleaseCache:
    """Letzte Antwort von GitHub, damit unveränderte Listen nicht erneut geladen werden."""

    etag: str = ""
    result: tuple[str, str] | None = None
    blocked_until: float = 0.0


def _rate_limit_reset(exc: urllib.error.HTTPError) -> float | None:
    """Zeitpunkt, ab dem GitHub wieder antwortet, oder None, wenn kein Limit vorliegt."""
    if exc.code not in (403, 429):
        return None
    headers = exc.headers or {}
    remaining = headers.get("x-ratelimit-remaining")
    retry_after = headers.get("retry-after")
    if remaining != "0" and retry_after is None:
        return None
    now = time.time()
    if retry_after and retry_after.isdigit():
        return now + int(retry_after)
    reset = headers.get("x-ratelimit-reset", "")
    return float(reset) if reset.isdigit() else now + 60 * 60


def _github(url: str, token: str, accept: str) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "networkmonitor"})
    if token:
        # Nicht an die Weiterleitung zum Download-Speicher mitschicken, der lehnt sie ab
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return req


def fetch_latest_release(
    repo: str, token: str = "", timeout: float = 30, cache: ReleaseCache | None = None
) -> tuple[str, str]:
    """Version und Download-Adresse der APK aus dem Release mit der höchsten Nummer.

    Nicht ``releases/latest``: GitHub sortiert dort nach Datum des Commits, nicht nach Nummer.
    Mit ``cache`` fragt sie per ETag nur nach Änderungen und wartet nach einem Limit ab.
    """
    if cache is not None and cache.blocked_until > time.time():
        raise RateLimited(cache.blocked_until)
    req = _github(
        f"https://api.github.com/repos/{repo}/releases?per_page=50",
        token,
        "application/vnd.github+json",
    )
    if cache is not None and cache.etag and cache.result:
        req.add_header("If-None-Match", cache.etag)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            releases = json.load(resp)
            etag = resp.headers.get("ETag", "") if getattr(resp, "headers", None) else ""
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cache is not None and cache.result:
            return cache.result
        reset = _rate_limit_reset(exc)
        if reset is not None:
            if cache is not None:
                cache.blocked_until = reset
            raise RateLimited(reset) from exc
        if exc.code == 404:
            hint = "Token gespeichert?" if not token else "Hat das Token Zugriff darauf?"
            raise ValueError(f"Repository {repo} nicht gefunden. {hint}") from exc
        if exc.code == 401:
            raise ValueError("Token ungültig oder abgelaufen") from exc
        raise
    candidates = []
    for release in releases:
        match = RELEASE_TAG.match(release.get("tag_name", ""))
        if not match or release.get("draft") or release.get("prerelease"):
            continue
        asset = next((a for a in release.get("assets", []) if a.get("name") == RELEASE_ASSET), None)
        if asset is not None:
            candidates.append((int(match.group(1)), asset["url"]))
    if not candidates:
        raise ValueError(f"Kein Release mit {RELEASE_ASSET} gefunden")
    number, url = max(candidates)
    result = (f"0.1.{number}", url)
    if cache is not None:
        cache.etag, cache.result = etag, result
    return result


def download(url: str, token: str = "", timeout: float = 120) -> bytes:
    req = _github(url, token, "application/octet-stream")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_APK_BYTES + 1)
    if len(data) > MAX_APK_BYTES:
        raise ValueError("APK ist unerwartet groß")
    if not data.startswith(b"PK"):
        raise ValueError("Download ist keine APK")
    return data


def update_from_github(
    folder: AppFolder, repo: str, token: str = "", cache: ReleaseCache | None = None
) -> str:
    """Lädt die neueste App-Version, falls sie neuer ist; liefert eine Meldung."""
    version, url = fetch_latest_release(repo, token, cache=cache)
    current = folder.latest()
    if current and _key(current.version) >= _key(version):
        return f"Aktuell: {current.version}"
    saved = folder.save(version, download(url, token))
    log.info("Neue App-Version %s bereitgestellt", saved.version)
    return f"Neue Version {saved.version} geladen"
