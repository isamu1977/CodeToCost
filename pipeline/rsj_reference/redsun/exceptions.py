"""Typed exceptions for the Red Sun Japan pipeline.

Every validator and phase MUST raise a subclass of RedsunError so
callers can catch without depending on string messages.
"""


class RedsunError(Exception):
    """Base exception for all redsun pipeline errors."""


class WorkspaceError(RedsunError):
    """Raised when ProjectWorkspace cannot resolve a path or create a directory."""


class SanitizationError(RedsunError):
    """Raised when sanitize_for_tts cannot produce a clean narration."""


class NarrationValidationError(RedsunError):
    """Raised when validate_narration rejects narration.txt."""


class AudioValidationError(RedsunError):
    """Raised when validate_audio rejects a generated WAV."""


class TranscriptValidationError(RedsunError):
    """Raised when validate_transcript rejects a Whisper transcript."""


class VisualPlanValidationError(RedsunError):
    """Raised when validate_visual_plan rejects a low-density plan."""


class ManifestValidationError(RedsunError):
    """Raised when validate_render_manifest rejects a manifest."""


class VideoValidationError(RedsunError):
    """Raised when validate_main_video or validate_final_video rejects a render."""


class ImagesValidationError(RedsunError):
    """Raised when validate_images rejects image artifacts."""


class CheckpointCorruptError(RedsunError):
    """Raised when a checkpoint JSON cannot be parsed or is missing required fields."""


class InvalidationError(RedsunError):
    """Raised when the downstream invalidation cascade cannot proceed."""


class ValidationError(RedsunError):
    """Generic validation failure (carries a ValidationResult)."""