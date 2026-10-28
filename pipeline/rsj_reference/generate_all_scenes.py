#!/usr/bin/env python3
"""
Generate all 32 scene images for Hoichi the Earless.
Uses Nano Banana (via OpenRouter) with 16:9 aspect ratio for YouTube.

Cost estimate: ~$1.28 for 32 scenes at $0.04 each.
"""
from __future__ import annotations

import os
import sys
import warnings
import time
from pathlib import Path

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR / "projects" / "hoichi_the_earless"
MOTOR_PYTHON = SCRIPT_DIR / "motor-python"

env_path = MOTOR_PYTHON / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key] = val

sys.path.insert(0, str(MOTOR_PYTHON))
sys.path.insert(0, str(PROJECT_ROOT / "prompts"))

from redsun.services.image_providers import OpenRouterProvider
from prompt_builder import build_scene_prompt, load_visual_bible, load_character_bible, load_shot_spec


def get_character_refs(char_id: str) -> list:
    """Get character reference images."""
    char_dir = PROJECT_ROOT / "visual_bible" / "characters" / char_id
    if not char_dir.exists():
        return []
    refs = []
    for f in ["master_front.png", "master_3quarter.png", "master_side.png"]:
        p = char_dir / f
        if p.exists():
            refs.append(str(p))
    return refs


def get_style_refs() -> list:
    """Get style reference images."""
    style_dir = PROJECT_ROOT / "visual_bible" / "style"
    return sorted([str(p) for p in style_dir.glob("*.png")])


def generate_scene(scene_num: int, output_dir: Path, provider: OpenRouterProvider) -> tuple:
    """Generate a single scene image."""
    # Map scene numbers to filenames
    scene_files = {
        1: "01_scene.yaml", 2: "02_scene.yaml", 3: "03_hoichi_alone_temple.yaml",
        4: "04_scene.yaml", 5: "05_hoichi_stands_temple.yaml", 6: "06_scene.yaml",
        7: "07_scene.yaml", 8: "08_path_to_graveyard.yaml", 9: "09_scene.yaml",
        10: "10_scene.yaml", 11: "11_scene.yaml", 12: "12_scene.yaml",
        13: "13_scene.yaml", 14: "14_scene.yaml", 15: "15_scene.yaml",
        16: "16_scene.yaml", 17: "17_scene.yaml", 18: "18_scene.yaml",
        19: "19_scene.yaml", 20: "20_scene.yaml", 21: "21_scene.yaml",
        22: "22_scene.yaml", 23: "23_scene.yaml", 24: "24_scene.yaml",
        25: "25_scene.yaml", 26: "26_scene.yaml", 27: "27_scene.yaml",
        28: "28_scene.yaml", 29: "29_scene.yaml", 30: "30_scene.yaml",
        31: "31_scene.yaml", 32: "32_scene.yaml",
    }

    scene_file = scene_files.get(scene_num)
    if not scene_file:
        return False, 0.0, f"Unknown scene {scene_num}"

    # Try to load shot spec; if doesn't exist, create a simple one
    shot = None
    try:
        shot = load_shot_spec(shot_filename=scene_file)
    except Exception:
        # Create a basic shot spec on the fly
        shot = create_basic_shot(scene_num)

    # Load Visual Bible
    visual_bible = load_visual_bible()

    # Determine which characters are needed
    char_ids = []
    for char in shot.get("characters", []):
        cid = char.get("id", "")
        if cid and cid not in char_ids:
            char_ids.append(cid)

    # Load character bibles
    char_bibles = []
    for cid in char_ids:
        try:
            char_bibles.append(load_character_bible(character_id=cid))
        except Exception:
            pass

    # Build prompt
    prompt = build_scene_prompt(visual_bible, char_bibles, shot)

    # Get all references
    all_refs = []
    for cid in char_ids:
        all_refs.extend(get_character_refs(cid))
    all_refs.extend(get_style_refs())

    # Override provider_hint to use OpenRouter Nano Banana
    shot["provider_hint"] = "nano_banana_openrouter"

    # Generate
    result = provider.generate(
        prompt=prompt,
        reference_images=all_refs,
        model="google/gemini-2.5-flash-image",
        aspect_ratio="16:9",
        quality="medium",
    )

    if result.success:
        output_path = output_dir / f"scene_{scene_num:02d}.png"
        result.save(str(output_path))
        return True, result.cost_usd, None
    else:
        return False, 0.0, result.error


