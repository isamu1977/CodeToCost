"""Red Sun Japan — Video Generation Pipeline.

Handles scene splitting, image prompt generation, audio generation,
and ComfyUI integration for Japanese horror/kaidan content.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from redsun.services.style_constants import (
    MASTER_STYLE_BIBLE,
    format_prompt_with_style_bible,
)

logger = logging.getLogger("redsun.generate_video")

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")


def split_into_scenes(script: str, max_scenes: int = 8) -> list[str]:
    """Split script into scenes (paragraphs) capped at max_scenes.

    If there are more paragraphs than max_scenes, merge the smallest ones
    into the previous until we're at the cap. Target: each scene gets a
    roughly equal share of the script.
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(script) if p.strip()]
    if len(paragraphs) <= max_scenes:
        return paragraphs

    # Sort by length descending, keep the longest; merge the rest.
    # A simple approach: walk and merge short ones into the previous.
    scenes: list[str] = [paragraphs[0]]
    for para in paragraphs[1:]:
        if len(scenes) >= max_scenes:
            scenes[-1] = scenes[-1] + " " + para
        elif len(para) < 250 and len(scenes) > 0:
            # Short paragraph — merge into the previous scene
            scenes[-1] = scenes[-1] + " " + para
        else:
            scenes.append(para)
    return scenes


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline state
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Scene:
    index: int
    text: str
    image_prompt: str
    theme_hint: str = ""
    image_path: str | None = None
    audio_path: str | None = None
    audio_duration_sec: float | None = None
    error: str | None = None


@dataclass
class VideoManifest:
    slug: str
    script_path: str
    model: str
    voice: str
    scene_count: int
    total_chars: int
    estimated_duration_sec: float
    scenes: list[Scene] = field(default_factory=list)
    final_audio_path: str | None = None
    generated_at: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Per-scene image generation
# ─────────────────────────────────────────────────────────────────────────────

def _build_image_prompt(scene_text: str, theme_hint: str = "") -> str:
    """Turn a narration scene into a ComfyUI image prompt.

    This is a FALLBACK generator — it extracts visual cues from the scene
    text and appends the generic style prefix. It produces reasonable prompts
    for any theme but is intentionally basic; the LLM-driven endpoint
    ``POST /projects/{id}/image-prompts`` should be preferred when available
    (it generates per-scene prompts with full theme awareness).

    Parameters
    ----------
    scene_text : str
        The narration paragraph for this scene.
    theme_hint : str
        Optional theme name (e.g. "yuki_onna", "mujina") that prefixes
        the prompt so the image model knows what entity/subject to draw.
    """
    # Take the first ~120 chars of the scene as the visual subject
    hook = scene_text[:120].strip().rstrip(",.;:")
    narrative = f"{theme_hint}, {hook}" if theme_hint else hook
    return format_prompt_with_style_bible(narrative, aspect_ratio="16:9")


