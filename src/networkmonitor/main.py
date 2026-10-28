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
    if not settings.ui_password:
        logging.warning("UI_PASSWORD ist leer – die Weboberfläche ist ohne Anmeldung erreichbar.")
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8085")))


if __name__ == "__main__":
    run()
