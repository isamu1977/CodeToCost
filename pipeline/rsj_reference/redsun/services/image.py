"""HTTP-backed image handoff gate service for the wired pipeline.

Since 2026-09-13 images are NOT generated inside the Python pipeline. They
are produced by the gemini-assistant Chrome extension, operated manually by
Isamu. This service is now a **handoff gate**: it verifies that the image
already exists at ``path`` (because Isamu has generated it via the extension)
and lets the pipeline continue. If the image is missing, it raises a clear,
recoverable error pointing the operator to the right tool.

See PIPELINE_STATE.md (root) for the canonical pipeline state and
``gate_image_handoff`` documentation.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("redsun.services.image")


class ImageService:
    """Image handoff gate — no longer an HTTP client to ComfyUI.

    Construct via ``ImageService.from_context(ctx)``.
    """

    def __init__(self, model: str = "epicrealism", base_seed: int = 42) -> None:
        # Kept for backward-compat with wired service signature; ignored now.
        self.model = model
        self.base_seed = base_seed

    @classmethod
    def from_context(cls, context) -> "ImageService":
        model = getattr(getattr(context, "image", None), "model", "epicrealism")
        base_seed = getattr(getattr(context, "image", None), "base_seed", 42)
        return cls(model=model, base_seed=base_seed)

    def generate(self, path: Path, beat: dict, ctx) -> None:
        """Verify that an image already exists at ``path`` (handoff gate).

        Parameters
        ----------
        path : Path
            Output file path that should contain the generated PNG.
        beat : dict
            Visual beat from the visual plan (has ``text_excerpt``, ``index``).
        ctx : ProjectContext
            Pipeline context (kept for signature compatibility).

        Behavior
        --------
        - If ``path`` exists, log and return (handoff successful).
        - If ``path`` is missing, raise ``FileNotFoundError`` with a message
          that points the operator to the gemini-assistant Chrome extension
          and to ``PIPELINE_STATE.md``.

        Notes
        -----
        The model/seed parameters are ignored: image generation moved out of
        the Python pipeline entirely. Prompt construction lives in the
        Chrome extension (see visual_bible/MASTER_VISUAL_PROMPT_V1.txt).
        """
        beat_index = beat.get("index", 0)

        if path.exists():
            logger.info(
                "Image %d: handoff OK (file already exists) → %s",
                beat_index, path,
            )
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        raise FileNotFoundError(
            f"Image {beat_index} not found at {path}. "
            f"Since 2026-09-13 images are generated via the gemini-assistant "
            f"Chrome extension (operated manually by Isamu), not by the "
            f"Python pipeline. Generate the image with the extension, save "
            f"it at the expected path, then re-run this phase. "
            f"See PIPELINE_STATE.md (canonical pipeline state) for the "
            f"canonical workflow and gate_image_handoff docs."
        )
