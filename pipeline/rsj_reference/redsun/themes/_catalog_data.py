"""Optional convenience catalog of pre-known themes.

Loaded lazily by `redsun.themes.catalog`. If this module fails to
import (e.g., syntax error, missing dependency), the pipeline
continues to work with empty ThemeMetadata.

Each entry's `bgm_mood_hints` uses generic Japanese terms
(`snow`, `winter`, `mountain`, `river`, `sea`, `temple`) rather
than entity names, so the BGM picker can classify moods for any
theme using the same keyword map.
"""

THEMES: list = [
    {
        "slug": "yuki_onna",
        "name": "Yuki Onna",
        "name_ja": "雪女",
        "category": "kaidan",
        "description": (
            "The Snow Woman — a beautiful spirit who appears during "
            "snowstorms, luring travelers to their frozen death."
        ),
        "atmosphere": "lantern-light-and-fog",
        "visual_keywords": [
            "snow",
            "white kimono",
            "pale woman",
            "blizzard",
            "cedar forest",
            "frozen",
        ],
        "bgm_mood_hints": ["snow", "winter", "mountain"],
    },
    {
        "slug": "kappa",
        "name": "Kappa",
        "name_ja": "河童",
        "category": "yokai",
        "description": (
            "Water-dwelling creatures with a water-filled dish on "
            "their heads, known for dragging children into rivers."
        ),
        "atmosphere": "murky-river-mist",
        "visual_keywords": [
            "river",
            "green creature",
            "turtle shell",
            "water",
            "reeds",
            "swamp",
        ],
        "bgm_mood_hints": ["river", "sea", "summer"],
    },
    {
        "slug": "mujina",
        "name": "Mujina",
        "name_ja": "貉",
        "category": "yokai",
        "description": (
            "A shape-shifting badger spirit that mimics humans and "
            "haunts mountain paths at night."
        ),
        "atmosphere": "misty-forest-path",
        "visual_keywords": [
            "badger",
            "forest",
            "moonlight",
            "shapeshifter",
            "mountain path",
        ],
        "bgm_mood_hints": ["night", "mountain", "fog"],
    },
    {
        "slug": "taira_no_masakado",
        "name": "Taira no Masakado",
        "name_ja": "平将門",
        "category": "samurai",
        "description": (
            "A rebel samurai who declared himself emperor. His "
            "severed head cursed the land."
        ),
        "atmosphere": "cursed-grave",
        "visual_keywords": [
            "severed head",
            "samurai armor",
            "cursed ground",
            "fire",
            "ruins",
        ],
        "bgm_mood_hints": ["war", "samurai", "fire"],
    },
    {
        "slug": "hoichi_the_earless",
        "name": "Hoichi the Earless",
        "name_ja": "耳なし芳一",
        "category": "kaidan",
        "description": (
            "A blind biwa player whose music summoned the ghosts of "
            "the Heike clan."
        ),
        "atmosphere": "temple-darkness",
        "visual_keywords": [
            "blind musician",
            "biwa",
            "ghostly audience",
            "temple",
            "sacred text",
        ],
        "bgm_mood_hints": ["temple", "moonlight", "ghost"],
    },
    {
        "slug": "kuchisake_onna",
        "name": "Kuchisake-onna",
        "name_ja": "口裂け女",
        "category": "showa_legend",
        "description": (
            "The Slit-Mouthed Woman — a modern urban legend asking "
            "'Am I beautiful?' before revealing her mutilated face."
        ),
        "atmosphere": "urban-neon-rain",
        "visual_keywords": [
            "surgical mask",
            "scissors",
            "dark alley",
            "rain",
            "street",
        ],
        "bgm_mood_hints": ["rain", "urban", "ghost"],
    },
    {
        "slug": "aokigahara",
        "name": "Aokigahara",
        "name_ja": "青木ヶ原",
        "category": "mystery",
        "description": (
            "The Sea of Trees at the base of Mount Fuji — a "
            "notorious forest where compasses malfunction and "
            "spirits wander."
        ),
        "atmosphere": "dense-forest-silence",
        "visual_keywords": [
            "dense forest",
            "twisted trees",
            "compass",
            "silence",
            "fog",
        ],
        "bgm_mood_hints": ["mountain", "fog", "forest"],
    },
    {
        "slug": "ronin_47",
        "name": "The 47 Ronin",
        "name_ja": "四十七士の霊",
        "category": "samurai",
        "description": (
            "After their famous revenge, the 47 ronin were forced "
            "to commit seppuku. Their ghosts guard Sengaku-ji Temple."
        ),
        "atmosphere": "temple-graveyard",
        "visual_keywords": [
            "47 graves",
            "temple",
            "incense",
            "katana",
            "seppuku",
        ],
        "bgm_mood_hints": ["temple", "samurai", "snow"],
    },
]