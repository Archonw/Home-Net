"""Schutz der Anmeldung, wenn der Monitor über einen Reverse Proxy von außen erreichbar ist."""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import Request

# Nach so vielen falschen Passwörtern in WINDOW Sekunden wird die Adresse für LOCK Sekunden gesperrt
MAX_FAILURES = 5
WINDOW = 15 * 60
LOCK = 15 * 60


def client_ip(request: Request) -> str:
    """Absender der Anfrage; hinter einem Proxy der Eintrag, den der Proxy selbst angehängt hat."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unbekannt"


def via_proxy(request: Request) -> bool:
    """Kommt die Anfrage über einen Reverse Proxy (also möglicherweise aus dem Internet)?"""
    return "x-forwarded-for" in request.headers or "x-real-ip" in request.headers


class LoginGuard:
    """Zählt falsche Passwörter je Absender und sperrt nach zu vielen Versuchen."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, ip: str) -> int:
        """Sekunden, bis die Adresse es wieder versuchen darf; 0 = nicht gesperrt."""
        until = self._locked_until.get(ip)
        if until is None:
            return 0
        left = until - self._clock()
        if left <= 0:
            del self._locked_until[ip]
            return 0
        return int(left) + 1

    def failed(self, ip: str) -> bool:
        """Merkt einen Fehlversuch; True, wenn die Adresse damit gesperrt ist."""
        now = self._clock()
        if len(self._failures) > 10_000:
            # Viele verschiedene Absender: alte Einträge verwerfen, damit der Speicher nicht wächst
            self._failures = {k: v for k, v in self._failures.items() if now - v[-1] < WINDOW}
        recent = [t for t in self._failures.get(ip, []) if now - t < WINDOW]
        recent.append(now)
        if len(recent) >= MAX_FAILURES:
            self._failures.pop(ip, None)
            self._locked_until[ip] = now + LOCK
            return True
        self._failures[ip] = recent
        return False

    def succeeded(self, ip: str) -> None:
        self._failures.pop(ip, None)
