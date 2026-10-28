"""Visual Prompt Linter and Quality Auditor for Red Sun Japan.

Enforces cross-scene uniqueness, narrative specificity, visual progression,
and bans generic/meta prompt templates.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Optional

from redsun.services.style_constants import clean_narrative_text


class VisualPromptLinterError(Exception):
    """Base exception for visual prompt linter failures."""
    pass


class DuplicateSceneError(VisualPromptLinterError):
    """Raised when two or more scenes are too textually similar."""
    pass


class GenericPhraseError(VisualPromptLinterError):
    """Raised when a prompt contains blacklisted generic phrases."""
    pass


class CompositionVarietyError(VisualPromptLinterError):
    """Raised when the sequence lacks sufficient camera/framing diversity."""
    pass


class MissingCharacterReferenceError(VisualPromptLinterError):
    """Raised when a scene mentions a character but has no character asset attached."""
    pass


class TruncatedPromptError(VisualPromptLinterError):
    """Raised when a prompt ends with a cut-off word or fragment."""
    pass


class MetadataPollutionError(VisualPromptLinterError):
    """Raised when a prompt contains project titles, episode names, or metadata tokens."""
    pass


# Blacklisted phrases and regex patterns that signify lazy template generation or narration leaks
BLACKLISTED_PATTERNS = [
    re.compile(r"cinematic\s+historical\s+frame\s+\d+", re.IGNORECASE),
    re.compile(r"detailed\s+edo\s+period\s+scene\s+under\s+low\s+atmospheric", re.IGNORECASE),
    re.compile(r"frame\s+\d+\s+for", re.IGNORECASE),
    re.compile(r"frame\s+showing\b", re.IGNORECASE),
    re.compile(r"^detailed\s+scene\s+of\b", re.IGNORECASE),
    re.compile(r"^a\s+scene\s+depicting\b", re.IGNORECASE),
    re.compile(r"tense\s+close-up\s+detail\s+shot\s+focusing\s+on\s+specific\s+objects\s+and\s+reactions:", re.IGNORECASE),
    re.compile(r"distinct\s+historical\s+scene\s+showing:", re.IGNORECASE),
    re.compile(r"the\s+daughter\s+of\s+a\s+minor\s+official\s+who\s+had\s+been\s+stationed", re.IGNORECASE),
    re.compile(r"always\s+arrived\s+at\s+the\s+same\s+hour,\s+always\s+accompanied\s+by\s+the\s+maid", re.IGNORECASE),
    re.compile(r"no\s+intervalo\s+de\s+tempo\s+\d+", re.IGNORECASE),
    re.compile(r"\bfor\s+[\w\s—\-]+Master\s+References?", re.IGNORECASE),
    re.compile(r"within\s+an\s+Edo-period\s+Japanese\s+setting\s+for\b", re.IGNORECASE),
    re.compile(r"^Establishing\s+master\s+style\s+anchor\s+for\b", re.IGNORECASE),
    re.compile(r"—\s*Master References?", re.IGNORECASE),
]

# Keywords indicating close-up / extreme detail framing
DETAIL_KEYWORDS = {
    "close-up",
    "close up",
    "extreme close-up",
    "extreme close up",
    "macro shot",
    "macro detail",
    "tight shot",
    "detail shot",
    "tight close-up",
    "macro",
    "hands clasped",
    "trembling hands",
    "trembling hand",
    "calloused hands",
    "close shot of",
    "face close-up",
    "facial expression",
    "eyes wide",
    "focus on the talisman",
    "focus on the bell",
    "focus on the flame",
    "focus on the petal",
    "focus on the feet",
    "barefoot",
    "close view of",
    "tight framing on",
    "intricate detail of",
}

# Terms signifying a documentary provenance artifact (Archival Bridge)
ARCHIVAL_ARTIFACT_TERMS = {
    "manuscript",
    "scroll",
    "book",
    "pages",
    "calligraphy",
    "ink",
    "archive",
    "newspaper",
    "dossier",
    "document",
    "desk",
}

ARCHIVAL_ARTIFACT_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in sorted(ARCHIVAL_ARTIFACT_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


class VisualPromptLinter:
    """Linter for validating prompt uniqueness, variety, and narrative fidelity."""

    def __init__(
        self,
        similarity_threshold: float = 0.50,
        min_detail_ratio: float = 0.30,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.min_detail_ratio = min_detail_ratio

    @staticmethod
    def extract_narrative_description(prompt: str) -> str:
        """Extract only the narrative visual description, stripping the fixed Style Bible."""
        cleaned = clean_narrative_text(prompt)
        # Remove trailing period if present
        return cleaned.rstrip(". ").strip()

    def check_generic_phrases(self, scenes: list[dict[str, Any]]) -> list[str]:
        """Detect any blacklisted meta-phrases in scenes."""
        errors: list[str] = []
        for i, sc in enumerate(scenes, 1):
            raw = sc.get("prompt") or sc.get("description") or ""
            narrative = self.extract_narrative_description(raw)
            for pattern in BLACKLISTED_PATTERNS:
                match = pattern.search(narrative)
                if match:
                    errors.append(
                        f"GenericPhraseError: Scene {i} contains blacklisted phrase: '{match.group()}'"
                    )
                    break
        return errors

    def check_duplicate_similarity(
        self, scenes: list[dict[str, Any]]
    ) -> list[str]:
        """Check pairwise similarity between all scene descriptions."""
        errors: list[str] = []
        narratives = [
            self.extract_narrative_description(sc.get("prompt") or sc.get("description") or "")
            for sc in scenes
        ]

        n = len(narratives)
        for i in range(n):
            for j in range(i + 1, n):
                s1 = narratives[i]
                s2 = narratives[j]
                if not s1 or not s2:
                    continue

                # Use SequenceMatcher for character/token sequence alignment
                ratio = difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()
                if ratio >= self.similarity_threshold:
                    errors.append(
                        f"DuplicateSceneError: Scene {i+1} and Scene {j+1} are too similar ({ratio:.1%} match). "
                        f"Both describe nearly identical content: '{s1[:80]}...' vs '{s2[:80]}...'"
                    )
        return errors

    def check_visual_progression(
        self, scenes: list[dict[str, Any]]
    ) -> list[str]:
        """Ensure at least min_detail_ratio of scenes are close-up / detail shots."""
        if not scenes:
            return []

        detail_count = 0
        total = len(scenes)

        for i, sc in enumerate(scenes, 1):
            text = (sc.get("prompt") or sc.get("description") or "").lower()
            scale = str(sc.get("scale") or "").lower()

            is_detail = any(k in scale for k in ("close", "detail", "macro", "tight"))
            if not is_detail:
                is_detail = any(k in text for k in DETAIL_KEYWORDS)

            if is_detail:
                detail_count += 1

        ratio = detail_count / total
        errors: list[str] = []
        if ratio < self.min_detail_ratio:
            errors.append(
                f"CompositionVarietyError: Only {detail_count}/{total} scenes ({ratio:.1%}) are close-up/detail shots. "
                f"At least {self.min_detail_ratio:.0%} required to avoid flat, repetitive wide shots."
            )
        return errors

    def check_character_references(
        self, scenes: list[dict[str, Any]], asset_catalog: Optional[dict[str, Any]] = None
    ) -> list[str]:
        """Ensure scenes mentioning characters have at least one character asset attached."""
        from redsun.services.entity_binder import CHARACTER_MENTION_PATTERN

        errors: list[str] = []
        for i, sc in enumerate(scenes, 1):
            if "references" not in sc:
                continue

            prompt_text = sc.get("prompt") or sc.get("description") or ""
            title_text = sc.get("title") or ""
            combined = f"{title_text} {prompt_text}"

            if CHARACTER_MENTION_PATTERN.search(combined):
                refs = sc.get("references") or []
                has_char = False
                for r in refs:
                    r_str = str(r).lower()
                    if r_str.startswith("character-") or r_str.startswith("char-"):
                        has_char = True
                        break
                    if asset_catalog and r in asset_catalog:
                        ainfo = asset_catalog[r]
                        atype = getattr(ainfo, "type", None) or (
                            ainfo.get("type") if isinstance(ainfo, dict) else None
                        )
                        if atype == "character":
                            has_char = True
                            break

                if not has_char:
                    errors.append(
                        f"MissingCharacterReferenceError: Scene {i} mentions a character but has no character asset attached."
                    )
        return errors

    def check_prompt_completeness(self, scenes: list[dict[str, Any]]) -> list[str]:
        """Ensure prompts do not end abruptly with cut-off words, fragments, or missing punctuation."""
        errors: list[str] = []
        truncated_pattern = re.compile(
            r"(\s+[a-zA-Z]{1,2}\.|\b(and|or|but|of|with|that|which|because|if|when|as|the|at|by|for|to)\.)$",
            re.IGNORECASE,
        )
        for i, sc in enumerate(scenes, 1):
            raw = sc.get("prompt") or sc.get("description") or ""
            # Isolate the narrative core before aspect ratio or style bible
            first_block = raw.split("\n\n")[0].strip()
            if "Cinematic semi-realistic" in first_block:
                first_block = first_block.split("Cinematic semi-realistic")[0].strip()

            if not first_block:
                continue

            # Check if ends abruptly without terminal punctuation
            if not first_block.endswith((".", "!", "?", '"', "'")):
                errors.append(
                    f"TruncatedPromptError: Scene {i} ends abruptly without terminal punctuation: '{first_block[-40:]}'"
                )
                continue

            # Check if ends with a sliced fragment (e.g. 'st.', 'o.', 'T.')
            match = truncated_pattern.search(first_block)
            if match:
                errors.append(
                    f"TruncatedPromptError: Scene {i} ends with truncated fragment '{match.group().strip()}': '{first_block[-50:]}'"
                )
        return errors

    def check_metadata_pollution(
        self, scenes: list[dict[str, Any]], project_title: str = ""
    ) -> list[str]:
        """Detect any project title, slug, or administrative contamination in scene prompts."""
        errors: list[str] = []
        clean_title = (project_title or "").strip()
        segments: list[str] = []
        if clean_title:
            segments.append(clean_title)
            for seg in re.split(r"[-—:–()]+", clean_title):
                s = seg.strip()
                if len(s) >= 4:
                    segments.append(s)

        for i, sc in enumerate(scenes, 1):
            raw = sc.get("prompt") or sc.get("description") or ""
            narrative = self.extract_narrative_description(raw)

            # Check for generic references leakage
            if re.search(r"—\s*Master References?", narrative, flags=re.IGNORECASE):
                errors.append(
                    f"MetadataPollutionError: Scene {i} contains administrative reference tag: 'Master References'"
                )
                continue

            for seg in segments:
                if re.search(rf"\bfor\s+\(?{re.escape(seg)}\)?", narrative, flags=re.IGNORECASE):
                    errors.append(
                        f"MetadataPollutionError: Scene {i} contains project title pollution: 'for {seg}'"
                    )
                    break
                elif f"({seg})" in narrative:
                    errors.append(
                        f"MetadataPollutionError: Scene {i} contains parenthesized project title: '({seg})'"
                    )
                    break
        return errors

    def check_archival_bridge(self, scenes: list[dict[str, Any]]) -> list[str]:
        """Soft verification ensuring scenes 3 to 8 (window 00:30 to 01:30) contain an archival artifact scene."""
        if len(scenes) < 3:
            return []

        # Scenes 3 to 8 (1-indexed) corresponds to 0-indexed [2:min(len(scenes), 8)]
        window_scenes = scenes[2 : min(len(scenes), 8)]
        for sc in window_scenes:
            text = (sc.get("prompt") or sc.get("description") or "").lower()
            if ARCHIVAL_ARTIFACT_PATTERN.search(text):
                return []

        return [
            "ArchivalBridgeWarning: No documentary provenance artifact scene found between scenes 3 and 8 "
            "(window 00:30 to 01:30). Expected a close-up of manuscript, scroll, book, pages, calligraphy, "
            "ink, archive, newspaper, dossier, document, or desk."
        ]

    def auto_fix_archival_bridge(
        self,
        scenes: list[dict[str, Any]],
        target_scene_idx: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Automatically injects the archival provenance shot into the designated scene if missing between scenes 3 and 8."""
        if len(scenes) < 3:
            return scenes

        window_scenes = scenes[2 : min(len(scenes), 8)]
        for sc in window_scenes:
            text = (sc.get("prompt") or sc.get("description") or "").lower()
            if ARCHIVAL_ARTIFACT_PATTERN.search(text):
                return scenes

        inject_idx = (
            target_scene_idx
            if (target_scene_idx is not None and 2 <= target_scene_idx < min(len(scenes), 8))
            else min(3, len(scenes) - 1)
        )

        target = scenes[inject_idx]
        target["scale"] = "Close-up"
        target["description"] = (
            "Close-up macro shot of an aged Japanese bound manuscript with brushed sumi-ink calligraphy "
            "resting open on a weathered dark wooden desk under the warm flickering amber glow of an oil lamp, "
            "deep shadows surrounding."
        )
        if "title" in target:
            target["title"] = "Archival Provenance Manuscript"
        if "prompt" in target:
            target["prompt"] = target["description"]
        return scenes

    def validate(
        self,
        scenes: list[dict[str, Any]],
        raise_on_error: bool = False,
        asset_catalog: Optional[dict[str, Any]] = None,
        project_title: str = "",
        auto_fix_archival: bool = True,
    ) -> tuple[bool, list[str]]:
        """Validate a full set of scenes. Return (is_valid, errors) or raise exception."""
        errors: list[str] = []
        errors.extend(self.check_prompt_completeness(scenes))
        errors.extend(self.check_generic_phrases(scenes))
        errors.extend(self.check_metadata_pollution(scenes, project_title=project_title))
        errors.extend(self.check_duplicate_similarity(scenes))
        errors.extend(self.check_visual_progression(scenes))
        errors.extend(self.check_character_references(scenes, asset_catalog))

        if auto_fix_archival:
            self.auto_fix_archival_bridge(scenes)
        else:
            errors.extend(self.check_archival_bridge(scenes))

        if errors and raise_on_error:
            # Raise the most critical error first
            for err in errors:
                if "MetadataPollutionError" in err:
                    raise MetadataPollutionError(err)
                if "TruncatedPromptError" in err:
                    raise TruncatedPromptError(err)
                if "MissingCharacterReferenceError" in err:
                    raise MissingCharacterReferenceError(err)
                if "DuplicateSceneError" in err:
                    raise DuplicateSceneError(err)
                if "GenericPhraseError" in err:
                    raise GenericPhraseError(err)
                if "CompositionVarietyError" in err:
                    raise CompositionVarietyError(err)
            raise VisualPromptLinterError(errors[0])

        return (len(errors) == 0, errors)