def build_image_prompts_for_script(
    scenes_text: list[str],
    theme_name: str,
) -> list[dict]:
    """Build scene image prompts using the LLM (theme-aware).

    TWO-PASS approach for maximum quality:
    1. First call: extract character visual descriptions from the script
    2. Second call: generate SDXL-optimized image prompts per scene

    Returns a list of dicts: [{scene_index, prompt, scene_type}, ...]

    IMPORTANT: This function requires OPENROUTER_API_KEY. If the key
    is not configured, it falls back to _build_image_prompt() which
    is purely heuristic-based (no LLM call).
    """
    try:
        import os
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set — using heuristic fallback")
    except Exception:
        logger.info(
            "LLM unavailable or no API key — using heuristic prompt builder (theme=%s)",
            theme_name,
        )
        results = []
        for idx, text in enumerate(scenes_text):
            results.append({
                "scene_index": idx + 1,
                "prompt": _build_image_prompt(text, theme_hint=theme_name),
            })
        return results

    from llm_client import LLMConfig, _call_openrouter, extract_json_safely

    cfg = (cfg if 'cfg' in locals() and cfg else LLMConfig.from_env()).for_utility()

    # ── PASS 1: Extract character visual descriptions ──────────────
    full_script = "\n\n".join(scenes_text)
    char_ref = _extract_character_references(full_script, theme_name, cfg)

    # ── PASS 2: Generate image prompts ─────────────────────────────
    system_prompt = (
        "You are an expert image-prompt engineer for cinematic historical dark fantasy.\n\n"
        "Your task: convert NARRATION SCENES into VISUAL IMAGE DESCRIPTIONS.\n\n"
        "=== CRITICAL RULES ===\n\n"
        "1. NEVER use character names (e.g. 'Hoichi', 'Yuki Onna', 'Kappa'). "
        "Image models do not know fictional characters. ALWAYS describe their physical "
        "appearance instead: 'blind young man with shaved head', 'pale woman in "
        "white kimono', 'green turtle-like creature'.\n\n"
        "2. Describe WHAT IS SEEN IN THE IMAGE, not what is happening in the story. "
        "BAD: 'A samurai came to summon Hoichi'. "
        "GOOD: 'armored samurai ghost standing at temple entrance, rain-soaked "
        "courtyard, paper lanterns, moonlight'.\n\n"
        "3. Each prompt describes subjects, appearance (clothing, features, posture), "
        "environment (location, weather, time), lighting, mood, camera angle.\n\n"
        "4. PROMPT LENGTH: 60-140 words. Concise, atmospheric, visual.\n\n"
        "5. STYLE BIBLE (adhere strictly): "
        "'Cinematic semi-realistic historical dark fantasy digital painting, "
        "grounded painterly realism, atmospheric depth, muted desaturated palette "
        "(charcoal black, dark indigo, weathered brown, amber lantern light). "
        "STYLE EXCLUSIONS: no anime, no manga, no cel shading, no 3D render, no photography.'\n\n"
        "6. CONSISTENCY: Use the CHARACTER REFERENCE below to keep characters "
        "looking the same across scenes. Always use the same visual descriptors "
        "for the same character.\n\n"
        "7. MANDATORY TERMINATOR: EVERY prompt MUST end strictly with the sentence: "
        "'Single unified composition.'\n\n"
        "8. SCENE TYPE: classify as 'character' (character is the main subject) "
        "or 'environment' (setting/atmosphere is the focus, character secondary).\n\n"
        "=== CHARACTER REFERENCE ===\n"
        f"{char_ref}\n\n"
        "=== OUTPUT FORMAT ===\n"
        "Return a valid JSON array. Each object: "
        '{"scene_index": 1, "prompt": "...", "scene_type": "character"}\n'
        "Return ONLY the JSON array. No markdown, no explanation."
    )

    # Build user prompt with scene text (500 chars per scene — enough context)
    scenes_block = ""
    for idx, text in enumerate(scenes_text):
        scene_excerpt = text[:500].strip()
        scenes_block += f"\nScene {idx + 1}:\n{scene_excerpt}\n"

    user_prompt = (
        f"Theme: {theme_name}\n"
        f"Total scenes: {len(scenes_text)}\n"
        f"{scenes_block}\n"
        f"Generate exactly {len(scenes_text)} image prompts as a JSON array."
    )

    try:
        result = _call_openrouter(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            cfg=cfg,
            json_mode=False,
            max_retries=3,
            temperature=0.6,
            max_tokens=4000,
        )
        raw = result.text.strip()

        # Resilient JSON extraction
        prompts_list = extract_json_safely(raw)
        if not isinstance(prompts_list, list):
            raise ValueError(f"No valid JSON array found in LLM response: {raw[:300]}")

        # Validate and clean prompts
        results = []
        for idx in range(len(scenes_text)):
            if idx < len(prompts_list):
                item = prompts_list[idx]
                if isinstance(item, dict):
                    prompt = str(item.get("prompt") or "").strip()
                    scene_type = str(item.get("scene_type") or "environment").strip().lower()
                else:
                    prompt = str(item).strip()
                    scene_type = "environment"
            else:
                prompt = ""
                scene_type = "environment"

            # Validate: not empty, not too short
            if not prompt or prompt == "." or len(prompt) < 20:
                logger.warning(
                    "Scene %d: LLM returned invalid prompt (%r) — using heuristic fallback",
                    idx + 1, prompt[:50],
                )
                prompt = _build_image_prompt(scenes_text[idx], theme_hint=theme_name)
                scene_type = "environment"
            else:
                prompt = format_prompt_with_style_bible(prompt, aspect_ratio="16:9")

            if scene_type not in {"character", "environment"}:
                scene_type = "environment"

            results.append({
                "scene_index": idx + 1,
                "prompt": prompt,
                "scene_type": scene_type,
            })

        logger.info("Image prompts generated: %d scenes (visual-description mode)", len(results))
        return results

    except Exception as exc:
        logger.warning("LLM image prompt generation failed: %s — using heuristic fallback", exc)
        results = []
        for idx, text in enumerate(scenes_text):
            results.append({
                "scene_index": idx + 1,
                "prompt": _build_image_prompt(text, theme_hint=theme_name),
            })
        return results


