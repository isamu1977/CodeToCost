"""Forced Alignment Engine for Red Sun Japan Subtitles.

Synchronizes Faster-Whisper word-level timestamps with the authoritative script (script_en.md).
Guarantees:
- Timestamps (start/end) are calibrated to spoken audio via Faster-Whisper.
- The visual text/spelling displayed on screen is 100% sourced from script_en.md
  (fixing misrecognitions like 'oil' -> 'O-Yone', 'kitchen by' -> 'Kichibei', 'jeez' -> 'Jizō').
- Whisper hallucinations / insertions are safely discarded.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Any

logger = logging.getLogger("redsun.subtitle_aligner")


def _normalize_token(token: str) -> str:
    """Normalize token for phonological similarity comparison (lowercase, alphanumeric only)."""
    return re.sub(r"[^a-z0-9]", "", token.lower())


def clean_script_for_subtitles(script_text: str) -> list[str]:
    """Extract ordered display words from a script markdown/plain-text document.
    
    Preserves authentic casing, punctuation, and hyphens (e.g. 'O-Yone,', 'Kichibei', 'Jizō').
    Strips markdown headers (#), blockquotes (>), bold/italic markers (*, _), and empty lines.
    """
    if not script_text:
        return []

    lines = script_text.splitlines()
    cleaned_tokens: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Skip pure markdown headers or separator lines
        if not stripped or stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("==="):
            continue

        # Strip leading blockquote or list bullets
        stripped = re.sub(r"^>\s*", "", stripped)
        stripped = re.sub(r"^[-*•]\s+", "", stripped)

        # Remove bold / italic symbols but preserve internal content
        stripped = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", stripped)
        stripped = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", stripped)
        stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        stripped = stripped.replace("`", "")

        tokens = stripped.split()
        for tok in tokens:
            t = tok.strip()
            # Skip standalone punctuation or stray bullets
            if t and not re.match(r"^[-*•_#]+$", t):
                cleaned_tokens.append(t)

    return cleaned_tokens


def align_whisper_to_script(
    whisper_words: list[dict[str, Any]],
    script_text: str,
) -> list[dict[str, Any]]:
    """Align Faster-Whisper word timestamps with authoritative script text.
    
    Args:
        whisper_words: List of dicts with keys 'word', 'start', 'end' from Faster-Whisper.
        script_text: The canonical script text (script_en.md).
        
    Returns:
        List of dicts with keys 'word', 'start', 'end' where 'word' comes 100% from script_text.
    """
    ref_tokens = clean_script_for_subtitles(script_text)
    if not ref_tokens:
        logger.warning("Empty reference script provided to align_whisper_to_script. Returning whisper words.")
        return whisper_words

    if not whisper_words:
        logger.warning("Empty whisper words provided to align_whisper_to_script. Synthesizing linear timings.")
        # Synthesize fallback timings (0.35s per word)
        t = 0.0
        fallback: list[dict[str, Any]] = []
        for w in ref_tokens:
            fallback.append({
                "word": w,
                "start": round(t, 3),
                "end": round(t + 0.35, 3),
            })
            t += 0.35
        return fallback

    ref_norm = [_normalize_token(w) for w in ref_tokens]
    whisper_norm = [_normalize_token(w.get("word", "")) for w in whisper_words]

    sm = difflib.SequenceMatcher(None, ref_norm, whisper_norm, autojunk=False)
    opcodes = sm.get_opcodes()

    aligned: list[dict[str, Any]] = []
    last_end = 0.0

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            # Direct 1-to-1 match: use script spelling + whisper timestamps
            for idx in range(i2 - i1):
                w_info = whisper_words[j1 + idx]
                st = round(float(w_info.get("start", last_end)), 3)
                en = round(float(w_info.get("end", st + 0.2)), 3)
                if st < last_end:
                    st = last_end
                if en <= st:
                    en = round(st + 0.1, 3)

                aligned.append({
                    "word": ref_tokens[i1 + idx],
                    "start": st,
                    "end": en,
                })
                last_end = en

        elif tag == "replace":
            # Whisper phonetic drift or split (e.g. 'Kichibei' -> ['kitchen', 'by'])
            ref_slice = ref_tokens[i1:i2]
            whisper_slice = whisper_words[j1:j2]

            t_start = round(float(whisper_slice[0].get("start", last_end)), 3)
            t_end = round(float(whisper_slice[-1].get("end", t_start + 0.3 * len(ref_slice))), 3)

            if t_start < last_end:
                t_start = last_end
            if t_end <= t_start:
                t_end = round(t_start + 0.25 * len(ref_slice), 3)

            total_dur = t_end - t_start
            n_words = len(ref_slice)

            if n_words == 1:
                aligned.append({
                    "word": ref_slice[0],
                    "start": t_start,
                    "end": t_end,
                })
                last_end = t_end
            else:
                # Proportional duration based on token character length
                weights = [max(len(_normalize_token(w)), 1) for w in ref_slice]
                total_weight = sum(weights) or n_words

                curr_t = t_start
                for idx, (w, wt) in enumerate(zip(ref_slice, weights)):
                    dur = total_dur * (wt / total_weight)
                    w_start = round(curr_t, 3)
                    w_end = round(curr_t + dur, 3) if idx < n_words - 1 else t_end
                    if w_end <= w_start:
                        w_end = round(w_start + 0.1, 3)

                    aligned.append({
                        "word": w,
                        "start": w_start,
                        "end": w_end,
                    })
                    curr_t += dur
                    last_end = w_end

        elif tag == "delete":
            # Reference words not detected by Whisper (interpolate brief interval)
            ref_slice = ref_tokens[i1:i2]
            next_start = whisper_words[j2].get("start") if j2 < len(whisper_words) else None

            t_start = last_end
            if next_start is not None and float(next_start) > t_start:
                available_dur = float(next_start) - t_start
                dur_per_word = max(available_dur / len(ref_slice), 0.15)
            else:
                dur_per_word = 0.25

            for w in ref_slice:
                w_start = round(t_start, 3)
                w_end = round(t_start + dur_per_word, 3)
                aligned.append({
                    "word": w,
                    "start": w_start,
                    "end": w_end,
                })
                t_start = w_end
                last_end = w_end

        elif tag == "insert":
            # Hallucinated syllables/words from Whisper not present in script.
            # Discard to preserve subtitle cleanliness.
            logger.debug(
                "Discarding %d whisper hallucinated token(s): %s",
                j2 - j1,
                [w.get("word") for w in whisper_words[j1:j2]],
            )

    return aligned
