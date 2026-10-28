"""Phase 8: image validator."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..exceptions import ImagesValidationError
from . import ValidationResult


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_images(
    images_dir,
    visual_plan,
    *,
    min_image_bytes: int = 10_000,
) -> ValidationResult:
    """Reject:
    - count mismatch with visual_plan.beat_count
    - any file smaller than `min_image_bytes` (likely generation
      failure)
    - any file that is not a decodable PNG (when Pillow is available)
    - duplicate file hashes
    """
    from ..phases.visual_plan import VisualPlan
    from .. import defaults

    images_dir = Path(images_dir)
    result = ValidationResult(
        metrics={
            "images_dir": str(images_dir),
            "expected_beat_count": getattr(visual_plan, "beat_count", None),
        }
    )

    if not images_dir.exists():
        result.fail(f"images directory missing: {images_dir}")
        raise ImagesValidationError("; ".join(result.errors))

    pngs = sorted(images_dir.glob("*.png"))
    result.metrics["found_count"] = len(pngs)

    if isinstance(visual_plan, VisualPlan):
        expected = visual_plan.beat_count
    elif isinstance(visual_plan, int):
        expected = visual_plan
    else:
        expected = None

    if expected is not None and len(pngs) != expected:
        result.fail(
            f"image count mismatch: found {len(pngs)}, expected {expected}"
        )

    seen_hashes: set[str] = set()
    duplicate_count = 0
    for p in pngs:
        if p.stat().st_size < min_image_bytes:
            result.fail(
                f"image too small: {p.name} = {p.stat().st_size} bytes"
            )
        try:
            digest = _hash_file(p)
        except Exception as exc:
            result.fail(f"cannot hash {p.name}: {exc}")
            continue
        if digest in seen_hashes:
            duplicate_count += 1
            result.fail(f"duplicate image: {p.name} hash={digest[:12]}")
        else:
            seen_hashes.add(digest)
    result.metrics["duplicate_count"] = duplicate_count

    # Optional Pillow decode
    try:
        from PIL import Image  # type: ignore

        for p in pngs:
            try:
                with Image.open(p) as im:
                    im.verify()
            except Exception as exc:
                result.fail(f"image not decodable: {p.name}: {exc}")
    except ImportError:
        # PIL not available — skip decode check.
        pass

    if not result.ok:
        raise ImagesValidationError("; ".join(result.errors))
    return result