def _extract_character_references(
    script: str, theme_name: str, cfg=None,
) -> str:
    """Extract visual character descriptions from the script.

    Returns a formatted string like:
        - Blind young man (protagonist): shaved head, simple brown pilgrim
          robes, wooden sandals, biwa lute strapped to back, thin build
        - Samurai ghost: full traditional armor (ō-yoroi), iron face,
          glowing eyes, tall, imposing build

    This ensures consistent character appearance across all scene images.
    """
    from llm_client import LLMConfig, _call_openrouter

    cfg = (cfg or LLMConfig.from_env()).for_utility()

    # Use first 3000 chars of script for character extraction
    script_excerpt = script[:3000]

    system_prompt = (
        "You are a visual character designer for Japanese historical dark fantasy.\n\n"
        "Analyze the narration script and extract ALL characters that appear. "
        "For each character, describe their VISUAL APPEARANCE in detail:\n"
        "- Physical features (age, build, hair, face)\n"
        "- Clothing/armor/accessories\n"
        "- Posture/typical pose\n"
        "- Distinctive visual markers\n\n"
        "RULES:\n"
        "1. Do NOT use character names as descriptions. "
        "Describe what they LOOK LIKE physically.\n"
        "2. If the script doesn't specify details, infer from context "
        "(time period, social role, setting).\n"
        "3. Be specific about Japanese historical clothing/armor.\n"
        "4. Format as a simple list, one character per line.\n\n"
        "OUTPUT FORMAT:\n"
        "- [role]: [visual description]\n"
        "- [role]: [visual description]\n\n"
        "Return ONLY the list. No explanation, no markdown headers."
    )

    user_prompt = (
        f"Theme: {theme_name}\n\n"
        f"Script excerpt:\n{script_excerpt}\n\n"
        "Extract all characters and their visual descriptions."
    )

    try:
        result = _call_openrouter(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            cfg=cfg,
            json_mode=False,
            max_retries=2,
            temperature=0.4,
            max_tokens=500,
        )
        char_ref = result.text.strip()
        logger.info("Character references extracted:\n%s", char_ref)
        return char_ref
    except Exception as exc:
        logger.warning("Character extraction failed: %s — using theme name as fallback", exc)
        return f"- Protagonist: described in theme '{theme_name}'"


def generate_scene_image(
    scene: Scene, model: str, output_dir: Path, base_seed: int
) -> None:
    """Stub kept for backwards compatibility — image generation moved off this module.

    Since 2026-09-13 images are generated via the gemini-assistant Chrome
    extension, operated manually by Isamu. The pipeline PAUSES at
    gate_image_handoff and resumes once images appear in
    ``projects/<slug>/images/``.

    See PIPELINE_STATE.md (root) for the canonical pipeline state.
    This stub is intentionally a no-op: returning normally lets legacy callers
    proceed without crashing, while logging a clear pointer for anyone reading
    the code.
    """
    logger.info(
        "scene %d: image generation handled by gemini-assistant (manual). "
        "Model/base_seed arguments are ignored. See PIPELINE_STATE.md.",
        scene.index,
    )
    scene.image_path = None


# ─────────────────────────────────────────────────────────────────────────────
# Per-scene audio generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_scene_audio(
    scene: Scene, voice: str, output_dir: Path, speed: float = 0.9
) -> None:
    """Run Kokoro to generate one audio chunk for a scene."""
    generate_tts = _get_tts()

    logger.info("scene %d: generating audio (voice=%s, %d chars)...", scene.index, voice, len(scene.text))
    t0 = time.time()
    try:
        wav_path = output_dir / f"scene{scene.index:02d}.wav"
        result = generate_tts(
            text=scene.text,
            output_path=str(wav_path),
            speaker=voice,
        )
        scene.audio_path = result.wav_path
        scene.audio_duration_sec = result.duration_sec
        logger.info("scene %d: audio generated in %.1fs (%.1fs audio) → %s",
                     scene.index, time.time() - t0, result.duration_sec, wav_path)
    except Exception as e:
        scene.error = f"audio: {e}"
        logger.error("scene %d: audio FAILED: %s", scene.index, e)


def _get_tts():
    """Lazy-import TTS engine."""
    from tts_engine import generate_tts
    return generate_tts


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="Red Sun Japan Video Generator")
    parser.add_argument("--script", type=str, required=True, help="Path to script file")
    parser.add_argument("--slug", type=str, required=True, help="Project slug")
    parser.add_argument("--voice", type=str, default="am_michael", help="Kokoro voice")
    parser.add_argument("--scenes", type=int, default=12, help="Max scenes")
    parser.add_argument("--output-dir", type=str, default="./video", help="Output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # Load script
    script_text = Path(args.script).read_text(encoding="utf-8")
    scenes_text = split_into_scenes(script_text, max_scenes=args.scenes)

    logger.info("Script: %d chars → %d scenes", len(script_text), len(scenes_text))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate prompts
    logger.info("PHASE 1: IMAGE PROMPTS")
    prompts = build_image_prompts_for_script(scenes_text, args.slug)

    # Build scenes
    scenes = []
    for idx, text in enumerate(scenes_text):
        prompt = prompts[idx]["prompt"] if idx < len(prompts) else _build_image_prompt(text, args.slug)
        scenes.append(Scene(index=idx + 1, text=text, image_prompt=prompt, theme_hint=args.slug))

    # Generate images (no-op stub: handled by gemini-assistant)
    logger.info("PHASE 2: IMAGE GENERATION")
    for scene in scenes:
        generate_scene_image(scene, "", output_dir, base_seed=42)

    # Generate audio
    logger.info("PHASE 3: AUDIO GENERATION")
    for scene in scenes:
        generate_scene_audio(scene, args.voice, output_dir)

    # Summary
    manifest = VideoManifest(
        slug=args.slug,
        script_path=args.script,
        model=args.model,
        voice=args.voice,
        scene_count=len(scenes),
        total_chars=len(script_text),
        estimated_duration_sec=sum(s.audio_duration_sec or 0 for s in scenes),
        scenes=scenes,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, default=str, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Manifest saved → %s", manifest_path)
