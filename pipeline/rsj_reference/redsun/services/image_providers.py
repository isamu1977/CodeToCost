"""Image provider registry — post-ComfyUI removal.

Since 2026-09-15 the wired pipeline has a single image backend:
the gemini-assistant Chrome extension, operated manually by Isamu. There is
no in-process image provider anymore — the
``redsun.services.image.ImageService`` is a **handoff gate** that verifies
already-generated assets exist on disk.

This module is kept as a registry skeleton so future providers (e.g. a
local Stable Diffusion backend) can be wired without reshaping the wired
pipeline. For now it exports a single ``PROVIDERS`` dict that resolves to
the gate service.

See PIPELINE_STATE.md for the canonical pipeline state.
"""
from __future__ import annotations

from typing import Callable, Dict

from redsun.services.image import ImageService

logger = __import__("logging").getLogger("redsun.services.image_providers")


# Backend → factory mapping. The wired pipeline picks the entry by
# ``context.image.backend``. ``handoff`` is the only supported value
# since ComfyUI was removed on 2026-09-15.
PROVIDERS: Dict[str, Callable[..., ImageService]] = {
    "handoff": ImageService,
}


def get_provider(name: str) -> Callable[..., ImageService]:
    """Return the factory for ``name`` or raise ``ValueError``."""
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown image backend {name!r}. Available: {sorted(PROVIDERS)!r}. "
            f"Note: 'comfyui' / 'comfy' were removed on 2026-09-15. The pipeline "
            f"now uses the gemini-assistant Chrome extension as the image source."
        ) from exc
