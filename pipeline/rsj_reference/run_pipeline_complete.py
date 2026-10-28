#!/usr/bin/env python3
"""Red Sun Japan — Standalone Pipeline Runner.

Runs the full video production pipeline via the FastAPI REST API.
Works independently of Hermes Agent's conversation state — all state
is persisted on filesystem via checkpoint files.

Usage:
    python run_pipeline_complete.py --slug mujina --title "Mujina"
    python run_pipeline_complete.py --slug yuki_onna --title "Yuki Onna" --resume
    python run_pipeline_complete.py --slug kappa --title "Kappa" --skip-phases audio,transcribe

Phases (executed in order):
    1. check_server        — Verify FastAPI server is reachable
    2. create_project      — POST /projects (idempotent if slug exists)
    3. generate_script     — POST /projects/{id}/script
    4. review_script       — POST /projects/{id}/review
    5. generate_image_prompts — POST /projects/{id}/image-prompts
    6. generate_audio      — POST /projects/{id}/audio
    7. transcribe          — POST /projects/{id}/transcribe
    8. render_manifest     — POST /projects/{id}/render-manifest
    9. done                — Summary and next steps

NOTE 2026-09-15:
    - ComfyUI removed from pipeline (was deprecated since 2026-09-13). Images
      are now generated via Chrome extension (gemini-assistant), operated by
      Isamu manually. The pipeline PAUSES at gate_image_handoff for the handoff.
    - phase_check_image_method: prints PIPELINE_STATE.md pointer, verifies the
      gemini-assistant workspace exists.
    - gate_image_handoff replaces phase_generate_images — pipeline PAUSES and
      waits for Isamu to drop images into projects/<slug>/images/ manually.
    - See ../../PIPELINE_STATE.md (single source of truth) for full pipeline state.

Each phase writes a checkpoint file to:
    storage/<slug>/checkpoints/<phase_name>.done

On re-launch with --resume, completed phases are skipped automatically.

Designed to work with ANY theme (yokai, kaidan, urban legends, etc.)
and ANY LLM model behind the API (DeepSeek, Step, MiniMax, etc.).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger("redsun.pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PHASES = [
    "check_server",
    "check_image_method",   # DEPRECATED_OLD: was check_comfyui
    "discover_themes",
    "create_project",
    "generate_script",
    "review_script",
    "generate_blog",
    "generate_shorts",
    "generate_image_prompts",
    "gate_image_handoff",   # HUMAN GATE — pauses pipeline until images dropped
    "generate_audio",
    "transcribe",
    "render_manifest",
    "render_video",
    "concat_final",
    "generate_post_kit",
    "organize_output",
]

STORAGE_ROOT = Path(os.environ.get(
    "REDSUN_STORAGE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
))

# Organized output directory — each project gets a clean folder with
# video/, shorts/, text/, blog/, images/, data/.
# Lives at channel-redsunjapan/projects/ (NOT inside motor-python/storage/).
PROJECTS_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent / "projects"

# Channel root (parent of motor-python/). Used for finding PIPELINE_STATE.md
# and as the canonical anchor for the verify_pipeline.py script.
ROOT = PROJECTS_ROOT.parent


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint system
# ─────────────────────────────────────────────────────────────────────────────

def _checkpoint_dir(slug: str) -> Path:
    """Return the checkpoint directory for a given slug."""
    return STORAGE_ROOT / slug / "checkpoints"


def _is_phase_done(slug: str, phase: str) -> bool:
    """Check if a phase has been completed (checkpoint exists)."""
    cp = _checkpoint_dir(slug) / f"{phase}.done"
    return cp.exists()


def _mark_phase_done(slug: str, phase: str, data: dict | None = None) -> None:
    """Write a checkpoint file marking a phase as complete."""
    cp_dir = _checkpoint_dir(slug)
    cp_dir.mkdir(parents=True, exist_ok=True)
    cp_path = cp_dir / f"{phase}.done"
    payload = {
        "phase": phase,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }
    cp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("✓ checkpoint: %s", cp_path)


def _load_checkpoint(slug: str, phase: str) -> dict | None:
    """Load checkpoint data for a phase. Returns None if not found."""
    cp = _checkpoint_dir(slug) / f"{phase}.done"
    if not cp.exists():
        return None
    try:
        return json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        return {"phase": phase, "completed_at": "unknown", "data": {}}


# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _api_base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _api_get(path: str, port: int, timeout: int = 30) -> dict:
    """GET request to the FastAPI server."""
    url = f"{_api_base(port)}{path}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _api_post(path: str, payload: dict, port: int, timeout: int = 300) -> dict:
    """POST request to the FastAPI server."""
    url = f"{_api_base(port)}{path}"
    logger.info("POST %s payload=%s", path, json.dumps(payload, ensure_ascii=False))
    resp = requests.post(url, json=payload, timeout=timeout)
    logger.info("POST %s status=%s body=%s", path, resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()


def _find_project_by_slug(slug: str, port: int) -> dict | None:
    """Find an existing project by slug."""
    try:
        projects = _api_get("/projects", port)
        for p in projects:
            if p.get("slug") == slug:
                return p
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Theme catalog for discover_themes phase
# ─────────────────────────────────────────────────────────────────────────────

THEMES_CATALOG = [
    # ── YOKAI CLÁSSICOS ──────────────────────────────────────────────
    {
        "slug": "yuki_onna",
        "name": "Yuki Onna",
        "name_ja": "雪女",
        "category": "yokai",
        "description": "The Snow Woman — a beautiful spirit who appears during snowstorms, luring travelers to their frozen death. Classical kaidan from the Tohoku region.",
        "source_type": "classical_kaidan",
        "atmosphere": "lantern-light-and-fog",
        "visual_keywords": ["snow", "white kimono", "pale woman", "blizzard", "cedar forest", "frozen"],
    },
    {
        "slug": "mujina",
        "name": "Mujina",
        "name_ja": "貉",
        "category": "yokai",
        "description": "A shape-shifting badger spirit that mimics humans and haunts mountain paths at night. Known from Lafcadio Hearn's Kwaidan collection.",
        "source_type": "regional_yokai",
        "atmosphere": "misty-forest-path",
        "visual_keywords": ["badger", "forest", "moonlight", "shapeshifter", "mountain path"],
    },
    {
        "slug": "kappa",
        "name": "Kappa",
        "name_ja": "河童",
        "category": "yokai",
        "description": "Water-dwelling creatures with turtle shells and a water-filled dish on their heads. Known for dragging children into rivers.",
        "source_type": "regional_yokai",
        "atmosphere": "murky-river-mist",
        "visual_keywords": ["river", "green creature", "turtle shell", "water", "reeds", "swamp"],
    },
    {
        "slug": "tengu",
        "name": "Tengu",
        "name_ja": "天狗",
        "category": "yokai",
        "description": "Mountain-dwelling spirits with long noses and red faces. Skilled martial artists and tricksters who guard the mountains.",
        "source_type": "classical_kaidan",
        "atmosphere": "mountain-shrine-darkness",
        "visual_keywords": ["mountain", "red face", "long nose", "wings", "shrine", "forest canopy"],
    },
    {
        "slug": "oni",
        "name": "Oni",
        "name_ja": "鬼",
        "category": "yokai",
        "description": "Demon-like ogres with horns and fangs. Associated with punishment in the afterlife and destruction.",
        "source_type": "classical_kaidan",
        "atmosphere": "volcanic-hell-glow",
        "visual_keywords": ["demon", "horns", "fangs", "red skin", "club", "fire", "gate"],
    },
    {
        "slug": "jorogumo",
        "name": "Jorogumo",
        "name_ja": "女郎蜘蛛",
        "category": "yokai",
        "description": "A spider spirit that transforms into a beautiful woman to lure and trap men in her web.",
        "source_type": "classical_kaidan",
        "atmosphere": "candlelit-labyrinth",
        "visual_keywords": ["spider web", "beautiful woman", "dark room", "silk threads", "candles"],
    },
    {
        "slug": "rokurokubi",
        "name": "Rokurokubi",
        "name_ja": "轆轤首",
        "category": "yokai",
        "description": "Women whose necks stretch to impossible lengths at night, terrorizing villages while they sleep.",
        "source_type": "classical_kaidan",
        "atmosphere": "moonlit-village",
        "visual_keywords": ["stretching neck", "woman", "night", "village", "paper lantern", "wooden house"],
    },
    {
        "slug": "nure_onna",
        "name": "Nure-onna",
        "name_ja": "濡女",
        "category": "yokai",
        "description": "A serpentine woman with a snake's body and wet black hair who lurks near rivers and shores.",
        "source_type": "regional_yokai",
        "atmosphere": "stormy-coastline",
        "visual_keywords": ["snake body", "wet hair", "river", "coast", "rain", "dark water"],
    },
    {
        "slug": "gashadokuro",
        "name": "Gashadokuro",
        "name_ja": "がしゃどくろ",
        "category": "yokai",
        "description": "A giant skeleton formed from the bones of people who died of famine. Wanders at night, crushing travelers in its grip.",
        "source_type": "classical_kaidan",
        "atmosphere": "bone-field-moonlight",
        "visual_keywords": ["giant skeleton", "bones", "moonlight", "famine", "crushing hands", "desolate road"],
    },
    {
        "slug": "tsuchinoko",
        "name": "Tsuchinoko",
        "name_ja": "槌の子",
        "category": "yokai",
        "description": "A fat, snake-like cryptid that can jump great distances and speaks human language. Modern sightings continue across Japan.",
        "source_type": "regional_yokai",
        "atmosphere": "dense-forest-ambush",
        "visual_keywords": ["fat snake", "forest floor", "jumping creature", "mysterious tracks", "deep woods"],
    },
    {
        "slug": "noppera_bou",
        "name": "Noppera-bō",
        "name_ja": "のっぺらぼう",
        "category": "yokai",
        "description": "A faceless ghost that appears as a normal person, then reveals its blank, featureless face to terrify witnesses.",
        "source_type": "classical_kaidan",
        "atmosphere": "empty-road-dread",
        "visual_keywords": ["faceless figure", "blank face", "dark road", "paper lantern", "terror"],
    },
    {
        "slug": "wanyudo",
        "name": "Wanyūdō",
        "name_ja": "輪入道",
        "category": "yokai",
        "description": "A burning wheel with a tormented human face inside. Rolls through streets at night, stealing the souls of those who see it.",
        "source_type": "classical_kaidan",
        "atmosphere": "burning-wheel-darkness",
        "visual_keywords": ["burning wheel", "human face", "fire", "dark street", "soul stealing", "night"],
    },
    {
        "slug": "ubume",
        "name": "Ubume",
        "name_ja": "産女",
        "category": "yokai",
        "description": "The ghost of a woman who died in childbirth. Appears holding a baby, asking passersby to hold it — the bundle grows heavier until it crushes the victim.",
        "source_type": "classical_kaidan",
        "atmosphere": "cemetery-fog",
        "visual_keywords": ["ghost woman", "baby bundle", "cemetery", "fog", "heavy burden", "night rain"],
    },

    # ── KAIDAN (CONTOS DE FANTASMA) ──────────────────────────────────
    {
        "slug": "hoichi_the_earless",
        "name": "Hoichi the Earless",
        "name_ja": "耳なし芳一",
        "category": "kaidan",
        "description": "A blind biwa player whose music summoned the ghosts of the Heike clan. Monks painted sacred text on his body, but forgot his ears.",
        "source_type": "classical_kaidan",
        "atmosphere": "temple-darkness",
        "visual_keywords": ["blind musician", "biwa", "ghostly audience", "temple", "sacred text", "blood"],
    },
    {
        "slug": "botan_doro",
        "name": "Botan Dōrō",
        "name_ja": "牡丹燈籠",
        "category": "kaidan",
        "description": "A samurai falls in love with a beautiful woman who visits him each night with a peony lantern. She is revealed to be a corpse.",
        "source_type": "classical_kaidan",
        "atmosphere": "peony-lantern-glow",
        "visual_keywords": ["peony lantern", "beautiful woman", "samurai", "graveyard", "corpse", "candlelight"],
    },
    {
        "slug": "yotsuya_kaidan",
        "name": "Yotsuya Kaidan",
        "name_ja": "四谷怪談",
        "category": "kaidan",
        "description": "The most famous Japanese ghost story. Oiwa's husband poisons her; her disfigured ghost returns for revenge.",
        "source_type": "classical_kaidan",
        "atmosphere": "vengeful-poison",
        "visual_keywords": ["disfigured woman", "poison", "revenge", "samurai house", "ghost", "mirror"],
    },
    {
        "slug": "bancho_sarayashiki",
        "name": "Banchō Sarayashiki",
        "name_ja": "番町皿屋敷",
        "category": "kaidan",
        "description": "A servant girl is killed for breaking a precious plate. Her ghost rises from a well, counting plates endlessly.",
        "source_type": "classical_kaidan",
        "atmosphere": "well-counting-plates",
        "visual_keywords": ["well", "counting plates", "ghost girl", "servant", "broken plate", "moonlit garden"],
    },
    {
        "slug": "mimi_nashi_hoichi",
        "name": "The Story of Mimi-nashi-Hōichi",
        "name_ja": "耳なし芳一の物語",
        "category": "kaidan",
        "description": "Hearn's retelling of Hoichi's encounter with the phantom Heike warriors at Akama shrine. A tale of music, ghosts, and sacred protection.",
        "source_type": "classical_kaidan",
        "atmosphere": "seaside-shrine-haunting",
        "visual_keywords": ["seaside shrine", "biwa music", "ghost warriors", "sacred sutras", "blood", "blind man"],
    },

    # ── LENDAS URBANAS SHŌWA ─────────────────────────────────────────
    {
        "slug": "kuchisake_onna",
        "name": "Kuchisake-onna",
        "name_ja": "口裂け女",
        "category": "showa_legend",
        "description": "The Slit-Mouthed Woman — a modern urban legend of a spirit who asks if she's beautiful before revealing her mutilated face.",
        "source_type": "showa_legend",
        "atmosphere": "urban-neon-rain",
        "visual_keywords": ["surgical mask", "woman", "scissors", "dark alley", "rain", "street"],
    },
    {
        "slug": "teke_teke",
        "name": "Teke Teke",
        "name_ja": "テケテケ",
        "category": "showa_legend",
        "description": "The torso ghost of a woman who was cut in half by a train. She drags herself on her elbows, making a 'teke teke' sound.",
        "source_type": "showa_legend",
        "atmosphere": "train-station-dread",
        "visual_keywords": ["torso", "train tracks", "station", "night", "elbows", "dark platform"],
    },
    {
        "slug": "hanako_san",
        "name": "Hanako-san",
        "name_ja": "花子さん",
        "category": "showa_legend",
        "description": "The ghost of a girl who haunts the third stall of school bathrooms. Knock three times and she appears.",
        "source_type": "showa_legend",
        "atmosphere": "school-bathroom-terror",
        "visual_keywords": ["school bathroom", "third stall", "ghost girl", "knock", "fluorescent light", "empty hallway"],
    },
    {
        "slug": "hachishakusama",
        "name": "Hachishakusama",
        "name_ja": "八尺様",
        "category": "showa_legend",
        "description": "An 8-foot tall woman in a white dress who abducts children. Her distinctive 'po-po-po' laugh echoes before she appears.",
        "source_type": "showa_legend",
        "atmosphere": "rural-pastoral-horror",
        "visual_keywords": ["tall woman", "white dress", "straw hat", "rice paddies", "child abduction", "po-po-po"],
    },
    {
        "slug": "aokigahara",
        "name": "Aokigahara",
        "name_ja": "青木ヶ原",
        "category": "showa_legend",
        "description": "The Sea of Trees at the base of Mount Fuji — one of the world's most notorious suicide forests. Compasses malfunction, spirits wander.",
        "source_type": "mystery",
        "atmosphere": "dense-forest-silence",
        "visual_keywords": ["dense forest", "twisted trees", "compass", "silence", "ropes", "fog", "mount fuji"],
    },
    {
        "slug": "kashima_reiko",
        "name": "Kashima Reiko",
        "name_ja": "カシマレイコ",
        "category": "showa_legend",
        "description": "A ghost who lost her legs in a train accident. She appears asking 'Where are my legs?' and will take yours if you answer wrong.",
        "source_type": "showa_legend",
        "atmosphere": "hospital-corridor",
        "visual_keywords": ["hospital corridor", "legless ghost", "train accident", "question", "midnight", "fluorescent light"],
    },

    # ── INCIDENTES HISTÓRICOS MISTERIOSOS ────────────────────────────
    {
        "slug": "himuro_murder",
        "name": "The Himuro Mansion Murders",
        "name_ja": "冰室邸事件",
        "category": "mystery",
        "description": "Japan's most famous haunted house. An entire family was found dead in a ritualistic massacre. The mansion remains sealed.",
        "source_type": "mystery",
        "atmosphere": "abandoned-mansion",
        "visual_keywords": ["abandoned mansion", "blood stains", "ritual", "sealed doors", "overgrown garden", "night"],
    },
    {
        "slug": "okiku_dolls",
        "name": "The Okiku Doll",
        "name_ja": "置き人形",
        "category": "mystery",
        "description": "A doll in Mannenji Temple whose hair reportedly grows. Named after a girl who died of fever in 1918.",
        "source_type": "mystery",
        "atmosphere": "temple-shrine",
        "visual_keywords": ["Japanese doll", "growing hair", "temple", "glass case", "offering", "child ghost"],
    },
    {
        "slug": "ise_shrines_rebuilding",
        "name": "The Secret of Ise Grand Shrine",
        "name_ja": "伊勢神宮の謎",
        "category": "mystery",
        "description": "Rebuilt every 20 years for 1,300 years. No outsider has ever seen the inner sanctuary. What is being hidden?",
        "source_type": "mystery",
        "atmosphere": "sacred-hidden",
        "visual_keywords": ["shrine", "wooden pillars", "forest", "sacred rope", "hidden chamber", "ceremony"],
    },
    {
        "slug": "byodo_in_phantom",
        "name": "The Phantom of Byōdō-in",
        "name_ja": "平等院の幽霊",
        "category": "mystery",
        "description": "Visitors to Byōdō-in Temple report seeing a phantom figure in the Phoenix Hall at night. Security footage shows nothing.",
        "source_type": "mystery",
        "atmosphere": "temple-reflection",
        "visual_keywords": ["phoenix hall", "reflection pool", "phantom figure", "moonlight", "lotus", "silence"],
    },

    # ── RITUAIS ASSUSTADORES ─────────────────────────────────────────
    {
        "slug": "shimenawa_exorcism",
        "name": "Shimenawa Exorcism Rituals",
        "name_ja": "注連縄の儀式",
        "category": "ritual",
        "description": "Sacred rice straw ropes used to ward off evil spirits. The process of creating and installing them involves ancient Shinto ceremonies.",
        "source_type": "ritual",
        "atmosphere": "shrine-sacred",
        "visual_keywords": ["shimenawa rope", "shrine gate", "rice straw", "purification", "shinto priest", "forest"],
    },
    {
        "slug": "obon_ghost_festival",
        "name": "Obon — Festival of the Dead",
        "name_ja": "お盆",
        "category": "ritual",
        "description": "When the spirits of the dead return to visit the living. Lanterns guide them home, but some refuse to leave.",
        "source_type": "ritual",
        "atmosphere": "lantern-lit-return",
        "visual_keywords": ["paper lanterns", "spirit procession", "family altar", "floating lanterns", "river", "ghost"],
    },
    {
        "slug": "hitobashira",
        "name": "Hitobashira — Human Pillars",
        "name_ja": "人柱",
        "category": "ritual",
        "description": "Ancient practice of burying living people within the foundations of castles and bridges as spiritual guardians.",
        "source_type": "ritual",
        "atmosphere": "castle-foundations",
        "visual_keywords": ["castle wall", "stone foundation", "sacrifice", "buried alive", "ancient construction", "ghost"],
    },
    {
        "slug": "goryo_shinko",
        "name": "Goryō Shinkō — Vengeful Spirits",
        "name_ja": "御霊信仰",
        "category": "ritual",
        "description": "Belief that the spirits of those who died unjustly become powerful vengeful ghosts. Rituals to appease them prevent disasters.",
        "source_type": "ritual",
        "atmosphere": "vengeful-spirit",
        "visual_keywords": ["vengeful spirit", "storm", "fire", "shrine", "exorcism", "dark sky"],
    },
    {
        "slug": "mizuko_kuyo",
        "name": "Mizuko Kuyō — Water Child Memorial",
        "name_ja": "水子供養",
        "category": "ritual",
        "description": "Buddhist ceremony for aborted or miscarried children. Jizō statues protect their souls. Some say the statues weep at night.",
        "source_type": "ritual",
        "atmosphere": "jizo-statue-night",
        "visual_keywords": ["jizo statues", "red bibs", "temple garden", "water offering", "candles", "tiny clothes"],
    },

    # ── SAMURAIS OBSCUROS ────────────────────────────────────────────
    {
        "slug": "ashigaru_ghost",
        "name": "The Ashigaru's Ghost",
        "name_ja": "足軽の亡霊",
        "category": "samurai",
        "description": "Foot soldiers who died in obscurity, their ghosts haunting battlefields centuries later. The forgotten warriors of feudal Japan.",
        "source_type": "samurai",
        "atmosphere": "battlefield-fog",
        "visual_keywords": ["foot soldier", "battlefield", "fog", "broken spear", "ghost army", "dawn"],
    },
    {
        "slug": "tsujigiri",
        "name": "Tsujigiri — Crossroad Killing",
        "name_ja": "辻斬り",
        "category": "samurai",
        "description": "The practice of testing a new sword on a random passerby at night. Some victims' ghosts still haunt the crossroads.",
        "source_type": "samurai",
        "atmosphere": "crossroad-blood",
        "visual_keywords": ["crossroad", "katana", "blood", "night walker", "samurai silhouette", "moonlight"],
    },
    {
        "slug": "ronin_47",
        "name": "The 47 Rōnin — Restless Spirits",
        "name_ja": "四十七士の霊",
        "category": "samurai",
        "description": "After their famous revenge, the 47 rōnin were forced to commit seppuku. Their ghosts reportedly still guard Sengaku-ji Temple.",
        "source_type": "samurai",
        "atmosphere": "temple-graveyard",
        "visual_keywords": ["47 graves", "temple", "incense", "katana", "seppuku", "loyalty", "ghost warriors"],
    },
    {
        "slug": "taira_no_masakado",
        "name": "Taira no Masakado's Curse",
        "name_ja": "平将門の祟り",
        "category": "samurai",
        "description": "A rebel samurai who declared himself emperor. His severed head flew to Tokyo and cursed the land. Construction on his grave causes disasters.",
        "source_type": "samurai",
        "atmosphere": "cursed-grave",
        "visual_keywords": ["severed head", "flying skull", "Tokyo ruins", "cursed ground", "construction disaster", "fire"],
    },

    # ── MISTÉRIOS NÃO RESOLVIDOS ─────────────────────────────────────
    {
        "slug": "dancing_plague_1518",
        "name": "The Dancing Sickness of Japan",
        "name_ja": "踊り病",
        "category": "mystery",
        "description": "Throughout Japanese history, episodes of mass involuntary dancing were documented. Some lasted days. No explanation was ever found.",
        "source_type": "mystery",
        "atmosphere": "mass-hysteria",
        "visual_keywords": ["crowd dancing", "exhaustion", "streets", "no music", "terrified faces", "ancient Japan"],
    },
    {
        "slug": "kamikakushi",
        "name": "Kamikakushi — Spirited Away",
        "name_ja": "神隠し",
        "category": "mystery",
        "description": "The phenomenon of people vanishing without trace, believed to be taken by gods or spirits. Hundreds of documented cases across centuries.",
        "source_type": "mystery",
        "atmosphere": "vanishing-village",
        "visual_keywords": ["empty village", "abandoned meal", "missing person", "forest path", "spirit fog", "search party"],
    },
    {
        "slug": "yamamba",
        "name": "Yamamba — The Mountain Witch",
        "name_ja": "山姥",
        "category": "yokai",
        "description": "An old woman living deep in the mountains who eats travelers. Sometimes helpful, sometimes deadly. The boundary between witch and yokai.",
        "source_type": "classical_kaidan",
        "atmosphere": "deep-mountain-cabin",
        "visual_keywords": ["old woman", "mountain cabin", "deep forest", "spinning wheel", "fire", "cannibalism"],
    },
    {
        "slug": "inugami",
        "name": "Inugami — Dog Spirit Servants",
        "name_ja": "犬神",
        "category": "yokai",
        "description": "Familiars created through cruel rituals involving burying a dog alive. Used for revenge and black magic in western Japan.",
        "source_type": "regional_yokai",
        "atmosphere": "black-magic-altar",
        "visual_keywords": ["dog spirit", "burial ritual", "black magic", "familiar", "revenge", "dark altar"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Image method verification — Chrome extension (gemini-assistant) since 2026-09-13
# ─────────────────────────────────────────────────────────────────────────────
# ComfyUI removed 2026-09-15 (was deprecated since 2026-09-13). The image
# generation method is now the gemini-assistant Chrome extension, operated by
# Isamu manually. See ../PIPELINE_STATE.md for the canonical pipeline state.


def phase_check_image_method() -> dict:
    """Verify the current image-generation method is configured.

    Since 2026-09-13, images are generated via Chrome extension (gemini-assistant).
    This phase:
      1. Prints a pointer to PIPELINE_STATE.md (single source of truth)
      2. Verifies the gemini-assistant workspace exists
      3. Returns a status dict (does NOT block pipeline)

    Returns dict with keys:
      - method: "chrome-extension"
      - workspace_ok: bool
      - workspace_path: str
    """
    state_doc = ROOT.parent / "PIPELINE_STATE.md"
    workspace = (
        Path.home()
        / "Desktop"
        / "JingJing-stuff"
        / "jingjing-workspace"
        / "gemini-assistant"
    )

    logger.info("✓ Image method: chrome-extension (gemini-assistant)")
    logger.info("  See: %s", state_doc)
    logger.info("  Workspace: %s", workspace)

    workspace_ok = workspace.exists()
    if not workspace_ok:
        logger.warning(
            "  gemini-assistant workspace missing at %s "
            "(may be normal on first run)", workspace
        )
    else:
        n = sum(1 for _ in workspace.iterdir() if _.is_dir())
        logger.info("  Workspace OK — %d job(s) present", n)

    return {
        "method": "chrome-extension",
        "workspace_ok": workspace_ok,
        "workspace_path": str(workspace),
        "state_doc": str(state_doc),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase implementations
# ─────────────────────────────────────────────────────────────────────────────

def phase_check_server(port: int) -> bool:
    """Verify the FastAPI server is reachable."""
    try:
        resp = requests.get(f"{_api_base(port)}/projects", timeout=5)
        resp.raise_for_status()
        logger.info("✓ FastAPI server is running on port %d", port)
        return True
    except Exception as exc:
        logger.error("✗ FastAPI server not reachable on port %d: %s", port, exc)
        logger.error("  Start the server first: python main.py")
        return False


def phase_create_project(
    slug: str, title: str, title_ja: str, port: int,
    source_type: str | None = None, atmosphere: str | None = None,
    source_text: str = "", source_url: str | None = None,
    keywords: str | None = None,
) -> int:
    """Create a new project or return existing one's ID."""
    existing = _find_project_by_slug(slug, port)
    if existing:
        project_id = existing["id"]
        logger.info("✓ Project already exists: #%d (slug=%s)", project_id, slug)
        return project_id

    payload = {
        "title": title,
        "title_ja": title_ja or None,
        "source_text_ja": source_text,
        "source_url": source_url,
        "target_keywords": keywords,
        "output_languages": "en",
        "source_type": source_type,
        "atmosphere": atmosphere,
    }
    result = _api_post("/projects", payload, port, timeout=30)
    project_id = result["id"]
    logger.info("✓ Created project #%d (slug=%s, title=%s)", project_id, slug, title)
    return project_id


