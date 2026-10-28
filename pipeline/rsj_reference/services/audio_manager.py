"""Re-export AudioManager for backward-compatible root-level imports."""
from redsun.services.audio_manager import AudioManager, MUSIC_LIBRARY_DIR, MANIFEST_PATH

__all__ = ["AudioManager", "MUSIC_LIBRARY_DIR", "MANIFEST_PATH"]