def create_basic_shot(scene_num: int) -> dict:
    """Create a basic shot spec for scenes without explicit files."""
    scene_data = {
        1: ("environment", "rain-soaked stone path to Akama temple, dark night, paper lanterns, mist", "rain_path"),
        2: ("character", "armored samurai ghost walking in heavy rain toward temple", "samurai_arrives"),
        4: ("character", "samurai ghost gripping biwa player Hoichi in dim temple, iron grip", "samurai_enters"),
        6: ("environment", "Akama temple cemetery at night, ancient stone tombs, ghostly lights", "cemetery"),
        7: ("environment", "Dan-no-ura sea channel at night, stormy waves, ghostly ships", "sea"),
        9: ("character", "blind Hoichi walking through rain, feet on wet stone, biwa in hand", "following"),
        10: ("character", "noblewoman in silk court robes facing Hoichi in Heian hall", "noblewoman"),
        11: ("character", "Hoichi playing biwa intensely, sweat on brow, ghostly audience", "playing"),
        12: ("character", "Heian ghosts and nobles weeping, court ladies in silk", "weeping"),
        13: ("character", "Hoichi walking at night through village under moon", "seven_nights"),
        14: ("character", "Hoichi returning to temple at dawn, misty", "returns_dawn"),
        15: ("character", "old priest observing suspiciously, bearded, monk robes", "priest"),
        16: ("character", "servants following Hoichi through dark, paper lanterns", "servants"),
        17: ("character", "Hoichi kneeling at Heike tomb playing biwa, ghost fire floating", "at_tomb"),
        18: ("character", "servants witnessing ghosts around Hoichi, terrified expressions", "witness"),
        19: ("character", "old priest confronting Hoichi in dim hall, oil lamp", "confronts"),
        20: ("character", "priest writing sutra on Hoichi's body with brush, candle light", "writes_sutra"),
        21: ("character", "Hoichi covered in sutra text, solemn, eyes unfocused", "sutra_on"),
        22: ("character", "samurai ghost returning to temple at night, misty", "returns"),
        23: ("character", "samurai confused, looking at empty space where Hoichi should be", "cant_see"),
        24: ("character", "samurai searching around Hoichi, hand on sword, supernatural", "searches"),
        25: ("character", "close-up of samurai finding Hoichi's ear in darkness", "finds_ear"),
        26: ("character", "samurai's supernatural hand gripping Hoichi's ear, horror", "tear"),
        27: ("character", "close-up of Hoichi in pain, blood from ear, eyes tight shut", "silent"),
        28: ("character", "samurai walking away into mist with trophy", "leaves"),
        29: ("character", "priest returning at dawn to find bloodied Hoichi", "priest_returns"),
        30: ("character", "priest realizing sutra mistake, holding Hoichi's ear wound", "discovers"),
        31: ("character", "Hoichi healed, playing biwa again in sunlight, calm", "healing"),
        32: ("environment", "Akama temple at sunset, peaceful, establishing final shot", "epilogue"),
    }

    scene_type, narrative, _ = scene_data.get(scene_num, ("medium", f"Scene {scene_num}", "scene"))

    # Determine characters
    chars = []
    if "hoichi" in narrative.lower() or "biwa" in narrative.lower() or "blind" in narrative.lower():
        chars.append({"id": "hoichi", "position": "foreground", "action": narrative, "expression": "appropriate", "pose": "natural"})
    if "samurai" in narrative.lower() or "ghost" in narrative.lower():
        chars.append({"id": "samurai_warrior", "position": "midground", "action": narrative, "expression": "severe", "pose": "commanding"})

    return {
        "scene_id": f"{scene_num:02d}",
        "sequence": scene_num,
        "narrative_beat": narrative,
        "shot": {"type": scene_type, "angle": "eye_level"},
        "characters": chars,
        "location": {"description": "Akama temple or related, Heian period"},
        "lighting": {"primary_source": "paper lanterns or candles"},
        "emotion": ["melancholic", "supernatural"],
        "critical_elements": ["historical Japanese setting", "no modern objects"],
        "provider_hint": "nano_banana_openrouter",
        "aspect_ratio": "16:9",
    }


def main():
    output_dir = PROJECT_ROOT / "images" / "v2_generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    provider = OpenRouterProvider()

    total_cost = 0.0
    success_count = 0
    fail_count = 0

    print("=" * 60)
    print("Generating 32 scene images for Hoichi the Earless")
    print(f"Output: {output_dir}")
    print(f"Provider: Nano Banana (google/gemini-2.5-flash-image via OpenRouter)")
    print(f"Aspect: 16:9 (1344x768)")
    print("=" * 60)

    for scene_num in range(1, 33):
        print(f"\n[{scene_num}/32] Scene {scene_num:02d}...", end=" ")
        sys.stdout.flush()

        success, cost, error = generate_scene(scene_num, output_dir, provider)

        if success:
            total_cost += cost
            success_count += 1
            print(f"✅ ${cost:.3f}")
        else:
            fail_count += 1
            print(f"❌ {error[:100] if error else 'unknown'}")

    print("\n" + "=" * 60)
    print(f"DONE: {success_count}/32 scenes generated")
    print(f"Total cost: ${total_cost:.3f}")
    print(f"Failed: {fail_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