def phase_generate_script(
    project_id: int, language: str, port: int,
) -> dict:
    """Generate the narration script via LLM."""
    payload = {"language_code": language}
    result = _api_post(f"/projects/{project_id}/script", payload, port, timeout=300)
    script_chars = len(result.get("script", ""))
    logger.info(
        "✓ Script generated: %d chars (model=%s, %.1fs)",
        script_chars, result.get("model", "?"), result.get("elapsed_sec", 0),
    )
    return result


def phase_moderate_content(
    project_id: int, language: str, port: int,
) -> dict:
    """Moderate violent/bloody content in script and image prompts.

    This phase runs AFTER review_script and BEFORE generate_image_prompts.
    It softens graphic violence, blood, gore, and mutilation descriptions
    to comply with YouTube/Instagram content policies.
    """
    from llm_client import moderate_script_content

    # 1. Get the current script from DB
    script = _get_script_from_db(project_id, language)
    if not script:
        logger.warning("moderate_content: no script found, skipping")
        return {"script_moderated": False, "prompts_moderated": 0}

    # 2. Check if script has violent content
    violence_keywords = [
        'blood', 'gore', 'mutilat', 'severed', 'torn flesh',
        'bleeding', 'wound', 'flesh tearing', 'blood-stained',
    ]
    script_lower = script.lower()
    has_violence = any(kw in script_lower for kw in violence_keywords)

    if not has_violence:
        logger.info("moderate_content: no violent content detected, skipping")
        return {"script_moderated": False, "prompts_moderated": 0}

    logger.info("moderate_content: violent content detected, moderating script...")

    # 3. Moderate the script via LLM
    result = moderate_script_content(script)
    moderated_script = result.text

    # 4. Verify moderation removed violence
    mod_lower = moderated_script.lower()
    remaining = [kw for kw in violence_keywords if kw in mod_lower]
    if remaining:
        logger.warning("moderate_content: some violence remains after moderation: %s", remaining)

    # 5. Save moderated script back to DB
    _update_script_in_db(project_id, language, moderated_script)

    logger.info(
        "moderate_content: script moderated (%d → %d chars)",
        len(script), len(moderated_script),
    )

    return {"script_moderated": True, "prompts_moderated": 0}


