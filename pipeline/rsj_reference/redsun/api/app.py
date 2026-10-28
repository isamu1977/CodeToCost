"""FastAPI app factory.

Mounts the workspace router and any future router split out of
the legacy `main.py`.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .routers import workspace


logger = logging.getLogger("redsun.api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Red Sun Japan — Video Automation Engine",
        version="6.0-phase0",
        description=(
            "Phase 10 split: legacy endpoints remain in `main.py` "
            "for now; new workspace / checkpoint / artifact-manifest "
            "endpoints are exposed via this app."
        ),
    )
    app.include_router(workspace.router)
    return app


app = create_app()