"""Entity Binding and Visual Consistency Service for Red Sun Japan.

Deterministically binds character and environment reference assets to scene tasks,
injects standardized physical feature anchors into prompt descriptions,
and ensures strict asset typing (style vs character vs environment).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("redsun.entity_binder")

# Maximum reference images allowed by Gemini Assistant extension (1 style + up to 2 characters/environments)
MAX_SCENE_REFERENCES = 3

# Canonical entity aliases mapped to candidate asset IDs
CANONICAL_ENTITY_ALIASES: dict[str, list[str]] = {
    "character-shinzaburo": [
        r"\bshinzabur[oō]\b",
        r"\bshanojo\b",
        r"\bajwara\b",
        r"\bmerchant\b",
        r"\bprotagonist\b",
        r"\bsamurai\b",
        r"\bronin\b",
        r"\byoung man\b",
        r"\bmiddle-aged man\b",
        r"\bman of middle age\b",
        r"\bman's\b",
        r"\bman\b",
    ],
    "character-shanojo": [
        r"\bshanojo\b",
        r"\bshinzabur[oō]\b",
        r"\bajwara\b",
        r"\bmerchant\b",
        r"\bprotagonist\b",
        r"\bsamurai\b",
        r"\bronin\b",
        r"\bmiddle-aged man\b",
        r"\bman of middle age\b",
        r"\bman's\b",
        r"\bman\b",
    ],
    "character-otsuyu": [
        r"\bo-tsuyu\b",
        r"\btsuyu\b",
        r"\bfemale ghost\b",
        r"\bspectral woman\b",
        r"\bghostly woman\b",
        r"\blady o-tsuyu\b",
        r"\blady\b",
        r"\bdead woman\b",
        r"\bskelet(al|on)\b",
        r"\bethereal woman\b",
        r"\bbeautiful woman\b",
        r"\byoung woman\b",
    ],
    "character-yone": [
        r"\byone\b",
        r"\bmaid\b",
        r"\bmaidservant\b",
        r"\bservant woman\b",
        r"\bsturdy maid\b",
        r"\bcarrie(s|d) (a|the) (peony )?lantern\b",
        r"\bcarrying (a|the) (peony )?lantern\b",
    ],
    "character-tomozo": [
        r"\btomoz[oō]\b",
        r"\bservant\b",
        r"\bgardener\b",
    ],
    "character-omine": [
        r"\bomine\b",
        r"\btomoz[oō]'s wife\b",
        r"\bservant's wife\b",
    ],
    "character-priest": [
        r"\bpriest\b",
        r"\bmonk\b",
        r"\bry[oō]seki\b",
        r"\bjikaku\b",
        r"\bbuddhist\b",
        r"\babbot\b",
    ],
    "environment-temple": [
        r"\btemple\b",
        r"\bshin-banzuiin\b",
        r"\bsanctuary\b",
        r"\baltar\b",
        r"\bbuddhist temple\b",
        r"\btemple grounds\b",
        r"\bmonastery\b",
    ],
    "environment-yanaka-lane": [
        r"\byanaka\b",
        r"\blane\b",
        r"\balley\b",
        r"\bstreet\b",
        r"\bnarrow dirt lane\b",
        r"\bwood(en)? fence\b",
    ],
    "environment-lane": [
        r"\byanaka\b",
        r"\blane\b",
        r"\balley\b",
        r"\bstreet\b",
        r"\bnarrow dirt lane\b",
    ],
}

# Standardized physical feature anchors for characters and key environments
ENTITY_PHYSICAL_ANCHORS: dict[str, str] = {
    "character-shinzaburo": (
        "Shinzaburō (Shanojo), a respectable Japanese man in modest dark indigo merchant "
        "kosode robes, contemplative expression and restrained posture"
    ),
    "character-shanojo": (
        "Shanojo, a respectable Japanese man in modest dark indigo merchant "
        "kosode robes, contemplative expression and restrained posture"
    ),
    "character-otsuyu": (
        "the lady O-Tsuyu, an ethereal pale Edo-period young woman with long loose black hair, "
        "calm dark almond eyes, wearing a delicate cooling amber-orange summer yukata"
    ),
    "character-yone": (
        "Yone, a sturdy middle-aged Japanese maidservant with weathered tan skin and hair "
        "in a low bun, wearing a practical dark indigo cotton work kimono"
    ),
    "character-priest": (
        "Priest Ryōseki (Jikaku), an elderly Japanese Buddhist monk in his sixties with a "
        "shaved head and layered saffron and earth-brown robes"
    ),
    "character-tomozo": (
        "Tomozō, a lean sun-darkened Japanese servant man in patched brown work clothes"
    ),
    "character-omine": (
        "Omine, a sturdy Japanese woman with hair pinned in a modest bun, wearing a plain navy cotton kimono"
    ),
    "environment-temple": (
        "Shin-Banzuiin Buddhist temple grounds with weathered unpainted timber halls, curved tile roofs, "
        "and ancient mossy stone lanterns"
    ),
    "environment-yanaka-lane": (
        "narrow dirt lane between weathered Edo wooden townhouses under late humid dusk in Yanaka"
    ),
}

# Regex to detect human/character mentions in scene descriptions
CHARACTER_MENTION_PATTERN = re.compile(
    r"\b("
    r"shanojo|shinzabur[oō]|priest|monk|ry[oō]seki|jikaku|buddhist|"
    r"o-tsuyu|tsuyu|lady|yone|maid|maidservant|servant|tomozo|omine|"
    r"woman|man|samurai|cooper|kichai|merchant|abbot|corpse|figure|visitor"
    r")\b",
    re.IGNORECASE,
)


def infer_asset_type(asset_id: str, raw_type: Optional[str] = None) -> str:
    """Deterministically determine the asset type according to Gemini Assistant Schema v2.
    
    Guarantees:
    - 'style-master' or any ID containing 'style' is ALWAYS 'style'.
    - 'character-*' or character aliases are ALWAYS 'character'.
    - 'environment-*' or env aliases are ALWAYS 'environment'.
    """
    aid = asset_id.lower().strip()
    if aid == "style-master" or aid.startswith("style-") or "style" in aid:
        return "style"
    if aid.startswith("character-") or aid.startswith("char-") or "character" in aid:
        return "character"
    if aid.startswith("environment-") or aid.startswith("env-") or "environment" in aid or "temple" in aid or "lane" in aid:
        return "environment"
    if aid.startswith("object-") or "object" in aid:
        return "object"

    if raw_type and raw_type.lower() in {"character", "environment", "style", "object", "other"}:
        return raw_type.lower()

    return "other"


def match_entities_in_text(text: str, available_asset_keys: set[str]) -> list[str]:
    """Scan scene text and return matching asset IDs from available assets."""
    lower_text = text.lower()
    matched_ids: list[str] = []

    for asset_id, patterns in CANONICAL_ENTITY_ALIASES.items():
        # Check direct ID match or alias match against available assets
        target_asset_id = asset_id
        if target_asset_id not in available_asset_keys:
            # Check if there is an equivalent asset available (e.g. character-shanojo -> character-shinzaburo)
            if asset_id == "character-shanojo" and "character-shinzaburo" in available_asset_keys:
                target_asset_id = "character-shinzaburo"
            elif asset_id == "character-shinzaburo" and "character-shanojo" in available_asset_keys:
                target_asset_id = "character-shanojo"
            elif asset_id == "environment-lane" and "environment-yanaka-lane" in available_asset_keys:
                target_asset_id = "environment-yanaka-lane"
            else:
                continue

        if target_asset_id in matched_ids:
            continue

        for pat in patterns:
            if re.search(pat, lower_text, re.IGNORECASE):
                if target_asset_id not in matched_ids:
                    matched_ids.append(target_asset_id)
                break

    return matched_ids


def bind_entities_to_scene(
    prompt: str,
    title: str = "",
    narration: str = "",
    available_assets: Optional[dict[str, Any]] = None,
    current_refs: Optional[list[str]] = None,
    max_refs: int = MAX_SCENE_REFERENCES,
) -> list[str]:
    """Deterministically binds reference assets to a scene task.
    
    Rules:
    1. 'style-master' is ALWAYS the first reference if available.
    2. Identifies characters and environments from text (title + prompt + narration).
    3. Retains existing valid references, adds newly detected references.
    4. Limits total references to max_refs (3), prioritizing characters over environments.
    """
    if available_assets is None:
        available_assets = {}

    available_keys = set(available_assets.keys())
    combined_text = f"{title} {prompt} {narration}".strip()

    # Step 1: Detect matching entities
    detected = match_entities_in_text(combined_text, available_keys)

    # Step 2: Combine with current references
    existing = [r for r in (current_refs or []) if r in available_keys]
    combined_refs = list(existing)
    for d in detected:
        if d not in combined_refs and d in available_keys:
            combined_refs.append(d)

    # Step 3: Ensure style-master is first if present
    if "style-master" in available_keys:
        if "style-master" in combined_refs:
            combined_refs.remove("style-master")
        combined_refs.insert(0, "style-master")

    # Step 4: Separate into style, character, and other
    styles = [r for r in combined_refs if infer_asset_type(r, getattr(available_assets.get(r), "type", None)) == "style"]
    characters = [r for r in combined_refs if infer_asset_type(r, getattr(available_assets.get(r), "type", None)) == "character"]
    others = [r for r in combined_refs if r not in styles and r not in characters]

    # Assemble final list respecting max_refs
    final_refs: list[str] = []
    # Add primary style reference
    if styles:
        final_refs.append(styles[0])
    
    # Add characters up to available slots
    for c in characters:
        if len(final_refs) < max_refs:
            final_refs.append(c)

    # Add environment/other references if slots remain
    for o in others:
        if len(final_refs) < max_refs:
            final_refs.append(o)

    # Ensure at least style-master if available
    if not final_refs and "style-master" in available_keys:
        final_refs = ["style-master"]

    return final_refs


def _replace_anchor_name(text: str, name_regex: str, anchor_desc: str) -> str:
    """Replaces character name occurrences, properly transforming possessives into natural syntax."""
    # 1. Possessive with following noun: Name's <word> -> the <word> of <anchor_desc>
    pos_pat = re.compile(rf"\b({name_regex})('s|’s)\s+([a-zA-Z]+)\b", re.IGNORECASE)
    if pos_pat.search(text):
        return pos_pat.sub(rf"the \3 of {anchor_desc}", text, count=1)
    # 2. Standalone possessive: Name's -> of <anchor_desc>
    pos_general = re.compile(rf"\b({name_regex})('s|’s)\b", re.IGNORECASE)
    if pos_general.search(text):
        return pos_general.sub(rf"the presence of {anchor_desc}", text, count=1)
    # 3. Direct name match
    direct_pat = re.compile(rf"\b({name_regex})\b", re.IGNORECASE)
    if direct_pat.search(text):
        return direct_pat.sub(anchor_desc, text, count=1)
    return text


def inject_physical_anchors(narrative_core: str, bound_refs: list[str]) -> str:
    """Injects standardized physical feature anchors when characters appear in the scene."""
    result = narrative_core

    for ref in bound_refs:
        anchor_desc = ENTITY_PHYSICAL_ANCHORS.get(ref)
        if not anchor_desc:
            continue

        # Extract primary name/entity identifier
        if ref in ("character-shinzaburo", "character-shanojo"):
            if not re.search(r"wearing\s+simple\s+dark|merchant\s+kosode|slender\s+Japanese\s+man|respectable\s+Japanese\s+man", result, re.IGNORECASE):
                result = _replace_anchor_name(result, r"Shanojo|Shinzabur[oō]|Ajwara Shanojo", anchor_desc)
                if anchor_desc not in result:
                    # If characters are bound but name was implied (e.g. 'the samurai', 'the man')
                    man_pat = re.compile(r"\b(the samurai|the protagonist|the man)\b", re.IGNORECASE)
                    if man_pat.search(result):
                        result = man_pat.sub(anchor_desc, result, count=1)

        elif ref == "character-priest":
            if not re.search(r"shaved\s+head|saffron\s+and\s+(brown|earth)", result, re.IGNORECASE):
                result = _replace_anchor_name(result, r"Priest Jikaku|Priest Ry[oō]seki|the priest|the monk", anchor_desc)

        elif ref == "character-otsuyu":
            if not re.search(r"ethereal\s+pale|cooling\s+amber|translucen(ce|t)", result, re.IGNORECASE):
                result = _replace_anchor_name(result, r"the lady O-Tsuyu|lady O-Tsuyu|O-Tsuyu", anchor_desc)

        elif ref == "character-yone":
            if not re.search(r"weathered\s+tan|low\s+bun|dark\s+indigo\s+cotton\s+work", result, re.IGNORECASE):
                result = _replace_anchor_name(result, r"the maid Yone|the maid|maid Yone|maidservant", anchor_desc)

    return result