def _update_script_in_db(project_id: int, language: str, new_script: str) -> None:
    """Update script text in translation_variants table."""
    import sqlite3
    db_path = os.environ.get(
        "REDSUN_DATABASE_URL",
        "sqlite:///./redsunjapan.db",
    )
    if db_path.startswith("sqlite:///"):
        db_path = db_path.replace("sqlite:///", "")
    elif db_path.startswith("sqlite://"):
        db_path = db_path.replace("sqlite://", "")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE translation_variants SET script = ? WHERE project_id = ? AND language_code = ?",
            (new_script, project_id, language),
        )
        conn.commit()
        conn.close()
        logger.info("moderate_content: script updated in DB")
    except Exception as exc:
        logger.error("Failed to update script in DB: %s", exc)


def phase_review_script(
    project_id: int, language: str, port: int,
    source_type: str | None = None, atmosphere: str | None = None,
) -> dict:
    """Review the script via LLM. Returns review results."""
    payload: dict = {"language_code": language}
    if source_type:
        payload["source_type"] = source_type
    if atmosphere:
        payload["atmosphere"] = atmosphere
    result = _api_post(f"/projects/{project_id}/review", payload, port, timeout=300)
    approved = result.get("approved", False)
    score = result.get("score", 0)
    issues = result.get("issues", [])
    logger.info(
        "✓ Review complete: approved=%s score=%d issues=%d (model=%s, %.1fs)",
        approved, score, len(issues), result.get("model", "?"), result.get("elapsed_sec", 0),
    )
    if not approved:
        logger.warning("  Script NOT approved. Issues:")
        for issue in issues:
            logger.warning("    [%s] %s: %s", issue.get("severity", "?"), issue.get("category", "?"), issue.get("message", "?"))
        if result.get("revised_script"):
            logger.info("  → Revised script was provided and saved automatically.")
    return result


