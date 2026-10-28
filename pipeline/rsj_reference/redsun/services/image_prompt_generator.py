"""Narrative-anchored visual prompt generator with automated linter & self-correction.

Extracts time-synced audio beats (subtitles/transcript or script paragraphs),
calls Gemini 2.5 Flash via OpenRouter with strict variety rules, and validates
via VisualPromptLinter with automated self-correction loops.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from redsun.schemas.gemini import GeminiSceneTask, GeminiTaskOutput

from llm_client import LLMConfig, _call_openrouter, extract_json_safely
from redsun.services.style_constants import (
    format_prompt_with_style_bible,
    sanitize_visual_prompt,
)
from redsun.services.visual_prompt_linter import VisualPromptLinter
from redsun.services.entity_binder import bind_entities_to_scene, inject_physical_anchors

logger = logging.getLogger("redsun.image_prompt_generator")

import math
import wave

TOTAL_SCENE_COUNT = 20


def calculate_dynamic_scene_count(total_duration_seconds: float) -> int:
    """Calculates optimal scene count ensuring 10-12s pacing (never > 12-14s per image)."""
    if total_duration_seconds <= 0:
        return 25
    return max(25, math.ceil(total_duration_seconds / 12.0))


def _clean_and_complete_sentence(text: str) -> str:
    """Guarantees text ends on a complete, grammatically sound sentence without sliced fragments."""
    s = str(text or "").strip()
    if not s:
        return "Atmospheric Edo period Japanese scene under low evening light."

    # Remove any trailing incomplete fragment (e.g. ' st.', ' o.', ' T.', ' but o.', ' and th.')
    s = re.sub(r"[\s,;—\-]+[a-zA-Z]{1,3}[\.,]?$", "", s).strip()

    # Remove trailing conjunctions or prepositions
    s = re.sub(
        r"\b(and|or|but|of|with|that|which|because|if|when|as|the|at|by|for|to)[\.,]?$",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()

    # Strip lingering punctuation before adding terminal period
    s = s.rstrip(" ,;:-—").strip()

    if not s.endswith((".", "!", "?")):
        last_punct = max(s.rfind("."), s.rfind("!"), s.rfind("?"))
        if last_punct > 40 and len(s) - last_punct < 30:
            s = s[: last_punct + 1].strip()
        else:
            s = f"{s}."

    return s



def slice_narrative_into_beats(
    script_text: str = "",
    transcript_path: Optional[Path] = None,
    subtitles_path: Optional[Path] = None,
    audio_path: Optional[Path] = None,
    scene_count: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Slice narrative into continuous temporal slices with exact audio timecodes (10-12s pacing)."""
    # 1. Determine total audio duration
    total_duration = 0.0

    if audio_path and Path(audio_path).exists():
        try:
            with wave.open(str(audio_path), "rb") as w:
                rate = w.getframerate()
                frames = w.getnframes()
                if rate and frames:
                    total_duration = frames / rate
        except Exception as exc:
            logger.debug("Could not read duration from audio_path %s: %s", audio_path, exc)

    sub_data = []
    if subtitles_path and Path(subtitles_path).exists():
        try:
            sub_data = json.loads(Path(subtitles_path).read_text(encoding="utf-8"))
            if isinstance(sub_data, list) and sub_data:
                last_end = float(sub_data[-1].get("end") or 0.0)
                if last_end > total_duration:
                    total_duration = last_end
        except Exception as exc:
            logger.debug("Could not read subtitles JSON: %s", exc)

    words_data = []
    if transcript_path and Path(transcript_path).exists():
        try:
            words_data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
            if isinstance(words_data, list) and words_data and "start" in words_data[0]:
                last_end = float(words_data[-1].get("end") or words_data[-1].get("start") or 0.0)
                if last_end > total_duration:
                    total_duration = last_end
        except Exception as exc:
            logger.debug("Could not read transcript JSON: %s", exc)

    if total_duration <= 0.0:
        total_duration = 300.0  # Fallback: 5 minutes

    target_count = scene_count or calculate_dynamic_scene_count(total_duration)
    interval = total_duration / target_count

    beats: list[dict[str, Any]] = []

    # Prefer word-level transcript if available
    if words_data:
        for i in range(target_count):
            t_start = i * interval
            t_end = (i + 1) * interval
            matched_words = [
                w["word"].strip()
                for w in words_data
                if (w.get("start", 0) >= t_start or not beats) and w.get("start", 0) < t_end
            ]
            if not matched_words:
                idx = int((t_start / total_duration) * len(words_data))
                window = words_data[max(0, idx - 10) : min(len(words_data), idx + 20)]
                matched_words = [w["word"].strip() for w in window]

            text_slice = " ".join(matched_words).strip()
            m1, s1 = int(t_start // 60), int(t_start % 60)
            m2, s2 = int(t_end // 60), int(t_end % 60)
            beats.append({
                "index": i + 1,
                "start_sec": round(t_start, 2),
                "end_sec": round(t_end, 2),
                "duration_sec": round(t_end - t_start, 2),
                "timecode": f"{m1:02d}:{s1:02d} - {m2:02d}:{s2:02d}",
                "narration": text_slice,
            })
        mark_archival_bridge_beat(beats)
        return beats

    # Else use subtitles JSON if available
    if sub_data:
        for i in range(target_count):
            t_start = i * interval
            t_end = (i + 1) * interval
            # Extract items that fall within or overlap [t_start, t_end]
            matched_subs = [
                item.get("text", "").strip()
                for item in sub_data
                if float(item.get("end", 0)) > t_start and float(item.get("start", 0)) < t_end
            ]
            text_slice = " ".join(matched_subs).strip()
            if not text_slice and script_text:
                # Fallback to proportion of script
                ratio_start = i / target_count
                ratio_end = (i + 1) / target_count
                chars = len(script_text)
                text_slice = script_text[int(ratio_start * chars) : int(ratio_end * chars)].strip()

            m1, s1 = int(t_start // 60), int(t_start % 60)
            m2, s2 = int(t_end // 60), int(t_end % 60)
            beats.append({
                "index": i + 1,
                "start_sec": round(t_start, 2),
                "end_sec": round(t_end, 2),
                "duration_sec": round(t_end - t_start, 2),
                "timecode": f"{m1:02d}:{s1:02d} - {m2:02d}:{s2:02d}",
                "narration": text_slice,
            })
        mark_archival_bridge_beat(beats)
        return beats

    # Fallback: slice script paragraphs / sentences
    clean_text = str(script_text or "").strip()
    if not clean_text:
        clean_text = "Edo period ghost story narrative unfolding under low twilight."

    from generate_video import split_into_scenes
    raw_chunks = split_into_scenes(clean_text, max_scenes=target_count)
    if len(raw_chunks) < target_count:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_text) if s.strip()]
        if len(sentences) >= target_count:
            step = len(sentences) / target_count
            raw_chunks = [
                " ".join(sentences[int(i * step) : int((i + 1) * step)])
                for i in range(target_count)
            ]
        else:
            while len(raw_chunks) < target_count:
                raw_chunks.append(raw_chunks[-1])

    beats = []
    sec_per_frame = total_duration / target_count
    for i in range(target_count):
        t_start = i * sec_per_frame
        t_end = (i + 1) * sec_per_frame
        m1, s1 = int(t_start // 60), int(t_start % 60)
        m2, s2 = int(t_end // 60), int(t_end % 60)
        chunk = raw_chunks[i] if i < len(raw_chunks) else f"Narrative moment {i+1}"
        beats.append({
            "index": i + 1,
            "start_sec": round(t_start, 2),
            "end_sec": round(t_end, 2),
            "duration_sec": round(t_end - t_start, 2),
            "timecode": f"{m1:02d}:{s1:02d} - {m2:02d}:{s2:02d}",
            "narration": chunk[:350].strip(),
        })
    mark_archival_bridge_beat(beats)
    return beats


def slice_narrative_into_20_beats(
    script_text: str = "",
    transcript_path: Optional[Path] = None,
    subtitles_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Legacy alias: slice narrative into exactly 20 beats."""
    return slice_narrative_into_beats(
        script_text=script_text,
        transcript_path=transcript_path,
        subtitles_path=subtitles_path,
        scene_count=20,
    )


ARCHIVAL_BRIDGE_KEYWORDS_PATTERN = re.compile(
    r"\b(story|comes\s+down|manuscript|archive|archives|recorded|collection|chronicle|chronicles|written|printed|scholar|scholars|account|accounts|newspaper|newspapers|records|dossier|dossiers)\b",
    re.IGNORECASE,
)


def mark_archival_bridge_beat(beats: list[dict[str, Any]]) -> Optional[int]:
    """
    Identifies which scene between seconds 35.0 and 90.0 contains documentary provenance markers.
    Falls back to the scene covering the 50.0s mark if no keyword is detected.
    Marks that beat with 'is_archival_bridge' = True and returns its 0-indexed position.
    """
    if not beats:
        return None

    # Clear previous markings
    for b in beats:
        b["is_archival_bridge"] = False

    # Filter candidate beats overlapping the [35.0, 90.0] window
    candidates: list[tuple[int, dict[str, Any], float, float]] = []
    for i, b in enumerate(beats):
        t_start = float(b.get("start_sec", 0.0))
        t_end = float(b.get("end_sec", t_start + 12.0))
        if t_end >= 35.0 and t_start <= 90.0:
            candidates.append((i, b, t_start, t_end))

    # 1. Search for keyword match in candidate beats
    matched_idx: Optional[int] = None
    for i, b, _, _ in candidates:
        narr = str(b.get("narration") or "")
        if ARCHIVAL_BRIDGE_KEYWORDS_PATTERN.search(narr):
            matched_idx = i
            break

    # 2. Fallback: select beat covering the 50.0s mark
    if matched_idx is None:
        for i, b, t_start, t_end in candidates:
            if t_start <= 50.0 <= t_end:
                matched_idx = i
                break

    # 3. If still None, pick candidate closest to 50.0s
    if matched_idx is None and candidates:
        matched_idx = min(
            candidates,
            key=lambda c: abs(((c[2] + c[3]) / 2.0) - 50.0),
        )[0]
    elif matched_idx is None and beats:
        # Fallback if audio is short or boundaries shifted
        for i, b in enumerate(beats):
            t_start = float(b.get("start_sec", 0.0))
            t_end = float(b.get("end_sec", t_start + 12.0))
            if t_start <= 50.0 <= t_end:
                matched_idx = i
                break
        if matched_idx is None:
            matched_idx = min(len(beats) - 1, 2)

    if matched_idx is not None and 0 <= matched_idx < len(beats):
        beats[matched_idx]["is_archival_bridge"] = True
        logger.info(
            "Archival Bridge beat identified at index %d (scene %d, %.1fs-%.1fs).",
            matched_idx,
            beats[matched_idx].get("index", matched_idx + 1),
            float(beats[matched_idx].get("start_sec", 0.0)),
            float(beats[matched_idx].get("end_sec", 0.0)),
        )
        return matched_idx

    return None


SYSTEM_PROMPT_SCENE_GENERATOR = """You are a prestigious visual narrative director for a Japanese historical dark fantasy YouTube documentary channel (Red Sun Japan).
You convert documentary voiceover audio excerpts into highly cinematic, diverse, and narratively concrete image generation prompts for Google Gemini (Pacing: 10-12s per scene).

=== CRITICAL REQUIREMENTS ===
1. STRICT VISUAL DESCRIPTION ONLY (ABSOLUTE PROHIBITION OF STORY EXPOSITION / BACKSTORY):
   - The image is a SILENT, STATIC PAINTING. An AI image generator paints physical objects, light, and people; it CANNOT paint backstory, family history, dialogue, or time passing.
   - NEVER narrate or recite plot facts, character histories, family background, or spoken dialogue.
   - BANNED EXAMPLES (NEVER WRITE THESE):
     * BAD: "the daughter of a minor official who had been stationed in the provinces and had recently died, leaving her without protection in the city. She was st."
     * BAD: "always arrived at the same hour, always accompanied by the maid who carried the peony lantern, and always left before the first temple bell of dawn."
     * BAD: "he heard a sound at his bamboo gate, a soft rhythmic tapping..."
   - GOOD EQUIVALENT VISUALS (ALWAYS WRITE LIKE THIS):
     * GOOD: "Close-up shot of a delicate young Japanese woman with downcast mournful eyes, pale skin illuminated by soft twilight, wearing a thin amber-orange summer yukata, standing hesitantly before a weathered wooden threshold."
     * GOOD: "Medium shot of two figures at midnight: a young ethereal woman accompanied by a middle-aged maid carrying a glowing peony lantern with carved floral ribs, footsteps silent on damp mossy paving stones."
     * GOOD: "Close-up detail shot of an exterior bamboo gate under full moonlight, a slender hand resting on the weathered latch, dew glistening on the dark timber in cold nighttime fog."
   - Every sentence must be 100% complete and end with proper punctuation. NEVER leave cut-off phrases or truncated words.

2. ABSOLUTE PROHIBITION OF META-TEMPLATES:
   - NEVER write "Cinematic historical frame X", "Detailed Edo period scene under low atmospheric...", "Frame X showing...", "A scene depicting...", "Tense close-up detail shot focusing on specific objects and reactions:".
   - Start immediately with the visual framing and action (e.g. "Close-up of...", "Wide shot across...", "Over-the-shoulder medium shot of...").

3. MANDATORY CAMERA SCALE ALTERNATION:
   - You MUST vary the camera distance across all scenes:
     * Wide Shot / Establishing Shot (environmental geography, mood, weather, architecture)
     * Medium Shot (character interactions, body language, emotional tension)
     * Close-up / Extreme Close-up (hands holding objects, flame, writing ofuda talismans, terrified eyes, dripping blood/water)
   - AT LEAST 30% OF ALL SCENES MUST BE CLOSE-UP OR DETAIL SHOTS. This is strictly audited by an automated linter.

4. CONCRETE PHYSICAL ANCHORS:
   - Each frame MUST illustrate tangible physical subjects, props, architecture, and lighting sources matching the mood of that audio interval.
   - Every prompt must be completely unique: NO two prompts may share repetitive descriptive phrasing or identical settings.

5. ASSET LINKING:
   - Pick relevant reference asset IDs from the provided catalog for each scene (e.g. ["style-master", "character-shinzaburo"]).

6. ABSOLUTE PROHIBITION OF PROJECT METADATA & TITLES:
   - NEVER inject the project title, series name, episode title, or phrases like "for <Project Title>" into the scene description.
   - The scene description must open DIRECTLY on the camera scale and concrete physical subject or setting (e.g., "Wide shot across a mist-shrouded riverbank...", "Close-up of weathered hands holding...").
   - Any inclusion of project titles dilutes diffusion attention (token bleed) and causes unwanted text generation.

=== OUTPUT FORMAT ===
Return ONLY a valid JSON array of scene objects. No markdown fences, no explanatory text.
Each object must follow:
[
  {
    "scene_index": 1,
    "scale": "Wide Shot | Medium Shot | Close-up",
    "title": "Short descriptive scene title",
    "description": "Concrete 50-90 word visual description of the camera shot, specific lighting, subject action, props, and textures without fixed style bible.",
    "references": ["style-master", "character-shinzaburo"]
  },
  ...
]
"""


def _generate_beats_batch(
    slug: str,
    title: str,
    batch_beats: list[dict[str, Any]],
    reference_assets: dict[str, Any],
    cfg: LLMConfig,
    asset_summary: str,
) -> list[dict[str, Any]]:
    """Calls Gemini 2.5 Flash for a discrete batch of beats (max 25) with fallback."""
    count = len(batch_beats)
    beats_block = []
    for b in batch_beats:
        idx = b["index"]
        t_start = b.get("start_sec", 0.0)
        t_end = b.get("end_sec", 12.0)
        tc = b.get("timecode", f"{int(t_start//60):02d}:{int(t_start%60):02d}")
        narr = b.get("narration", "").strip()
        archival_instruction = ""
        if b.get("is_archival_bridge"):
            archival_instruction = (
                "[ARCHIVAL_BRIDGE_SCENE: Must depict a close-up of the historical manuscript, "
                "scroll, or archival dossier described in this narration segment, resting on a dark desk under low amber light.]\n"
            )
        beats_block.append(
            f"Scene {idx} [{tc}]:\n"
            f"{archival_instruction}"
            f"No intervalo {t_start:.1f}s a {t_end:.1f}s, o áudio narra o seguinte momento: '{narr}'.\n"
            f"Instrução: Gere uma descrição PURAMENTE VISUAL (enquadramento de câmera, luz prática, personagens, vestimentas, cenário, texturas) para ilustrar esta cena em uma pintura estática. NUNCA resuma a história nem use frases do narrador.\n"
        )
    user_prompt = (
        f"Project Title: {title} (slug: {slug})\n\n"
        f"Available Reference Assets:\n{asset_summary}\n\n"
        f"Narration Audio Beats ({count} chronological frames):\n"
        f"{''.join(beats_block)}\n\n"
        f"Generate all {count} distinct cinematic scene objects as a JSON array adhering to all variety and close-up rules."
    )

    logger.info("Calling Gemini 2.5 Flash for %d narrative scene prompts (%s)...", count, slug)
    try:
        raw_response = _call_openrouter(
            cfg=cfg,
            system_prompt=SYSTEM_PROMPT_SCENE_GENERATOR,
            user_prompt=user_prompt,
            temperature=0.6,
            max_tokens=min(12000, max(3000, count * 250)),
        )
        batch_json = extract_json_safely(raw_response.text)
        if isinstance(batch_json, list) and len(batch_json) >= int(count * 0.7):
            return batch_json
    except Exception as exc:
        logger.warning("Batch generation failed for %d scenes: %s", count, exc)

    logger.warning("Using resilient visual scene generator for batch of %d scenes.", count)
    return _heuristic_narrative_scene_generator(title, batch_beats, reference_assets)


def generate_narrative_scene_prompts(
    slug: str,
    title: str,
    narrative_beats: list[dict[str, Any]],
    reference_assets: dict[str, Any],
    cfg: Optional[LLMConfig] = None,
    max_correction_attempts: int = 2,
) -> list[GeminiSceneTask]:
    """Generate dynamic narrative scene tasks verified by VisualPromptLinter with auto-correction."""
    cfg = (cfg or LLMConfig.from_env()).for_utility()
    target_count = len(narrative_beats)
    min_detail_count = max(1, int(target_count * 0.30))
    linter = VisualPromptLinter(similarity_threshold=0.50, min_detail_ratio=0.30)

    # Ensure Archival Bridge beat is identified and marked
    archival_bridge_idx = mark_archival_bridge_beat(narrative_beats)

    # Build reference assets catalog summary for LLM prompt
    asset_lines = []
    for aid, ainfo in reference_assets.items():
        lbl = getattr(ainfo, "label", aid) if hasattr(ainfo, "label") else ainfo.get("label", aid)
        atype = getattr(ainfo, "type", "asset") if hasattr(ainfo, "type") else ainfo.get("type", "asset")
        asset_lines.append(f"- ID: {aid} ({atype}): {lbl}")
    asset_summary = "\n".join(asset_lines) if asset_lines else "- ID: style-master (style): Master visual anchor"

    # Generate scenes in discrete batches of max 25 to guarantee LLM responsiveness and avoid token cuts
    batch_size = 25
    if target_count <= batch_size:
        scenes_json = _generate_beats_batch(slug, title, narrative_beats, reference_assets, cfg, asset_summary)
    else:
        scenes_json = []
        for start_idx in range(0, target_count, batch_size):
            chunk = narrative_beats[start_idx : start_idx + batch_size]
            chunk_scenes = _generate_beats_batch(slug, title, chunk, reference_assets, cfg, asset_summary)
            scenes_json.extend(chunk_scenes)

    # Pad or trim to target_count
    while len(scenes_json) < target_count:
        idx = len(scenes_json) + 1
        scenes_json.append({
            "scene_index": idx,
            "scale": "Close-up" if idx % 3 == 0 else "Medium Shot",
            "title": f"Scene {idx:02d}",
            "description": (
                "Close-up detail shot of an inscribed ofuda paper talisman pasted onto a weathered wooden door, "
                "vermilion ink faintly glowing under low candle lighting with deep natural shadows."
                if idx % 3 == 0 else
                "Atmospheric medium shot within an Edo-period Japanese setting, "
                "muted indigo and charcoal tones with paper shoji screens and soft ambient moonlight."
            ),
            "references": ["style-master"],
        })
    scenes_json = scenes_json[:target_count]

    # Pre-enforce Archival Bridge artifact scene before linter checks
    linter.auto_fix_archival_bridge(scenes_json, target_scene_idx=archival_bridge_idx)

    # Run Linter & Auto-Correction Loop
    attempt = 0
    while attempt <= max_correction_attempts:
        # Pre-bind references for linter validation
        for i, sc in enumerate(scenes_json):
            b_narr = narrative_beats[i].get("narration", "") if i < len(narrative_beats) else ""
            sc["references"] = bind_entities_to_scene(
                prompt=sc.get("description", ""),
                title=sc.get("title", ""),
                narration=b_narr,
                available_assets=reference_assets,
                current_refs=sc.get("references", []),
                max_refs=3,
            )

        is_valid, errors = linter.validate(
            scenes_json, asset_catalog=reference_assets, project_title=title
        )
        if is_valid:
            logger.info("✓ VisualPromptLinter PASSED on attempt %d: all %d scenes unique and diverse.", attempt + 1, target_count)
            break

        attempt += 1
        logger.warning(
            "VisualPromptLinter FAILED (attempt %d/%d) with %d errors:\n%s",
            attempt, max_correction_attempts, len(errors), "\n".join(f"  - {e}" for e in errors)
        )

        if attempt > max_correction_attempts or target_count > 30:
            logger.warning("Applying programmatic diversity fixes for %d scenes (attempt %d).", target_count, attempt)
            scenes_json = _apply_programmatic_diversity_fixes(scenes_json, errors, narrative_beats, title)
            break

        # Auto-Correction Agent prompt
        correction_prompt = (
            f"The {target_count} generated visual scenes were AUDITED and REJECTED by our VisualPromptLinter with the following critical issues:\n\n"
            + "\n".join(f"• {e}" for e in errors)
            + "\n\n"
            f"Here are the original scenes and spoken narration:\n"
            + "\n".join(
                f"Scene {s.get('scene_index', i+1)}: scale={s.get('scale', 'Wide')} | current description='{s.get('description', '')[:100]}...'\n"
                f"Spoken audio: \"{narrative_beats[i].get('narration', '')[:180]}\""
                for i, s in enumerate(scenes_json)
            )
            + "\n\n"
            f"INSTRUCTIONS FOR AUTO-CORRECTION:\n"
            f"1. Completely rewrite the duplicate or generic scenes identified in the errors.\n"
            f"2. Ensure AT LEAST {min_detail_count} scenes are explicit 'Close-up' or 'Extreme Close-up' framing specific details (hands, ofuda talisman, lantern flame, expressions, wet stones).\n"
            f"3. Eliminate any remaining generic phrases ('Cinematic historical frame', 'Detailed scene of').\n"
            f"4. For any scene mentioning characters, explicitly include their asset IDs in 'references'.\n"
            f"Return the corrected full {target_count}-scene JSON array ONLY."
        )

        corr_response = _call_openrouter(
            cfg=cfg,
            system_prompt=SYSTEM_PROMPT_SCENE_GENERATOR,
            user_prompt=correction_prompt,
            temperature=0.7,
            max_tokens=min(12000, max(4000, target_count * 250)),
        )
        corrected_list = extract_json_safely(corr_response.text)
        if isinstance(corrected_list, list) and len(corrected_list) == target_count:
            scenes_json = corrected_list

    # Final conversion to GeminiSceneTask objects with Master Style Bible formatting
    from redsun.schemas.gemini import GeminiSceneTask, GeminiTaskOutput

    tasks: list[GeminiSceneTask] = []
    for i, sc in enumerate(scenes_json, 1):
        task_id = f"shot-{i:03d}"
        t_title = sc.get("title") or f"Shot {i:02d}: {sc.get('scale', 'Scene')}"
        raw_desc = _clean_and_complete_sentence(sc.get("description") or "")
        scale = sc.get("scale", "")

        scale_prefix = f"[{scale}] " if scale and not raw_desc.startswith("[") else ""
        narrative_core = f"{scale_prefix}{raw_desc}".strip()

        beat_narration = narrative_beats[i - 1].get("narration", "") if i - 1 < len(narrative_beats) else ""
        raw_refs = sc.get("references", ["style-master"])
        if not isinstance(raw_refs, list):
            raw_refs = ["style-master"]

        bound_refs = bind_entities_to_scene(
            prompt=narrative_core,
            title=t_title,
            narration=beat_narration,
            available_assets=reference_assets,
            current_refs=raw_refs,
            max_refs=3,
        )

        anchored_core = inject_physical_anchors(narrative_core, bound_refs)
        anchored_core = sanitize_visual_prompt(anchored_core, project_title=title, slug=slug)
        anchored_core = _clean_and_complete_sentence(anchored_core)
        prompt = format_prompt_with_style_bible(
            anchored_core, aspect_ratio="16:9", project_title=title, slug=slug
        )

        basename = f"shot_{i:03d}"
        tasks.append(
            GeminiSceneTask(
                id=task_id,
                title=t_title,
                prompt=prompt,
                references=bound_refs,
                output=GeminiTaskOutput(basename=basename),
            )
        )

    return tasks


def _heuristic_narrative_scene_generator(
    title: str,
    narrative_beats: list[dict[str, Any]],
    reference_assets: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generates structurally diverse visual scenes directly depicting Edo dark fantasy aesthetics."""
    scenes = []
    char_keys = [
        k
        for k, v in reference_assets.items()
        if getattr(v, "type", "") == "character"
        or (isinstance(v, dict) and v.get("type") == "character")
    ]

    scales = [
        "Wide Shot",
        "Medium Shot",
        "Close-up",
        "Medium Shot",
        "Close-up",
        "Wide Shot",
        "Medium Shot",
        "Extreme Close-up",
        "Medium Shot",
        "Close-up",
        "Wide Shot",
        "Close-up",
    ]

    close_up_archetypes = [
        "Extreme close-up shot of trembling hands holding an inscribed ofuda paper talisman, vermilion calligraphy dissolving in damp mountain air, aged cedar wood in deep shadow.",
        "Close-up detail shot of a carved wooden lantern, soft warm amber glow illuminating delicate paper ribs, dark mist swirling around.",
        "Close-up shot of terrified dark almond eyes reflecting the warm amber flicker of a dying candle, beaded sweat on pale skin under heavy atmospheric tension.",
        "Macro detail shot of a slender hand resting on the weathered bamboo latch of a gate, rain droplets clinging to dark lichen-covered wood under midnight moonlight.",
        "Close-up shot of weathered prayer beads sliding slowly through calloused fingers, faint incense smoke curling across ancient saffron-dyed Buddhist robes.",
        "Tight shot of weathered autumn leaves resting motionless on wet stone stairs, surrounded by cold morning dew and grey granite textures.",
        "Extreme close-up on the edge of an old inkstone and wet calligraphy brush, black sumi ink pooling on textured washi paper beside a sputtering wick.",
    ]

    wide_archetypes = [
        "Wide establishing shot across a desolate traditional residential lane in Edo Japan at deep dusk, dark timber fencing, damp gravel road slick with moisture, and heavy low-hanging fog.",
        "Atmospheric wide frame of ancient temple grounds under midnight rain, crooked pine branches silhouetted against bruised storm clouds, mossy stone lanterns cold and dark.",
        "Wide shot of an overgrown cemetery on a hillside overlooking the bay, leaning stone markers with worn inscriptions, tangled summer weeds whispering in the damp sea breeze.",
        "Establishing wide shot of a traditional wooden Edo townhouse under full moonlight, paper shoji windows casting faint amber geometric squares onto the dark earth below.",
        "Wide shot looking down a narrow canal embankment lined with weeping willow branches swaying gently in humid nocturnal air, distant paper lanterns blurred in mist.",
    ]

    medium_archetypes = [
        "Over-the-shoulder medium shot inside a sparse six-mat tatami room, low wooden desk holding a single candle, paper screens casting elongated dramatic shadows across the floor.",
        "Medium shot of a figure in dark indigo cotton robes sitting frozen on a veranda, staring into the dark courtyard with posture rigid with quiet dread.",
        "Medium shot capturing two figures at a threshold: an ethereal pale woman in an amber-toned summer yukata accompanied by an attendant holding a soft glowing lantern.",
        "Side-profile medium shot of an elderly Buddhist priest chanting solemnly before a shadowed temple altar, gold leaf statues dimly visible through incense haze.",
        "Medium shot of a traveler pausing at the turn of a narrow dirt lane, deep blue twilight sky framing dark tiled roofs and silent merchant facades.",
    ]

    for i, beat in enumerate(narrative_beats):
        idx = i + 1
        scale = scales[i % len(scales)]

        if beat.get("is_archival_bridge"):
            scale = "Close-up"
            desc = (
                "Close-up macro shot of an aged Japanese bound manuscript with brushed sumi-ink calligraphy "
                "resting open on a weathered dark wooden desk under the warm flickering amber glow of an oil lamp, "
                "deep shadows surrounding."
            )
            title_text = f"Shot {idx:02d}: Archival Provenance"
        elif "Close" in scale:
            desc = close_up_archetypes[i % len(close_up_archetypes)]
            title_text = f"Shot {idx:02d}: {scale}"
        elif "Wide" in scale:
            desc = wide_archetypes[i % len(wide_archetypes)]
            title_text = f"Shot {idx:02d}: {scale}"
        else:
            desc = medium_archetypes[i % len(medium_archetypes)]
            title_text = f"Shot {idx:02d}: {scale}"

        desc = sanitize_visual_prompt(desc, project_title=title)
        desc = _clean_and_complete_sentence(desc)

        refs = ["style-master"]
        if char_keys:
            refs.append(char_keys[i % len(char_keys)])

        scenes.append({
            "scene_index": idx,
            "scale": scale,
            "title": title_text,
            "description": desc,
            "references": refs,
        })
    return scenes


def _apply_programmatic_diversity_fixes(
    scenes: list[dict[str, Any]],
    errors: list[str],
    narrative_beats: list[dict[str, Any]],
    title: str,
) -> list[dict[str, Any]]:
    """Applies non-destructive deterministic fixes ensuring >= 30% close-ups, removing generic templates and completing sentences."""
    fixed = list(scenes)
    for i in range(len(fixed)):
        desc = fixed[i].get("description", "")
        scale = fixed[i].get("scale", "")

        # Clean generic / meta templates and narration leak phrases
        desc = re.sub(
            r"cinematic\s+historical\s+frame\s+\d+.*?(under\s+low|\.)",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()
        desc = re.sub(
            r"^tense\s+close-up\s+detail\s+shot\s+focusing\s+on\s+specific\s+objects\s+and\s+reactions:\s*",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()
        desc = re.sub(
            r"^distinct\s+historical\s+scene\s+showing:\s*",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()

        # If description was empty or invalid, provide a rich visual baseline
        if not desc or len(desc) < 25:
            desc = (
                "Close-up detail shot of an inscribed ofuda paper talisman pasted onto a weathered wooden door, "
                "vermilion ink faintly glowing under low candle lighting with deep natural shadows."
                if (i + 1) % 3 == 0
                else "Atmospheric medium shot within an Edo-period Japanese setting, "
                "muted indigo and charcoal tones with paper shoji screens and soft ambient moonlight."
            )

        # Sanitize any title pollution
        desc = sanitize_visual_prompt(desc, project_title=title)

        # Check if designated as Archival Bridge
        is_archival = False
        if i < len(narrative_beats) and narrative_beats[i].get("is_archival_bridge"):
            is_archival = True

        if is_archival:
            scale = "Close-up"
            if not any(
                k in desc.lower()
                for k in (
                    "manuscript",
                    "scroll",
                    "calligraphy",
                    "book",
                    "ink",
                    "archive",
                    "document",
                    "newspaper",
                    "dossier",
                    "desk",
                )
            ):
                desc = (
                    "Close-up macro shot of an aged Japanese bound manuscript with brushed sumi-ink calligraphy "
                    "resting open on a weathered dark wooden desk under the warm flickering amber glow of an oil lamp, "
                    "deep shadows surrounding."
                )
        # Force close-ups on every 3rd frame to guarantee >= 30% close-ups without destroying scene content
        elif (i + 1) % 3 == 0:
            scale = "Close-up"
            if not any(
                k in desc.lower()
                for k in (
                    "close",
                    "macro",
                    "detail",
                    "tight",
                    "hands",
                    "eyes",
                    "talisman",
                    "flame",
                    "petal",
                )
            ):
                reframed = re.sub(
                    r"^(wide\s+shot|establishing\s+shot|medium\s+shot|over-the-shoulder\s+medium\s+shot|wide\s+establishing\s+shot)\s+(of|across|showing)\s+",
                    "Close-up detail shot focusing on the tactile textures and subtle details of ",
                    desc,
                    flags=re.IGNORECASE,
                )
                if reframed == desc:
                    desc = f"Close-up detail shot focusing on intricate textures and physical reactions: {desc}"
                else:
                    desc = reframed
        elif (i + 1) % 4 == 0:
            scale = "Wide Shot"
            if "close" in desc.lower()[:30]:
                desc = re.sub(
                    r"^(close-up|tight\s+shot|extreme\s+close-up)\s+(of|on|across)\s+",
                    "Wide shot across ",
                    desc,
                    flags=re.IGNORECASE,
                )
        else:
            if not scale:
                scale = "Medium Shot"

        # Guarantee complete, grammatically sound sentence
        desc = _clean_and_complete_sentence(desc)

        fixed[i]["scale"] = scale
        fixed[i]["description"] = desc
    return fixed
