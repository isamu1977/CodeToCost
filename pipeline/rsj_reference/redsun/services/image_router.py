"""
Image Router for Red Sun Japan
==============================
Routes scene generation requests to the appropriate provider based on:
- Provider hint in the shot spec
- Type of scene (landscape, character, hero)
- Reference image requirements

This is the single entry point for image generation in the pipeline.
Replaces the old SDXL-only approach.

Author: JingJing (Kaoru) for Isamu
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

from .image_providers import (
    ImageProvider,
    ImageResult,
    get_provider,
)


# =============================================================================
# Scene classification
# =============================================================================

@dataclass
class SceneClassification:
    """How a scene is classified for routing."""
    primary_provider: str
    fallback_providers: List[str]
    reasoning: str
    estimated_cost_usd: float


def classify_scene(shot_spec: dict) -> SceneClassification:
    """Classify a scene to determine which provider to use.

    Rules:
    - provider_hint in shot_spec overrides everything
    - Pure landscapes/atmospheric → sdxl_local (free)
    - Single character, standard scene → nano_banana (cost-efficient)
    - Multiple characters or hero moment → gpt_image_1.5 (premium quality)
    - Editing request → flux_kontext
    """
    # Explicit provider hint takes priority
    hint = shot_spec.get("provider_hint")
    if hint:
        return SceneClassification(
            primary_provider=hint,
            fallback_providers=["nano_banana", "gpt_image_1.5"],
            reasoning=f"Explicit hint: {hint}",
            estimated_cost_usd=0.04,
        )

    characters = shot_spec.get("characters", [])
    shot = shot_spec.get("shot", {})
    shot_type = shot.get("type", "medium")

    # No characters = pure landscape/environment
    if not characters:
        return SceneClassification(
            primary_provider="sdxl_local",
            fallback_providers=["nano_banana"],
            reasoning="No characters — landscape/environment, use SDXL local (free)",
            estimated_cost_usd=0.0,
        )

    # Multiple characters = hero shot
    if len(characters) >= 2:
        return SceneClassification(
            primary_provider="gpt_image_1.5",
            fallback_providers=["nano_banana"],
            reasoning=f"{len(characters)} characters — complex composition, use GPT Image 1.5",
            estimated_cost_usd=0.04,
        )

    # Single character + hero shot types
    hero_types = {"extreme_close_up", "close_up"}
    if shot_type in hero_types:
        return SceneClassification(
            primary_provider="gpt_image_1.5",
            fallback_providers=["nano_banana"],
            reasoning=f"Hero shot ({shot_type}) — premium quality for character close-up",
            estimated_cost_usd=0.04,
        )

    # Single character, standard scene
    return SceneClassification(
        primary_provider="nano_banana",
        fallback_providers=["gpt_image_1.5"],
        reasoning="Single character, standard scene — Nano Banana is cost-efficient",
        estimated_cost_usd=0.039,
    )


# =============================================================================
# Image Router
# =============================================================================

class ImageRouter:
    """Routes image generation requests to the best provider."""

    def __init__(self):
        self._providers: Dict[str, ImageProvider] = {}
        self._load_providers()

    def _load_providers(self):
        """Lazily load available providers (skip those with missing API keys)."""
        providers_to_try = {
            "gpt_image_1.5": lambda: get_provider("gpt_image_1.5"),
            "nano_banana": lambda: get_provider("nano_banana"),
            "flux_kontext": lambda: get_provider("flux_kontext"),
            "sdxl_local": lambda: get_provider("sdxl_local"),
        }
        for name, loader in providers_to_try.items():
            try:
                self._providers[name] = loader()
            except Exception as e:
                # Provider not available (missing API key, etc.)
                pass

    @property
    def available_providers(self) -> List[str]:
        return list(self._providers.keys())

    def generate_scene(
        self,
        prompt: str,
        shot_spec: dict,
        reference_images: Optional[List[Path]] = None,
        character_id: Optional[str] = None,
        **kwargs,
    ) -> ImageResult:
        """Generate an image for a scene, using the appropriate provider.

        Args:
            prompt: Full scene prompt (style + character + scene)
            shot_spec: The Shot Specification dict
            reference_images: List of paths to character/style reference images
            character_id: Optional character ID for logging

        Returns:
            ImageResult with the generated image
        """
        classification = classify_scene(shot_spec)
        primary = classification.primary_provider

        if primary not in self._providers:
            # Fallback to first available
            if not self._providers:
                return ImageResult(
                    success=False,
                    error="No image providers available (check API keys)",
                )
            primary = next(iter(self._providers))
            classification.reasoning += f" [FALLBACK: {primary} not available]"

        provider = self._providers[primary]

        return provider.generate(
            prompt=prompt,
            reference_images=reference_images,
            aspect_ratio=shot_spec.get("aspect_ratio", "16:9"),
            quality=kwargs.get("quality", "medium"),
            **kwargs,
        )

    def generate_character_reference(
        self,
        prompt: str,
        style_references: List[Path],
        character_id: str,
        aspect_ratio: str = "1:1",
    ) -> ImageResult:
        """Generate a character reference image (always uses GPT Image 1.5 for quality)."""
        # Character refs need maximum consistency → GPT Image 1.5
        provider_name = "gpt_image_1.5"
        if provider_name not in self._providers:
            # Fallback to nano_banana
            provider_name = "nano_banana" if "nano_banana" in self._providers else next(iter(self._providers))

        provider = self._providers[provider_name]
        return provider.generate(
            prompt=prompt,
            reference_images=style_references,
            aspect_ratio=aspect_ratio,
            quality="high",  # character refs need high quality
        )

    def edit_image(
        self,
        source_image: Path,
        edit_instruction: str,
        preserve_references: Optional[List[Path]] = None,
    ) -> ImageResult:
        """Edit an existing image using FLUX Kontext."""
        provider_name = "flux_kontext"
        if provider_name not in self._providers:
            return ImageResult(
                success=False,
                error="FLUX Kontext provider not available",
            )

        provider = self._providers[provider_name]
        refs = [source_image]
        if preserve_references:
            refs.extend(preserve_references)

        return provider.generate(
            prompt=edit_instruction,
            reference_images=refs,
            quality="medium",
        )


# =============================================================================
# Convenience function
# =============================================================================

_router_instance: Optional[ImageRouter] = None


def get_router() -> ImageRouter:
    """Get the singleton ImageRouter instance."""
    global _router_instance
    if _router_instance is None:
        _router_instance = ImageRouter()
    return _router_instance


def generate_scene_image(
    prompt: str,
    shot_spec: dict,
    reference_images: Optional[List[Path]] = None,
) -> ImageResult:
    """Convenience function: generate a scene image."""
    return get_router().generate_scene(prompt, shot_spec, reference_images)


if __name__ == "__main__":
    router = get_router()
    print("Available providers:", router.available_providers)
