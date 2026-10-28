# ProjectWorkspace

The pipeline's canonical path layout. Every project-local artifact
MUST be resolved through a `ProjectWorkspace` instance. No phase
composes paths manually.

## Canonical root

```
channel-redsunjapan/projects/<technical_slug>/
```

`<technical_slug>` is derived from the project title via
`slugify_title()` and is **immutable** after `create_project`.
Editing the title later does NOT change the workspace root.

## Layout

```
projects/<slug>/
├── project.json                       # versioned artifact manifest (Phase 5)
├── script.md                          # generate_script output
├── script_reviewed.md                 # review_script output (when revised)
├── narration.txt                      # prepare_narration output
├── audio/
│   ├── main.wav                       # final concatenated narration
│   └── chunks/
│       ├── chunk_001.wav
│       ├── chunk_002.wav
│       └── ...
├── transcript/
│   └── transcript.json                # Whisper word-level JSON
├── visual_plan.json                   # create_visual_plan output
├── images/
│   ├── scene_001_seed43.png           # one per VisualBeat
│   ├── scene_002_seed44.png
│   └── ...
├── video/
│   ├── main.mp4                       # canonical Remotion output
│   └── final.mp4                      # canonical intro+main+ending
├── checkpoints/
│   ├── create_project.json
│   ├── generate_script.json
│   └── ...                            # one per phase
├── logs/
│   └── pipeline.log
├── blog/
│   └── <slug>.<lang>.md
├── shorts/
└── post_kit/
    └── youtube_description.md etc.
```

## Filename templates

Filenames are parametric. They derive from
`ProjectContext.episode.slug` (= `ProjectWorkspace.technical_slug`),
`ProjectContext.image.model`, `seed`, `beat_index`, `chunk_index`.

| Property | Pattern |
|---|---|
| `video_main_path` | `video/<slug>_main.mp4` |
| `video_final_path` | `video/<slug>_final.mp4` |
| `audio_chunk_path(i)` | `audio/chunks/chunk_<i:03d>.wav` |
| `image_path(i, seed)` | `images/scene_<i:03d>_seed<seed>.png` |
| `checkpoint_path(phase)` | `checkpoints/<phase>.json` |

## Construction

```python
from redsun.workspace import ProjectWorkspace

ws = ProjectWorkspace.from_slug("kappa")
ws.create()                                   # idempotent; never overwrites

# Alternative constructors
ws = ProjectWorkspace.from_title("Kappa Test")            # slugifies
ws = ProjectWorkspace.from_legacy(project_id=5, slug="kappa")  # probes legacy paths
```

## Legacy fallback (read-only)

`ProjectWorkspace.from_legacy(pid, slug)` probes the old
`motor-python/storage/<slug>/` and `motor-python/storage/<id>/`
trees. The probe is cached for the workspace's lifetime. The
legacy files are never modified.

Migration from legacy to canonical happens in two places:

- `CheckpointStore.migrate_legacy(project_id)` copies
  `*.done` files into `checkpoints/<phase>.json` (one-shot).
- `phase_concat_final` (future) will move
  `render-remotion/<slug>_main.mp4` into
  `projects/<slug>/video/`.

## Production runtime MUST NOT branch on concrete theme names

Filename templates MUST be parametric. Hardcoded theme
literals (`kappa_*.mp4`, `yuki_*.wav`, etc.) are forbidden and
detected by the runtime hardcode audit in Phase 12B.