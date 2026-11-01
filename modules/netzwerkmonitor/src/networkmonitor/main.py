"""Startpunkt für den Dienst."""

from __future__ import annotations

import logging
import os

import uvicorn

from .api import create_app
from .config import Settings


def run() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    app = create_app(settings)
    socket_path = os.environ.get("LISTEN_SOCKET")
    if socket_path:
        # Im Baukasten: kein offener Port, nur der Zugang erreicht den Dienst.
        uvicorn.run(app, uds=socket_path)
    else:
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8085")))


if __name__ == "__main__":
    run()