def phase_generate_image_prompts(
    project_id: int, language: str, theme_hint: str, port: int,
) -> dict:
    """Generate per-scene image prompts via LLM (theme-aware)."""
    payload = {
        "language_code": language,
        "theme_hint": theme_hint,
    }
    result = _api_post(
        f"/projects/{project_id}/image-prompts", payload, port, timeout=300,
    )
    scene_count = result.get("scene_count", 0)
    logger.info(
        "✓ Image prompts generated: %d scenes (model=%s, %.1fs)",
        scene_count, result.get("model", "?"), result.get("elapsed_sec", 0),
    )
    for p in result.get("prompts", []):
        idx = p.get("scene_index", "?")
        prompt_text = p.get("prompt", "")[:100]
        logger.info("    scene %s: %s...", idx, prompt_text)

    # Get scene text for context in the .md file
    scenes_text = _get_scenes_text_for_prompts(project_id, language, port)

    # Save image prompts as .md for reproducibility
    _save_image_prompts_md(result.get("prompts", []), project_id, theme_hint, scenes_text)

    return result


def _get_scenes_text_for_prompts(project_id: int, language: str, port: int) -> list[str]:
    """Get scene text excerpts for inclusion in image_prompts.md."""
    try:
        # Try to get script from DB
        script = _get_script_from_db(project_id, language)
        if script:
            from generate_video import split_into_scenes
            scenes = split_into_scenes(script, max_scenes=50)
            # Return first 300 chars of each scene for context
            return [s[:300].strip() for s in scenes]
    except Exception as exc:
        logger.debug("Could not get scene text: %s", exc)
    return []


def _save_image_prompts_md(
    prompts: list[dict], project_id: int, theme_hint: str,
    scenes_text: list[str] | None = None,
) -> None:
    """Save image prompts as a markdown file for future regeneration.

    Writes to BOTH:
    - storage/{project_id}/image_prompts.md (pipeline internal)
    - projects/{slug}/images/image_prompts.md (operator-facing)

    When scenes_text is provided, includes the narrative context for each
    scene so the operator can understand what the image represents.
    """
    if not prompts:
        return

    storage_root = os.environ.get(
        "REDSUN_STORAGE_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage"),
    )
    md_path = os.path.join(storage_root, str(project_id), "image_prompts.md")

    lines = [
        f"# {theme_hint} — Image Prompts",
        "",
        f"Project ID: {project_id}",
        f"Theme: {theme_hint}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Each scene below corresponds to one image. To regenerate in another",
        "AI model, copy the prompt text for the scene you need.",
        "",
        "---",
        "",
    ]

    for p in prompts:
        idx = p.get("scene_index", "?")
        prompt = p.get("prompt", "")
        scene_type = p.get("scene_type", "")

        lines.append(f"## Scene {idx}")
        lines.append("")
        if scene_type:
            lines.append(f"**Type:** {scene_type}")
            lines.append("")

        # Include narrative context if available
        if scenes_text and isinstance(idx, int) and 1 <= idx <= len(scenes_text):
            context = scenes_text[idx - 1]
            if context:
                lines.append("**Context (narrative):**")
                lines.append("")
                lines.append(f"> {context}")
                lines.append("")

        lines.append("**Prompt (visual description for AI image generation):**")
        lines.append("")
        lines.append("```")
        lines.append(prompt)
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    md_content = "\n".join(lines)

    # Save to storage
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info("✓ Image prompts saved → %s", md_path)

    # Also save to project images folder
    proj_images_dir = PROJECTS_ROOT / theme_hint.replace(" ", "_").lower() / "images"
    if not proj_images_dir.exists():
        # Try with the slug from the project
        proj_images_dir = PROJECTS_ROOT / theme_hint / "images"
    if proj_images_dir.exists():
        proj_md = proj_images_dir / "image_prompts.md"
        with open(proj_md, "w", encoding="utf-8") as f:
            f.write(md_content)
        logger.info("✓ Image prompts saved → %s", proj_md)


def phase_generate_audio(
    project_id: int, language: str, port: int,
) -> dict:
    """Generate TTS audio for the script."""
    # Try API first; if it returns empty script, fall back to DB
    project_data = _api_get(f"/projects/{project_id}", port)
    script = ""
    if isinstance(project_data, dict):
        variants = project_data.get("variants") or []
        if not isinstance(variants, list):
            variants = []
        for v in variants:
            if isinstance(v, dict) and v.get("language_code") == language:
                script = (v.get("script") or "").strip()
                break

    # Fallback: read script directly from SQLite if API didn't return it
    if not script:
        script = _get_script_from_db(project_id, language) or ""

    if not script:
        raise RuntimeError(
            f"No script found for project {project_id} language {language}. "
            "Run generate_script first."
        )

    payload = {
        "language_code": language,
        "script": script,
    }
    result = _api_post(f"/projects/{project_id}/audio", payload, port, timeout=600)
    duration_sec = result.get("duration_sec", 0)
    logger.info(
        "✓ Audio generated: %.1fs duration (model=%s, %.1fs elapsed)",
        duration_sec, result.get("model", "?"), result.get("elapsed_sec", 0),
    )
    # Warn if audio is suspiciously short (< 8 min = 480s for a long-form video)
    if duration_sec < 480:
        logger.warning(
            "⚠ AUDIO SHORT: only %.1fs (%.1f min). For a 10-15 min video, "
            "the source text should be 2000+ chars JP. Check if the script "
            "was truncated or the source was too brief.",
            duration_sec, duration_sec / 60,
        )
    return result


def _get_script_from_db(project_id: int, language: str) -> str:
    """Read script directly from SQLite as a fallback."""
    try:
        import sqlite3
        db_path = os.environ.get(
            "REDSUN_DATABASE_URL",
            "sqlite:///./redsunjapan.db",
        )
        if db_path.startswith("sqlite:///"):
            db_path = db_path.replace("sqlite:///", "")
        elif db_path.startswith("sqlite://"):
            db_path = db_path.replace("sqlite://", "")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT script FROM translation_variants WHERE project_id = ? AND language_code = ?",
            (project_id, language),
        )
        row = cursor.fetchone()
        conn.close()
        return (row[0] or "").strip() if row else ""
    except Exception as exc:
        logger.warning("DB fallback failed: %s", exc)
        return ""


def phase_transcribe(
    project_id: int, language: str, port: int,
) -> dict:
    """Transcribe the audio to get timestamps."""
    payload = {"language_code": language}
    result = _api_post(f"/projects/{project_id}/transcribe", payload, port, timeout=300)
    logger.info(
        "✓ Transcription done: %d words, %d segments, %.1fs duration",
        result.get("word_count", 0), result.get("segment_count", 0), result.get("duration_sec", 0),
    )
    return result


