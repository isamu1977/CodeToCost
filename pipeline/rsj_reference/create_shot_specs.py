#!/usr/bin/env python3
"""
Create shot spec YAML files for all 32 scenes.
Uses data from image_prompts.md and narration.txt.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path("/Users/isamumatsuyama/Documents/online-projects/red-sun-japan/channel-redsunjapan/projects/hoichi_the_earless")
SHOTS_DIR = PROJECT_ROOT / "shots"
SHOTS_DIR.mkdir(exist_ok=True)

# Comprehensive scene data based on narration + image_prompts
SCENES = {
    1: {
        "narrative": "It began with footsteps in the rain. The rain that night over Akama was the kind that erases all other sound, turning the Inland Sea into grey nothing.",
        "shot": {"type": "establishing", "angle": "eye_level"},
        "characters": [],
        "location": {"description": "rain-soaked stone path leading to ancient temple gate, cedar trees, distant Inland Sea grey smear", "time_of_day": "night", "weather": "heavy rain"},
        "lighting": {"primary_source": "paper lanterns", "quality": "flickering through rain"},
        "emotion": ["ominous", "mysterious"],
        "critical": ["rain visible as streaks", "temple gate silhouette", "stone path reflective"]
    },
    2: {
        "narrative": "Not sandals on wet stone — heavier, rhythmic, as though someone in armor walking slowly toward the temple gate.",
        "shot": {"type": "wide", "angle": "low_angle"},
        "characters": [{"id": "samurai_warrior", "position": "midground", "action": "walking in heavy rain toward temple", "expression": "stern, cold", "pose": "armored figure in heavy rain"}],
        "location": {"description": "temple approach path, heavy rain, distant lanterns", "time_of_day": "night", "weather": "heavy rain"},
        "lighting": {"primary_source": "distant paper lanterns", "quality": "dim amber through rain"},
        "emotion": ["foreboding", "supernatural"],
        "critical": ["armored samurai ghost", "kabuto helmet with horns", "rain falling"]
    },
    4: {
        "narrative": "Inside the hall, Hoichi sat alone when the voice of the samurai spoke with clipped authority. When he stepped inside, the air grew cold.",
        "shot": {"type": "medium", "angle": "low_angle"},
        "characters": [
            {"id": "samurai_warrior", "position": "foreground", "action": "standing over Hoichi with iron grip", "expression": "commanding, cold", "pose": "armored figure with hand on sword"},
            {"id": "hoichi", "position": "background", "action": "looking up with vacant blind eyes", "expression": "vulnerable", "pose": "kneeling on tatami with biwa"}
        ],
        "location": {"description": "interior of Amida-ji temple hall", "time_of_day": "night", "weather": "rain outside"},
        "lighting": {"primary_source": "low oil lamp", "quality": "dim, warm, flickering"},
        "emotion": ["ominous", "supernatural"],
        "critical": ["samurai's iron grip visible", "Hoichi's blind eyes unfocused", "cold aura around samurai"]
    },
    6: {
        "narrative": "Amida-ji temple cemetery holds the tombs of Antoku and his Heike court. Ancient stone tombs rise from misty ground.",
        "shot": {"type": "establishing", "angle": "wide"},
        "characters": [],
        "location": {"description": "ancient Heike cemetery, stone tombs, misty atmosphere", "time_of_day": "night", "weather": "mist"},
        "lighting": {"primary_source": "ghostly pale lights floating", "quality": "cold supernatural glow"},
        "emotion": ["solemn", "supernatural"],
        "critical": ["ancient stone tombs", "misty atmosphere", "ghostly fire lights"]
    },
    7: {
        "narrative": "Akama faces the narrow channel where the Heike fought their final battle at Dan-no-ura. Ghostly ships still sail these waters.",
        "shot": {"type": "establishing", "angle": "wide"},
        "characters": [],
        "location": {"description": "Dan-no-ura sea channel, stormy waves, ghostly ships", "time_of_day": "stormy night", "weather": "stormy sea"},
        "lighting": {"primary_source": "moonlight through storm clouds", "quality": "cold, dramatic"},
        "emotion": ["somber", "historical"],
        "critical": ["stormy sea", "ghostly Heike ships", "moonlight through clouds"]
    },
    9: {
        "narrative": "He could not see the path but felt the familiar stones giving way to mud, then slick cedar roots, then polished wood. The rain seemed muffled.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "foreground", "action": "walking through rain, feet on wet surfaces", "expression": "confused, sensing", "pose": "barefoot in rain"}],
        "location": {"description": "transition from stone path to mud to polished wooden floor", "time_of_day": "night", "weather": "heavy rain"},
        "lighting": {"primary_source": "distant lanterns", "quality": "fading into supernatural dimness"},
        "emotion": ["transitional", "mystical"],
        "critical": ["Hoichi's bare feet on wet surfaces", "rain muffling into silence", "transitioning environment"]
    },
    10: {
        "narrative": "A woman's voice, authoritative and calm, addressed him. She was the chief lady-in-waiting of a noble household.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "foreground", "action": "kneeling, tuning biwa", "expression": "attentive, respectful", "pose": "kneeling with biwa"}],
        "location": {"description": "dimly lit Heian-era hall, sliding shoji screens, paper lanterns", "time_of_day": "night", "weather": "rain outside"},
        "lighting": {"primary_source": "paper lanterns", "quality": "dim, warm"},
        "emotion": ["solemn", "anticipatory"],
        "critical": ["noblewoman figure (subtle in background)", "Hoichi tuning biwa", "Heian architecture"]
    },
    11: {
        "narrative": "The Tale of the Heike is sung in a mode between speech and keening, biwa providing percussive counterpoint. Hoichi's voice had been trained since childhood.",
        "shot": {"type": "medium", "angle": "low_angle"},
        "characters": [{"id": "hoichi", "position": "foreground", "action": "playing biwa intensely, sweat on brow", "expression": "absorbed, passionate", "pose": "kneeling, striking strings"}],
        "location": {"description": "supernatural Heian hall with ghostly audience of Heike warriors and court ladies", "time_of_day": "night"},
        "lighting": {"primary_source": "incense haze, ghostly glow", "quality": "moody, dramatic"},
        "emotion": ["intense", "passionate"],
        "critical": ["Hoichi playing biwa with intensity", "sweat on brow", "ghostly audience suggestion"]
    },
    12: {
        "narrative": "At first, low murmur of restrained grief, but as the narrative deepened, they wept openly. Lady Nii's lament, the emperor's fate.",
        "shot": {"type": "close_up", "angle": "eye_level"},
        "characters": [],
        "location": {"description": "ghostly Heian court audience", "time_of_day": "night"},
        "lighting": {"primary_source": "ghostly pale glow", "quality": "supernatural, melancholic"},
        "emotion": ["deep grief", "solemn"],
        "critical": ["court nobles weeping", "spectral warriors in armor", "long flowing silk robes"]
    },
    13: {
        "narrative": "He was asked to perform seven nights. And so night after night he would go, led by the same warrior through the same path.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "hoichi", "position": "center", "action": "walking through night village", "expression": "tired but dutiful", "pose": "walking with biwa"}],
        "location": {"description": "village path at night under moonlight", "time_of_day": "night", "weather": "clear, moonlit"},
        "lighting": {"primary_source": "moonlight", "quality": "silver, mystical"},
        "emotion": ["repetitive", "fated"],
        "critical": ["moonlit night", "Hoichi walking alone", "village silhouettes"]
    },
    14: {
        "narrative": "He would return at dawn, rain-soaked or dew-damp, to take his place in the temple as if nothing had happened.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "hoichi", "position": "center", "action": "returning to temple at dawn", "expression": "peaceful, routine", "pose": "walking toward temple"}],
        "location": {"description": "temple approach at dawn, misty", "time_of_day": "dawn", "weather": "misty morning"},
        "lighting": {"primary_source": "early dawn light", "quality": "soft grey-pink"},
        "emotion": ["peaceful", "cyclical"],
        "critical": ["misty dawn", "Hoichi walking to temple", "peaceful morning atmosphere"]
    },
    15: {
        "narrative": "The priest grew suspicious. Every night the blind Hoichi would leave alone, returning at dawn. What could he be doing?",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [],
        "location": {"description": "temple interior, old priest observing", "time_of_day": "day"},
        "lighting": {"primary_source": "natural daylight through windows", "quality": "dim interior"},
        "emotion": ["suspicious", "concerned"],
        "critical": ["old priest with worried expression", "temple interior", "sense of observation"]
    },
    16: {
        "narrative": "The priest sent temple servants to follow Hoichi on his nightly journey. They would discover the truth.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "hoichi", "position": "distant", "action": "walking ahead", "expression": "unaware", "pose": "walking into distance"}],
        "location": {"description": "night path being followed", "time_of_day": "night", "weather": "dark"},
        "lighting": {"primary_source": "moonlight, distant lanterns", "quality": "dim, suspenseful"},
        "emotion": ["suspenseful", "observational"],
        "critical": ["Hoichi walking unaware", "sense of being followed", "night atmosphere"]
    },
    17: {
        "narrative": "They found him kneeling before the tomb of Emperor Antoku, surrounded by countless ghost fires, playing his biwa for the dead.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "hoichi", "position": "center", "action": "kneeling before tomb playing biwa", "expression": "absorbed, unaware", "pose": "kneeling"}],
        "location": {"description": "Heike cemetery, tomb of Emperor Antoku", "time_of_day": "night", "weather": "rain"},
        "lighting": {"primary_source": "ghost fires floating", "quality": "eerie supernatural glow"},
        "emotion": ["supernatural", "haunting"],
        "critical": ["emperor's tomb", "ghost fires floating", "Hoichi playing unaware"]
    },
    18: {
        "narrative": "The servants watched in terror. Ghostly audience surrounded Hoichi — samurai, nobles, the entire Heike court, weeping at his song.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "playing biwa, unaware", "expression": "absorbed", "pose": "kneeling"}],
        "location": {"description": "Heike cemetery with ghostly audience", "time_of_day": "night", "weather": "rain"},
        "lighting": {"primary_source": "ghost fires", "quality": "supernatural cold"},
        "emotion": ["horror", "witness"],
        "critical": ["ghostly court audience visible", "terrifying scene", "Hoichi unaware of danger"]
    },
    19: {
        "narrative": "The priest confronted Hoichi about what he had seen. Faced with the truth, Hoichi finally confessed.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "being confronted", "expression": "ashamed, afraid", "pose": "kneeling before priest"}],
        "location": {"description": "temple interior, dimly lit", "time_of_day": "day"},
        "lighting": {"primary_source": "oil lamp", "quality": "dim, warm"},
        "emotion": ["confrontation", "shame"],
        "critical": ["Hoichi's confession moment", "dim interior lighting", "sense of judgment"]
    },
    20: {
        "narrative": "The priest knew that sutras written on the body would make Hoichi invisible to the ghosts. He began writing the Heart Sutra on Hoichi's skin.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [],
        "location": {"description": "temple interior, candle light", "time_of_day": "night"},
        "lighting": {"primary_source": "candle light", "quality": "warm, intimate"},
        "emotion": ["ritual", "protection"],
        "critical": ["sutra writing on body", "candle light", "ritual atmosphere"]
    },
    21: {
        "narrative": "Hoichi was covered in sacred text. Every part of his body bore the Heart Sutra, except — they forgot the ears.",
        "shot": {"type": "close_up", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "covered in sutra text", "expression": "solemn, eyes unfocused", "pose": "kneeling"}],
        "location": {"description": "temple interior, candle light", "time_of_day": "night"},
        "lighting": {"primary_source": "candle", "quality": "warm, sacred"},
        "emotion": ["sacred", "vulnerable"],
        "critical": ["sutra text visible on skin", "Hoichi's blind eyes", "candle glow"]
    },
    22: {
        "narrative": "On the seventh night, the warrior returned for Hoichi. But the sutras made him invisible to the ghost.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "samurai_warrior", "position": "center", "action": "returning to temple", "expression": "determined, searching", "pose": "armored figure at door"}],
        "location": {"description": "temple at night", "time_of_day": "night", "weather": "dark"},
        "lighting": {"primary_source": "moonlight", "quality": "cold"},
        "emotion": ["fated", "dangerous"],
        "critical": ["samurai at temple door", "moonlit night", "sense of doom"]
    },
    23: {
        "narrative": "The warrior called for Hoichi but received no answer. The sutra-covered body was invisible to him. He searched in confusion.",
        "shot": {"type": "medium", "angle": "low_angle"},
        "characters": [
            {"id": "samurai_warrior", "position": "foreground", "action": "searching confusedly", "expression": "confused, frustrated", "pose": "looking around"},
            {"id": "hoichi", "position": "background", "action": "sitting silent as instructed", "expression": "terrified but still", "pose": "kneeling motionless"}
        ],
        "location": {"description": "temple hall at night", "time_of_day": "night"},
        "lighting": {"primary_source": "moonlight", "quality": "cold, dramatic"},
        "emotion": ["tension", "supernatural"],
        "critical": ["samurai searching, looking right at Hoichi but not seeing him", "Hoichi sitting perfectly still"]
    },
    24: {
        "narrative": "The warrior searched everywhere. The body was invisible but the ears — the ears had no sutra. There. There they were.",
        "shot": {"type": "medium", "angle": "low_angle"},
        "characters": [{"id": "samurai_warrior", "position": "center", "action": "searching with supernatural focus", "expression": "intense, predatory", "pose": "hunched, searching"}],
        "location": {"description": "temple hall, supernatural darkness", "time_of_day": "night"},
        "lighting": {"primary_source": "supernatural cold glow around samurai", "quality": "eerie"},
        "emotion": ["predatory", "horror building"],
        "critical": ["samurai searching intently", "sense of supernatural detection", "approaching danger"]
    },
    25: {
        "narrative": "There. The ears. Two pale shapes in the darkness. The warrior's hand reached out.",
        "shot": {"type": "close_up", "angle": "low_angle"},
        "characters": [],
        "location": {"description": "extreme close-up of the discovery", "time_of_day": "night"},
        "lighting": {"primary_source": "supernatural glow", "quality": "focused on the ears"},
        "emotion": ["horror", "discovery"],
        "critical": ["extreme close-up", "supernatural focus", "ears visible"]
    },
    26: {
        "narrative": "The warrior's supernatural grip closed on Hoichi's ear. The sutra did not cover it. With supernatural strength, he tore it off.",
        "shot": {"type": "close_up", "angle": "low_angle"},
        "characters": [],
        "location": {"description": "temple hall, the violent moment", "time_of_day": "night"},
        "lighting": {"primary_source": "supernatural glow, moonlight", "quality": "dramatic horror"},
        "emotion": ["horror", "violence"],
        "critical": ["supernatural hand gripping ear", "moment of violence", "Hoichi remaining still"]
    },
    27: {
        "narrative": "Hoichi did not move. Did not cry out. He bore the pain in silence, as the priest had instructed. Blood ran down his neck.",
        "shot": {"type": "close_up", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "enduring pain silently", "expression": "agony controlled, eyes tight shut", "pose": "kneeling motionless"}],
        "location": {"description": "temple hall", "time_of_day": "night"},
        "lighting": {"primary_source": "moonlight", "quality": "cold, focused on face"},
        "emotion": ["agony", "discipline"],
        "critical": ["Hoichi's controlled agony", "blood from ear wound", "stillness despite pain"]
    },
    28: {
        "narrative": "The warrior departed into the night, carrying his trophy. The Heike court followed, their lamenting song fading into the distance.",
        "shot": {"type": "medium", "angle": "wide"},
        "characters": [{"id": "samurai_warrior", "position": "center", "action": "walking away into mist", "expression": "triumphant, cold", "pose": "departing with trophy"}],
        "location": {"description": "temple entrance, misty night", "time_of_day": "night", "weather": "misty"},
        "lighting": {"primary_source": "moonlight through mist", "quality": "ethereal"},
        "emotion": ["departure", "supernatural"],
        "critical": ["samurai fading into mist", "trophy carried", "ethereal departure"]
    },
    29: {
        "narrative": "At dawn, the priest returned. He found Hoichi kneeling, bloodied, ears torn, but alive.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [{"id": "hoichi", "position": "center", "action": "kneeling bloodied", "expression": "exhausted, relieved priest returned", "pose": "kneeling, wounded"}],
        "location": {"description": "temple hall at dawn", "time_of_day": "dawn"},
        "lighting": {"primary_source": "first dawn light", "quality": "soft grey"},
        "emotion": ["relief", "horror aftermath"],
        "critical": ["Hoichi bloodied but alive", "dawn light returning", "sense of aftermath"]
    },
    30: {
        "narrative": "The priest saw the wound and understood immediately — they had forgotten the ears. The sutra was incomplete. He fell to his knees in guilt.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "wounded, being tended", "expression": "suffering", "pose": "kneeling wounded"}],
        "location": {"description": "temple interior, dawn", "time_of_day": "dawn"},
        "lighting": {"primary_source": "dawn light", "quality": "soft"},
        "emotion": ["guilt", "tenderness"],
        "critical": ["priest's realization of mistake", "Hoichi wounded", "tender aftermath"]
    },
    31: {
        "narrative": "The wounds healed. The Heike never returned. Hoichi's fame spread — for his music, and for the strange tale of his missing ears.",
        "shot": {"type": "medium", "angle": "eye_level"},
        "characters": [{"id": "hoichi", "position": "center", "action": "playing biwa again, healed", "expression": "peaceful, content", "pose": "playing in sunlight"}],
        "location": {"description": "temple interior in warm sunlight", "time_of_day": "day", "weather": "clear"},
        "lighting": {"primary_source": "warm sunlight", "quality": "warm, hopeful"},
        "emotion": ["peace", "resolution"],
        "critical": ["Hoichi healed and playing", "warm sunlight", "sense of resolution"]
    },
    32: {
        "narrative": "And so Hoichi the Earless lived out his days at Amida-ji, his strange tale becoming legend. The ghosts were quiet. The music continued.",
        "shot": {"type": "wide", "angle": "wide"},
        "characters": [],
        "location": {"description": "Akama temple at sunset, establishing final shot", "time_of_day": "sunset", "weather": "clear"},
        "lighting": {"primary_source": "golden sunset light", "quality": "warm, peaceful"},
        "emotion": ["peaceful", "finale"],
        "critical": ["temple at sunset", "peaceful atmosphere", "sense of story's end"]
    },
}


def create_shot_yaml(scene_num: int, data: dict) -> str:
    """Generate YAML content for a shot spec."""
    chars_yaml = ""
    if data.get("characters"):
        chars_yaml = "characters:\n"
        for c in data["characters"]:
            chars_yaml += f"  - id: {c['id']}\n"
            chars_yaml += f"    position: {c.get('position', 'midground')}\n"
            chars_yaml += f"    action: \"{c.get('action', '')}\"\n"
            chars_yaml += f"    expression: \"{c.get('expression', '')}\"\n"
            chars_yaml += f"    pose: \"{c.get('pose', '')}\"\n"

    return f"""scene_id: "{scene_num:02d}"
sequence: {scene_num}
narrative_beat: >
  {data['narrative']}

shot:
  type: {data['shot'].get('type', 'medium')}
  angle: {data['shot'].get('angle', 'eye_level')}

{chars_yaml}location:
  description: "{data['location'].get('description', '')}"
  time_of_day: {data['location'].get('time_of_day', 'night')}
  weather: "{data['location'].get('weather', 'clear')}"

lighting:
  primary_source: "{data['lighting'].get('primary_source', 'lantern')}"
  quality: "{data['lighting'].get('quality', 'dim')}"

emotion:
{chr(10).join(f'  - {e}' for e in data.get('emotion', ['melancholic']))}

critical_elements:
{chr(10).join(f'  - {c}' for c in data.get('critical', []))}

provider_hint: nano_banana_openrouter
aspect_ratio: "16:9"
"""


def main():
    # Create YAML files for scenes 1-32
    for num, data in SCENES.items():
        content = create_shot_yaml(num, data)
        filename = SHOTS_DIR / f"{num:02d}_scene.yaml"
        filename.write_text(content, encoding="utf-8")
        print(f"Created {filename.name}")

    print(f"\nCreated {len(SCENES)} shot spec files in {SHOTS_DIR}")


if __name__ == "__main__":
    main()
