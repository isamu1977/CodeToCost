# Theme-agnostic pipeline

The Red Sun Japan pipeline MUST be theme-agnostic. The pipeline
runs any theme — yokai, kaidan, samurai, mystery, ritual,
non-yokai, or a brand-new uncatalogued slug — without code
changes.

## ThemeMetadata vs EpisodeMetadata

Two distinct data structures:

- `ThemeMetadata` (catalog data, generic, reusable across
  episodes of the same theme): `name`, `description`,
  `atmosphere`, `visual_keywords`, `category`, `source_type`,
  `bgm_mood_hints`. MAY be empty.
- `EpisodeMetadata` (specific to one project): `slug`, `title`,
  `title_ja`, `source_text`, `source_url`, `output_language`,
  `target_keywords`. Always populated.

Prompt construction prefers `ThemeMetadata` for thematic
guidance and `EpisodeMetadata` for identity. The visual planner
MUST fall back to `EpisodeMetadata.title` when
`ThemeMetadata` is empty.

## THEMES_CATALOG is convenience, not whitelist

`motor-python/redsun/themes/_catalog_data.py` is a convenience
preset module. Its presence is optional. The pipeline MUST run
successfully when:

- the catalog is missing,
- the catalog is empty,
- the catalog is broken (`SyntaxError`, etc.),
- the project slug is not in the catalog.

In all of these cases, `ProjectContext.theme_metadata` is
populated as `ThemeMetadata()` (empty) and the pipeline falls
back to generic defaults.

## Production runtime MUST NOT branch on concrete theme names

Production runtime code (phases, services, validators, runner,
FastAPI) MUST NOT branch on concrete theme names or slugs.
Forbidden constructs:

```python
# FORBIDDEN
if slug == "kappa":
    ...
if "yuki-onna" in title.lower():
    ...
slug in {"yuki_onna", "mujina"}
```

Theme-specific keywords MAY appear as generic Japanese terms
(`snow`, `winter`, `mountain`, `river`, `sea`, `temple`) in
`ThemeMetadata.bgm_mood_hints`. These are data, not runtime
conditionals.

The runtime hardcode audit in Phase 12B enforces this rule via
`tests/test_runtime_no_theme_branching.py`.

## Definition of Done (multi-theme)

The pipeline is considered theme-agnostic for delivery when
**three projects of different themes traverse the same
pipeline with zero code changes between runs**:

- a yokai (e.g., `kappa`)
- a kaidan (e.g., `mujina`)
- an uncatalogued non-yokai (e.g., `taira-no-masakado`)

This is verified by `tests/test_theme_agnostic.py` (parametrized
over 4 themes) and by the final Phase 12B integration check.