def phase_render_manifest(
    project_id: int, language: str, port: int,
) -> dict:
    """Generate the render manifest for Remotion."""
    payload = {"language_code": language}
    result = _api_post(
        f"/projects/{project_id}/render-manifest", payload, port, timeout=60,
    )
    logger.info(
        "✓ Render manifest generated: %s",
        result.get("manifest_path", "?"),
    )
    return result


def phase_discover_themes(slug: str) -> list[dict]:
    """Output the theme catalog for the agent to present to the user.

    This phase doesn't call the API — it writes the theme suggestions
    to a file that the agent can read and present as options.
    """
    output_path = STORAGE_ROOT / slug / "theme_suggestions.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save catalog
    output_path.write_text(
        json.dumps(THEMES_CATALOG, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info("✓ Theme suggestions written → %s", output_path)
    logger.info("  %d themes available. Read the file and present options to the user.", len(THEMES_CATALOG))

    for i, theme in enumerate(THEMES_CATALOG, 1):
        logger.info("  %2d. %-20s %s — %s", i, theme["slug"], theme["name_ja"], theme["name"])

    return THEMES_CATALOG


def phase_generate_images(
    project_id: int, language: str, theme_hint: str, port: int,
    model: str = "finalcut", base_seed: int = 42,
) -> dict:
    """DEPRECATED 2026-09-13 — ComfyUI discontinued since Hoichi.

    Pipeline no longer auto-generates images via ComfyUI. Images are now
    generated manually by Isamu via Chrome extension (gemini-assistant).

    Use phase_gate_image_handoff instead — it PAUSES the pipeline and waits
    for Isamu to drop images in the gemini-assistant workspace, then auto-
    resumes on --resume.

    This function is kept as DEPRECATED stub for historical reference.
    See ../PIPELINE_STATE.md.
    """
    logger.warning(
        "phase_generate_images is DEPRECATED. Pipeline uses Chrome extension now. "
        "Use phase_gate_image_handoff. Returning empty result."
    )
    return {
        "deprecated": True,
        "method": "chrome-extension",
        "see": str((ROOT / "PIPELINE_STATE.md").resolve()),
    }


def phase_gate_image_handoff(slug: str) -> dict:
    """HUMAN GATE — Pause pipeline and wait for Isamu to generate images.

    Since 2026-09-13, image generation is performed manually by Isamu via
    the Chrome extension (gemini-assistant). This phase:

      1. Prints instructions to the console (where to go, what to do)
      2. Polls the gemini-assistant workspace for the project
      3. Returns when status == "review" (Isamu marked job as ready)
      4. Writes a checkpoint so --resume knows this phase is done

    Args:
        slug: project slug (matches gemini-assistant workspace folder)

    Returns:
        dict with handoff status + output paths
    """
    workspace = (
        Path.home()
        / "Desktop"
        / "JingJing-stuff"
        / "jingjing-workspace"
        / "gemini-assistant"
        / slug
    )
    output_dir = workspace / "output"
    # Trigger Hook Handoff Gate: notify macOS that prompts are ready for image generation
    try:
        from services.agent_dispatcher import notify_macos
        notify_macos(
            title="Red Sun Japan // Handoff Gate",
            message=f"Prompts gerados para '{slug}'. Pronto para geração de imagens no Gemini Assistant!",
            sound="Glass",
        )
    except Exception as exc:
        logger.warning("Could not emit macOS notification: %s", exc)

    logger.info("🚦 HUMAN GATE: image generation handoff")
    logger.info("=" * 60)
    logger.info("Pipeline is paused. To proceed:")
    logger.info("")
    logger.info("  1. Open: ~/Desktop/JingJing-stuff/jingjing-workspace/")
    logger.info("           gemini-assistant/%s/", slug)
    logger.info("  2. Use the Chrome extension (gemini-assistant) to")
    logger.info("     generate images using the prompts in image_prompts.json")
    logger.info("  3. Drop outputs in: %s", output_dir)
    logger.info("  4. Mark job as ready: edit %s", job_file)
    logger.info("     → set \"status\": \"review\"")
    logger.info("  5. Re-run: python run_pipeline_complete.py --slug %s --resume", slug)
    logger.info("")
    logger.info("See ../PIPELINE_STATE.md for the full flow.")
    logger.info("=" * 60)

    # Quick check: does the job already exist with status=review?
    if job_file.exists():
        try:
            job = json.loads(job_file.read_text(encoding="utf-8"))
            if job.get("status") == "review":
                logger.info("✓ Job status is 'review' — proceeding with handoff.")
                return {
                    "status": "complete",
                    "workspace": str(workspace),
                    "output_dir": str(output_dir),
                    "method": "chrome-extension",
                }
        except Exception as e:
            logger.warning("Could not parse job.json: %s", e)

    # Job not ready yet — pipeline must wait for human
    logger.warning(
        "Job not marked 'review' yet. PIPELINE PAUSES HERE. "
        "Re-run with --resume after dropping images."
    )
    sys.exit(0)  # Clean exit; checkpoint will record this as "stopped here"




def phase_render_video(
    project_id: int, language: str, port: int,
    chunked: bool = True, chunk_seconds: int = 30,
) -> dict:
    """Render the final video via Remotion."""
    payload = {
        "language_code": language,
        "chunked": chunked,
        "chunk_seconds": chunk_seconds,
    }
    result = _api_post(
        f"/projects/{project_id}/render", payload, port, timeout=3600,
    )
    logger.info(
        "✓ Video rendered: %s (%s, %.1fs)",
        result.get("output_path", "?"),
        result.get("status", "?"),
        result.get("elapsed_sec", 0),
    )
    return result


def phase_concat_final(slug: str, ending_gap_seconds: float = 0.0) -> dict:
    """Concatenate branding intro + main video + optional fade-to-black gap + branding ending.

    Audio is normalized to -16 LUFS (YouTube standard) to avoid volume
    jumps between segments. Video is copied without re-encoding.

    Uses videos from:
      - ../branding/red sun japan intro.mp4  (6.5s)
      - render-remotion/<slug>_main.mp4      (variable)
      - [optional] generated fade-to-black gap (ending_gap_seconds, default 0.0 = disabled)
      - ../branding/red sun japan ending.mp4 (6.0s)

    Output: render-remotion/<slug>_final.mp4

    The gap is a 1920x1080 30fps black frame with stereo silence, generated via
    lavfi, that gets loudnorm'd just like the other segments so the concat
    demuxer (c copy) sees a consistent audio profile. Audio fades to silence
    over the gap; visually it's a clean fade-to-black that lingers until the
    ending video takes over.
    """
    import subprocess
    import tempfile

    repo_root = Path(__file__).resolve().parent.parent
    # Paths overridable via env so the pipeline can run from a layout that
    # doesn't follow the legacy <repo>/branding + <repo>/render-remotion assumption.
    # Defaults preserved for operators on the legacy layout.
    branding_dir = Path(
        os.environ.get(
            "REDSUN_BRANDING_DIR",
            str(repo_root.parent / "branding"),
        )
    )
    if (
        os.environ.get("REDSUN_REMOTION_DIR")
        or (repo_root / "render-remotion").exists()
    ):
        remotion_dir = Path(
            os.environ.get("REDSUN_REMOTION_DIR", str(repo_root / "render-remotion"))
        )
    else:
        remotion_dir = repo_root.parent / "render-remotion"

    intro_path = branding_dir / "red sun japan intro.mp4"
    ending_path = branding_dir / "red sun japan ending.mp4"
    main_path = remotion_dir / f"{slug}_main.mp4"
    out_path = remotion_dir / f"{slug}_final.mp4"

    # Verify files
    for label, path in [("intro", intro_path), ("main video", main_path), ("ending", ending_path)]:
        if not path.exists():
            raise FileNotFoundError(f"Missing {label}: {path}")

    # Temp directory for normalized segments
    tmp_dir = remotion_dir / ".concat_tmp"
    tmp_dir.mkdir(exist_ok=True)

    try:
        # Step 0 (optional): Build a fade-to-black gap as a 4th segment.
        # We keep the main video itself un-mutated so Ken Burns / subtitles
        # are not touched; the gap is its own self-contained clip.
        gap_path = None
        if ending_gap_seconds > 0:
            gap_path = tmp_dir / f"{slug}_gap.mp4"
            duration = max(0.5, float(ending_gap_seconds))
            logger.info(
                "  Building fade-to-black gap: %.2fs of black + silence before ending",
                duration,
            )
            # Build gap with an audio fade-to-silence over the whole duration,
            # so when main ends and gap begins the audio drops to zero smoothly.
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=30",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", f"{duration:.3f}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(0.0, duration-0.5):.3f}:d=0.5",
                    "-movflags", "+faststart",
                    str(gap_path),
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Gap generation failed: {result.stderr[:500]}")

        # Step 1: Normalize each segment's audio to -16 LUFS
        segments = [
            ("intro", intro_path),
            ("main", main_path),
        ]
        if gap_path is not None:
            segments.append(("gap", gap_path))
        segments.append(("ending", ending_path))

        norm_paths = []
        for name, src in segments:
            norm_path = tmp_dir / f"{slug}_{name}_norm.mp4"
            logger.info("  Normalizing audio: %s → -16 LUFS", name)
            if name in ("intro", "ending"):
                cmd_norm = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-video_track_timescale", "90000",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary",
                    "-ar", "48000",
                    "-movflags", "+faststart",
                    str(norm_path),
                ]
            else:
                cmd_norm = [
                    "ffmpeg", "-y", "-i", str(src),
                    "-c:v", "copy",
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary",
                    "-ar", "48000",
                    "-movflags", "+faststart",
                    str(norm_path),
                ]
            result = subprocess.run(
                cmd_norm,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Audio normalization failed for {name}: {result.stderr[:500]}")
            norm_paths.append(norm_path)

        # Step 2: Concatenate normalized segments
        list_path = tmp_dir / ".concat_list.txt"
        list_path.write_text(
            "\n".join(f"file '{p}'" for p in norm_paths) + "\n",
            encoding="utf-8",
        )

        logger.info("Concatenating: intro + %s + ending → %s", slug, out_path.name)
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy", "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                str(out_path),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr[:500]}")

    finally:
        # Cleanup temp files
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Materialize final video into workspace canonical paths if workspace exists
    try:
        from redsun.workspace import ProjectWorkspace
        ws = ProjectWorkspace.from_slug(slug)
        (ws.root / "video").mkdir(parents=True, exist_ok=True)
        (ws.root / "output").mkdir(parents=True, exist_ok=True)
        (ws.root / "render").mkdir(parents=True, exist_ok=True)

        shutil.copy2(out_path, ws.video_final_path)
        shutil.copy2(out_path, ws.root / "video" / f"{slug}_master.mp4")
        shutil.copy2(out_path, ws.root / "output" / "output_master_with_bumper.mp4")
        shutil.copy2(out_path, ws.root / "output" / f"{slug}_final.mp4")
        shutil.copy2(out_path, ws.root / "render" / "output_master.mp4")
        shutil.copy2(out_path, ws.root / "render" / f"{slug}_master_with_bumpers.mp4")
        logger.info("✓ Propagated final video with bumpers to workspace: %s", ws.root)
    except Exception as exc:
        logger.warning("Could not sync final video to workspace (%s): %s", slug, exc)

    size_mb = out_path.stat().st_size / 1024 / 1024
    logger.info("✓ Final video: %s (%.1f MB, audio normalized to -16 LUFS)", out_path, size_mb)
    return {"output_path": str(out_path), "size_mb": round(size_mb, 1)}


def phase_generate_blog(
    project_id: int, language: str, port: int,
) -> dict:
    """Generate a blog article from the script via LLM."""
    payload = {"language_code": language}
    result = _api_post(f"/projects/{project_id}/blog", payload, port, timeout=300)
    logger.info(
        "✓ Blog article generated: %d chars (model=%s, %.1fs)",
        len(result.get("blog_article", "")),
        result.get("model", "?"),
        result.get("elapsed_sec", 0),
    )
    return result


def phase_generate_shorts(
    project_id: int, language: str, port: int,
    max_clips: int = 5,
) -> dict:
    """Generate short-form clip suggestions from the script via LLM."""
    payload = {"language_code": language, "max_clips": max_clips}
    result = _api_post(f"/projects/{project_id}/shorts", payload, port, timeout=300)
    clip_count = len(result.get("clips", []))
    logger.info(
        "✓ Shorts generated: %d clips (model=%s, %.1fs)",
        clip_count,
        result.get("model", "?"),
        result.get("elapsed_sec", 0),
    )
    for clip in result.get("clips", []):
        logger.info(
            "    clip %s: %s (%ds)",
            clip.get("index", "?"),
            clip.get("title", "?")[:60],
            clip.get("duration_sec", 0),
        )
    return result


def phase_generate_post_kit(
    project_id: int, language: str, port: int,
) -> dict:
    """Generate the post-publishing kit (YouTube desc, social captions, etc.)."""
    payload = {"language_code": language}
    result = _api_post(
        f"/projects/{project_id}/post-kit", payload, port, timeout=300,
    )
    file_count = len(result.get("files", []))
    logger.info("✓ Post kit generated: %d files", file_count)
    for f in result.get("files", []):
        logger.info("    → %s (%s)", f.get("filename", "?"), f.get("purpose", "?"))
    return result


def phase_organize_output(
    slug: str, project_id: int, language: str,
    title: str, title_ja: str = "",
) -> dict:
    """Organize all project outputs into a clean folder structure.

    Structure:
        projects/{slug}/
        ├── video/           ← final MP4
        ├── shorts/          ← short clips
        ├── text/            ← YouTube desc, social captions, pinned comment
        ├── blog/            ← blog article markdown
        ├── images/          ← ComfyUI generated images
        └── data/            ← audio, timestamps, manifest, script
    """
    import shutil

    proj_dir = PROJECTS_ROOT / slug
    dirs = {
        "video": proj_dir / "video",
        "shorts": proj_dir / "shorts",
        "text": proj_dir / "text",
        "blog": proj_dir / "blog",
        "images": proj_dir / "images",
        "data": proj_dir / "data",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    project_storage = STORAGE_ROOT / str(project_id)
    copied = []

    # 1. Copy final video from render-remotion/
    repo_root = Path(__file__).resolve().parent.parent
    remotion_dir = repo_root / "render-remotion"
    for video_file in [f"{slug}_final.mp4", f"{slug}_main.mp4"]:
        src_video = remotion_dir / video_file
        if src_video.exists():
            dst = dirs["video"] / video_file
            if not dst.exists():
                shutil.copy2(src_video, dst)
                copied.append(f"video/{video_file}")

    # 2. Copy images
    src_images = project_storage / "images"
    if src_images.exists():
        for img in src_images.glob("*.png"):
            dst = dirs["images"] / img.name
            if not dst.exists():
                shutil.copy2(img, dst)
                copied.append(f"images/{img.name}")

    # 2b. Copy image prompts .md (reproducibility reference)
    src_prompts_md = project_storage / "image_prompts.md"
    if src_prompts_md.exists():
        dst = dirs["images"] / "image_prompts.md"
        shutil.copy2(src_prompts_md, dst)
        copied.append("images/image_prompts.md")

    # 2. Copy audio
    src_audio = project_storage / f"audio_{language}.wav"
    if src_audio.exists():
        dst = dirs["data"] / src_audio.name
        if not dst.exists():
            shutil.copy2(src_audio, dst)
            copied.append(f"data/{src_audio.name}")

    # 3. Copy timestamps
    src_ts = project_storage / f"timestamps_{language}.json"
    if src_ts.exists():
        dst = dirs["data"] / src_ts.name
        if not dst.exists():
            shutil.copy2(src_ts, dst)
            copied.append(f"data/{src_ts.name}")

    # 4. Copy blog article
    blog_src = STORAGE_ROOT / "blog_articles"
    for blog_file in blog_src.glob(f"{slug}*"):
        dst = dirs["blog"] / blog_file.name
        if not dst.exists():
            shutil.copy2(blog_file, dst)
            copied.append(f"blog/{blog_file.name}")

    # 5. Copy post-kit files
    post_kit_src = STORAGE_ROOT / "post_kits"
    for kit_dir in post_kit_src.glob(f"{slug}*"):
        if kit_dir.is_dir():
            for f in kit_dir.iterdir():
                # YouTube desc + pinned comment → text/
                if "youtube" in f.name or "social" in f.name or "thumbnail" in f.name:
                    dst = dirs["text"] / f.name
                elif f.suffix == ".json":
                    dst = dirs["data"] / f.name
                else:
                    dst = dirs["text"] / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)
                    copied.append(f"text/{f.name}")

    # 6. Write project metadata
    meta = {
        "slug": slug,
        "project_id": project_id,
        "title": title,
        "title_ja": title_ja,
        "language": language,
        "organized_at": datetime.now(timezone.utc).isoformat(),
        "files_copied": copied,
    }
    meta_path = dirs["data"] / "project_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("✓ Output organized: %s (%d files copied)", proj_dir, len(copied))
    return {"project_dir": str(proj_dir), "files_copied": copied}


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    slug: str,
    title: str,
    title_ja: str = "",
    language: str = "en",
    port: int = 8771,
    source_type: str | None = None,
    atmosphere: str | None = None,
    source_text: str = "",
    source_url: str | None = None,
    keywords: str | None = None,
    theme_hint: str = "",
    resume: bool = True,
    skip_phases: list[str] | None = None,
    ending_gap_seconds: float = 0.0,
) -> dict:
    """Run the full pipeline with checkpoint-based resumption.

    Returns a summary dict with phase results.
    """
    skip = set(skip_phases or [])
    summary: dict = {"slug": slug, "phases": {}}
    project_id: int | None = None

    logger.info("=" * 70)
    logger.info("RED SUN JAPAN — PIPELINE RUNNER")
    logger.info("  slug:       %s", slug)
    logger.info("  title:      %s", title)
    logger.info("  language:   %s", language)
    logger.info("  port:       %d", port)
    logger.info("  theme_hint: %s", theme_hint or "(from title)")
    logger.info("  resume:     %s", resume)
    logger.info("  skip:       %s", skip or "(none)")
    logger.info("=" * 70)

    for phase in PHASES:
        # Skip if explicitly excluded
        if phase in skip:
            logger.info("⊘ Skipping phase '%s' (explicitly excluded)", phase)
            summary["phases"][phase] = {"status": "skipped"}

            # Recover project_id from create_project checkpoint even if skipped
            if project_id is None and phase == "create_project":
                cp = _load_checkpoint(slug, "create_project")
                if cp and cp.get("data", {}).get("project_id"):
                    project_id = cp["data"]["project_id"]
                    logger.info("  ↳ recovered project_id=%d from checkpoint", project_id)
            continue

        # Skip if already completed and resume is enabled
        if resume and _is_phase_done(slug, phase):
            cp = _load_checkpoint(slug, phase)
            logger.info(
                "⊘ Skipping phase '%s' (already completed at %s)",
                phase, cp.get("completed_at", "?") if cp else "?",
            )
            summary["phases"][phase] = {"status": "already_done", "checkpoint": cp}
            # Recover project_id from checkpoint if available
            if phase == "create_project" and cp and cp.get("data", {}).get("project_id"):
                project_id = cp["data"]["project_id"]
            continue

        logger.info("")
        logger.info("─" * 70)
        logger.info("PHASE: %s", phase.upper())
        logger.info("─" * 70)

        t0 = time.time()
        try:
            if phase == "check_server":
                ok = phase_check_server(port)
                if not ok:
                    logger.error("Pipeline ABORTED: server not running.")
                    summary["phases"][phase] = {"status": "failed", "error": "server not reachable"}
                    return summary
                summary["phases"][phase] = {"status": "ok"}

            elif phase == "discover_themes":
                themes = phase_discover_themes(slug)
                _mark_phase_done(slug, phase, {"theme_count": len(themes)})
                summary["phases"][phase] = {"status": "ok", "themes": themes}

            elif phase == "create_project":
                project_id = phase_create_project(
                    slug, title, title_ja, port,
                    source_type=source_type, atmosphere=atmosphere,
                    source_text=source_text, source_url=source_url,
                    keywords=keywords,
                )
                _mark_phase_done(slug, phase, {"project_id": project_id})
                summary["phases"][phase] = {"status": "ok", "project_id": project_id}

            elif phase == "generate_script":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_generate_script(project_id, language, port)
                _mark_phase_done(slug, phase, {"chars": len(result.get("script", ""))})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "review_script":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_review_script(
                    project_id, language, port,
                    source_type=source_type, atmosphere=atmosphere,
                )
                _mark_phase_done(slug, phase, {
                    "approved": result.get("approved"),
                    "score": result.get("score"),
                })
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "moderate_content":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_moderate_content(project_id, language, port)
                _mark_phase_done(slug, phase, {
                    "script_moderated": result.get("script_moderated", False),
                    "prompts_moderated": result.get("prompts_moderated", 0),
                })
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "generate_image_prompts":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                effective_theme = theme_hint or title
                result = phase_generate_image_prompts(
                    project_id, language, effective_theme, port,
                )
                _mark_phase_done(slug, phase, {"scene_count": result.get("scene_count")})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "generate_images":
                # REMOVED 2026-09-15: ComfyUI is no longer in the pipeline.
                # Images come from the gemini-assistant Chrome extension
                # (operated manually by Isamu) and land in projects/<slug>/images/.
                # The correct phase is gate_image_handoff — see PIPELINE_STATE.md.
                # If this branch runs, it means the orchestrator is running an
                # old phases list. Refuse loudly so the operator notices.
                raise RuntimeError(
                    "phase='generate_images' was removed on 2026-09-15. "
                    "Images now come from the gemini-assistant Chrome extension. "
                    "Run phase 'gate_image_handoff' instead, then "
                    "'organize_output' will copy images from projects/<slug>/images/. "
                    "See PIPELINE_STATE.md for the canonical pipeline state."
                )

            elif phase == "gate_image_handoff":
                # Verify that images exist in projects/<slug>/images/ before
                # continuing. This is the human gate: Isamu generates images
                # via the gemini-assistant Chrome extension, then this phase
                # validates the asset handoff. See PIPELINE_STATE.md.
                phase_gate_image_handoff(slug)
                _mark_phase_done(slug, phase, {})
                summary["phases"][phase] = {"status": "ok"}

            elif phase == "generate_audio":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_generate_audio(project_id, language, port)
                _mark_phase_done(slug, phase, {"duration_sec": result.get("duration_sec")})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "transcribe":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_transcribe(project_id, language, port)
                # Validation: check word count
                word_count = result.get("word_count", 0)
                if word_count == 0:
                    raise RuntimeError("TRANSCRIPTION VALIDATION FAILED: 0 words transcribed")
                _mark_phase_done(slug, phase, {"segments": result.get("segment_count"), "words": word_count})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "render_manifest":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_render_manifest(project_id, language, port)
                # Validation: check manifest has images and duration
                img_count = result.get("image_count", 0)
                duration = result.get("duration_seconds", 0)
                if img_count == 0:
                    raise RuntimeError("MANIFEST VALIDATION FAILED: 0 images in manifest")
                if duration <= 0:
                    raise RuntimeError(f"MANIFEST VALIDATION FAILED: invalid duration {duration}s")
                bgm = result.get("bgm_filename", "none")
                logger.info(
                    "  Manifest: %d images, %.1fs duration, BGM=%s",
                    img_count, duration, bgm,
                )
                _mark_phase_done(slug, phase, {"manifest_path": result.get("manifest_path")})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "render_video":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_render_video(
                    project_id, language, port,
                    chunked=True, chunk_seconds=30,
                )
                _mark_phase_done(slug, phase, {
                    "output_path": result.get("output_path"),
                    "chunks": result.get("chunks_rendered"),
                })
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "concat_final":
                # concat_final itself does not need project_id (it works off
                # slug + filesystem paths), but earlier phases skipped via
                # --only-phases may have left it unset. Try to recover from
                # the create_project checkpoint; if not available (project was
                # aborted before create_project ever completed, or the
                # checkpoint was deleted), proceed anyway — phase_concat_final
                # only touches the filesystem and does not call the API.
                if project_id is None:
                    cp = _load_checkpoint(slug, "create_project")
                    recovered = (cp or {}).get("data", {}).get("project_id")
                    if recovered is not None:
                        project_id = recovered
                        logger.info(
                            "  ↳ recovered project_id=%d from create_project checkpoint",
                            project_id,
                        )
                    else:
                        logger.info(
                            "  ↳ no project_id available (create_project never checkpointed); "
                            "proceeding with concat_final on slug-only paths"
                        )
                result = phase_concat_final(slug, ending_gap_seconds=ending_gap_seconds)
                _mark_phase_done(slug, phase, {
                    "output_path": result.get("output_path"),
                    "size_mb": result.get("size_mb"),
                })
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "generate_blog":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_generate_blog(project_id, language, port)
                _mark_phase_done(slug, phase, {"chars": len(result.get("blog_article", ""))})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "generate_shorts":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_generate_shorts(project_id, language, port)
                _mark_phase_done(slug, phase, {"clips": len(result.get("clips", []))})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "generate_post_kit":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_generate_post_kit(project_id, language, port)
                _mark_phase_done(slug, phase, {"files": len(result.get("files", []))})
                summary["phases"][phase] = {"status": "ok", "result": result}

            elif phase == "organize_output":
                if project_id is None:
                    raise RuntimeError("project_id not set — run create_project first")
                result = phase_organize_output(
                    slug, project_id, language, title, title_ja,
                )
                _mark_phase_done(slug, phase, {
                    "project_dir": result.get("project_dir"),
                    "files": len(result.get("files_copied", [])),
                })
                summary["phases"][phase] = {"status": "ok", "result": result}

                # Trigger Hook Stage 4 & 5: Notify macOS and dispatch JingJing for Release Audit and Distribution
                try:
                    from services.agent_dispatcher import notify_macos, dispatch_hermes_agent, dispatch_background
                    notify_macos(
                        title="Red Sun Japan // Render Concluído",
                        message=f"Vídeo Master de '{slug}' compilado. Acionando JingJing para auditoria e despacho.",
                        sound="Submarine",
                    )
                    prompt_stage_4_5 = (
                        f"JingJing, o pipeline concluiu com sucesso a renderização completa (16/16) do projeto '{slug}'. "
                        f"Execute imediatamente o Stage 4 (Auditoria de Integridade do Master sem bumpers externos, "
                        f"conferência de legendas contra o script e integridade dos 3 Shorts) "
                        f"e o Stage 5 (Git push do blog Astro e upload privado no YouTube). "
                        f"Emita o Release Audit Report final ao Isamu conforme o AGENTS.md."
                    )
                    dispatch_background(dispatch_hermes_agent(prompt_stage_4_5))
                except Exception as exc:
                    logger.warning("Could not dispatch Stage 4/5 agent hook in run_pipeline_complete: %s", exc)

            elapsed = time.time() - t0
            logger.info("  Phase '%s' completed in %.1fs", phase, elapsed)

        except requests.HTTPError as exc:
            elapsed = time.time() - t0
            resp = exc.response
            error_msg = f"HTTP {resp.status_code if resp else '?'}: {(resp.text[:500] if resp else str(exc))}"
            logger.error("✗ Phase '%s' FAILED after %.1fs: %s", phase, elapsed, error_msg)
            summary["phases"][phase] = {"status": "failed", "error": error_msg, "elapsed": elapsed}
            # Don't abort — let user decide if they want to retry
            return summary

        except Exception as exc:
            elapsed = time.time() - t0
            logger.error("✗ Phase '%s' FAILED after %.1fs: %s", phase, elapsed, exc)
            summary["phases"][phase] = {"status": "failed", "error": str(exc), "elapsed": elapsed}
            return summary

    # Final summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 70)
    logger.info("  slug:       %s", slug)
    logger.info("  project_id: %s", project_id)
    completed_count = sum(
        1 for v in summary["phases"].values() if v.get("status") in ("ok", "already_done")
    )
    logger.info("  phases:     %d/%d completed", completed_count, len(PHASES))

    # Check if video was rendered
    render_result = summary["phases"].get("render_video", {}).get("result", {})
    if render_result and render_result.get("output_path"):
        logger.info("")
        logger.info("  🎬 VIDEO: %s", render_result["output_path"])

    # Check if output was organized
    organize_result = summary["phases"].get("organize_output", {}).get("result", {})
    if organize_result and organize_result.get("project_dir"):
        logger.info("")
        logger.info("  📁 PROJECT: %s", organize_result["project_dir"])

    # Show what's missing
    missing = []
    if not summary["phases"].get("generate_blog", {}).get("result"):
        missing.append("blog article")
    if not summary["phases"].get("generate_shorts", {}).get("result"):
        missing.append("shorts clips")
    if not summary["phases"].get("render_video", {}).get("result"):
        missing.append("video render")
    if not summary["phases"].get("generate_post_kit", {}).get("result"):
        missing.append("post kit (YouTube desc, social)")
    if missing:
        logger.info("")
        logger.info("  Missing: %s", ", ".join(missing))
        logger.info("  Re-run with --resume to complete remaining phases.")

    logger.info("=" * 70)

    summary["project_id"] = project_id
    summary["status"] = "complete"
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Red Sun Japan — Standalone Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # New project (Mujina)
  python run_pipeline_complete.py --slug mujina --title "Mujina" --theme mujina

  # Resume interrupted project
  python run_pipeline_complete.py --slug mujina --title "Mujina" --resume

  # Skip audio/transcribe phases
  python run_pipeline_complete.py --slug kappa --title "Kappa" --skip-phases audio,transcribe

  # Specify source type and atmosphere
  python run_pipeline_complete.py --slug yuki_onna --title "Yuki Onna" \\
      --source-type classical_kaidan --atmosphere "lantern-light-and-fog"
        """,
    )
    parser.add_argument(
        "--slug", type=str, default=None,
        help="Short identifier for the project (used in file names and API calls).",
    )
    parser.add_argument(
        "--title", type=str, default=None,
        help="Project title (e.g. 'Mujina', 'Yuki Onna').",
    )
    parser.add_argument(
        "--title-ja", type=str, default="",
        help="Optional Japanese title (e.g. '貉').",
    )
    parser.add_argument(
        "--language", type=str, default="en",
        help="Output language code (default: en).",
    )
    parser.add_argument(
        "--port", type=int, default=8771,
        help="FastAPI server port (default: 8771).",
    )
    parser.add_argument(
        "--source-type", type=str, default=None,
        help="Source classification (e.g. 'classical_kaidan', 'regional_yokai').",
    )
    parser.add_argument(
        "--atmosphere", type=str, default=None,
        help="Atmosphere/tone target (e.g. 'lantern-light-and-fog').",
    )
    parser.add_argument(
        "--source-text", type=str, default="",
        help="Raw Japanese source text (optional — server can fetch from sources).",
    )
    parser.add_argument(
        "--source-url", type=str, default=None,
        help="URL where source text was obtained.",
    )
    parser.add_argument(
        "--keywords", type=str, default=None,
        help="Comma-separated SEO keywords.",
    )
    parser.add_argument(
        "--theme", type=str, default="",
        help="Theme hint for image prompts (e.g. 'yuki_onna', 'mujina'). Defaults to title.",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Skip phases that have checkpoint files (default: True).",
    )
    parser.add_argument(
        "--no-resume", dest="resume", action="store_false",
        help="Force re-run all phases, ignoring checkpoints.",
    )
    parser.add_argument(
        "--skip-phases", type=str, default="",
        help="Comma-separated list of phases to skip (e.g. 'audio,transcribe').",
    )
    parser.add_argument(
        "--only-phases", type=str, default="",
        help="Comma-separated list of phases to run ONLY (skip everything else).",
    )
    parser.add_argument(
        "--ending-gap-seconds", type=float, default=0.0,
        help="Seconds of fade-to-black silence before the ending video (default: 0.0 = off).",
    )
    parser.add_argument(
        "--base-seed", type=int, default=42,
        help="Base seed reserved for future deterministic prompts (default: 42).",
    )
    parser.add_argument(
        "--max-clips", type=int, default=0,
        help="Number of short-form clips to generate (0 = disabled, default).",
    )
    parser.add_argument(
        "--chunked", action="store_true", default=True,
        help="Use chunked Remotion rendering (default: True).",
    )
    parser.add_argument(
        "--no-chunked", dest="chunked", action="store_false",
        help="Use single-pass Remotion rendering (for short videos).",
    )
    parser.add_argument(
        "--chunk-seconds", type=int, default=30,
        help="Chunk duration in seconds for Remotion rendering (default: 30).",
    )
    parser.add_argument(
        "--list-phases", action="store_true",
        help="List all available phases and exit.",
    )
    parser.add_argument(
        "--legacy-pipeline", action="store_true", default=False,
        help=argparse.SUPPRESS,  # hidden — preserves legacy operators' muscle memory
    )

    args = parser.parse_args()

    if args.list_phases:
        print("Available phases (16 total):")
        for i, phase in enumerate(PHASES, 1):
            print(f"  {i:2d}. {phase}")
        return

    if not args.slug or not args.title:
        parser.error("--slug and --title are required (unless using --list-phases)")

    log_dir = STORAGE_ROOT / args.slug
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), mode="a", encoding="utf-8"),
        ],
    )

    # ── Dispatch: new pipeline (default) vs legacy pipeline (--legacy-pipeline) ──
    if not args.legacy_pipeline:
        try:
            _run_new_pipeline(args)
            sys.exit(0)
        except Exception as exc:
            logger.error("New pipeline failed: %s", exc)
            # Do NOT fall back to legacy — operators who want the legacy
            # flow must opt in explicitly with --legacy-pipeline.
            sys.exit(1)
        return

    # ── Legacy pipeline (--legacy-pipeline): preserved unchanged ────────
    skip = [s.strip() for s in args.skip_phases.split(",") if s.strip()]

    # Handle --only-phases: if set, skip everything NOT in this list
    if args.only_phases:
        only = set(s.strip() for s in args.only_phases.split(",") if s.strip())
        skip = [p for p in PHASES if p not in only]

    summary = run_pipeline(
        slug=args.slug,
        title=args.title,
        title_ja=args.title_ja,
        language=args.language,
        port=args.port,
        source_type=args.source_type,
        atmosphere=args.atmosphere,
        source_text=args.source_text,
        source_url=args.source_url,
        keywords=args.keywords,
        theme_hint=args.theme,
        resume=args.resume,
        skip_phases=skip,
        ending_gap_seconds=args.ending_gap_seconds,
    )

    # Write summary to disk
    summary_path = STORAGE_ROOT / args.slug / "pipeline_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Summary written → %s", summary_path)

    # Exit with appropriate code
    failed = [p for p in summary["phases"].values() if p.get("status") == "failed"]
    if failed:
        logger.error("Pipeline finished with %d failed phase(s).", len(failed))
        sys.exit(1)
    else:
        logger.info("Pipeline finished successfully.")
        sys.exit(0)


def _parse_phase_list(raw: str, kind: str) -> list[str]:
    """Parse a comma-separated phase list from CLI args.

    Returns an empty list when `raw` is empty. Validates the names
    against the runner's `PHASE_ORDER`; raises `ValueError` with a
    sorted list of valid phases when an unknown name is provided.
    Silent fall-through is forbidden (D3 in the design).
    """
    if not raw:
        return []
    from redsun.pipeline.runner import PHASE_ORDER

    valid = set(PHASE_ORDER)
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in valid]
    if unknown:
        valid_sorted = ", ".join(sorted(valid))
        raise ValueError(
            f"unknown {kind} phase(s): {unknown!r}. "
            f"Valid phases: {valid_sorted}"
        )
    return names



def _run_new_pipeline(args) -> None:
    """The new wired flow: ProjectContext → open_or_create → PipelineRunner.

    See `openspec/changes/wire-production-pipeline/proposal.md` and
    `design.md` for the contract. This shim is intentionally small —
    the heavy lifting lives in `redsun.pipeline.runner.PipelineRunner`.
    """
    from redsun.context import ProjectContext
    from redsun.workspace import ProjectWorkspace, WorkspaceError
    from redsun.pipeline.runner import (
        PipelineRunner,
        install_default_services,
    )

    # 1. Build the canonical context from CLI args.
    context = ProjectContext.from_cli_args(args)
    context.workspace = ProjectWorkspace.from_slug(args.slug)

    # 2. Resolve the canonical workspace (idempotent).
    try:
        workspace, mode = ProjectWorkspace.open_or_create(args.slug)
    except WorkspaceError as exc:
        logger.error("workspace error: %s", exc)
        raise

    logger.info(
        "new pipeline: workspace=%s mode=%s schema_version=%d",
        workspace.root,
        mode,
        1,
    )

    # 2b. Parse phase-boundary flags on the new pipeline path.
    only_phases = _parse_phase_list(getattr(args, "only_phases", "") or "", "only")
    skip_phases = _parse_phase_list(getattr(args, "skip_phases", "") or "", "skip")

    # 3. Wire the production services (LLM/TTS/Whisper/Image HTTP clients)
    # into the runner's SERVICES registry BEFORE running. Without this,
    # every phase that depends on a service sees `SERVICES["<key>"] is None`
    # and the phase is recorded as `skipped` with `data: {}` — which the
    # checkpoint validator still treats as `completed`. That is the silent
    # failure mode this fix prevents.
    #
    # Tests that mock the runner should NOT call this CLI; they should
    # inject their stubs directly into the SERVICES registry. Production
    # invocations always wire the default services.
    try:
        install_default_services(context)
        logger.info("services installed: llm/tts/whisper/image wired to FastAPI")
    except Exception as exc:
        logger.warning(
            "install_default_services failed (%s); phases will be skipped. "
            "Is the FastAPI server running on the expected port?",
            exc,
        )

    result = PipelineRunner.run(
        context,
        workspace,
        resume=bool(getattr(args, "resume", True)),
        only_phases=only_phases or None,
        skip_phases=skip_phases or None,
    )

    # 4. Emit the structured result.
    if result.status == "READY":
        logger.info(
            "READY: %s @ %s",
            result.technical_slug,
            result.video_final or "(no video_final)",
        )
    else:
        bits = [f"FAILED: {result.technical_slug} @ {result.failed_phase or 'unknown'}"]
        if result.error_code:
            bits.append(f"code={result.error_code}")
        if result.expected is not None:
            bits.append(f"expected={result.expected}")
        if result.actual is not None:
            bits.append(f"actual={result.actual}")
        logger.error(" ".join(bits))

    # 4b. JSON-serialize the structured PipelineResult on stdout so
    # downstream tooling can pipe it.
    print(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str)
    )

    # 5. Persist the structured PipelineResult for downstream consumers.
    summary_path = workspace.pipeline_summary_path
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("PipelineResult written → %s", summary_path)


if __name__ == "__main__":
    main()
