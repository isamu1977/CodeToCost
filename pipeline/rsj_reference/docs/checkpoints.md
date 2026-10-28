# Hash-based checkpoints

Replaces the legacy `phase.done` filename convention with
versioned JSON files.

## File layout

```
projects/<slug>/checkpoints/<phase>.json
```

Each file is a JSON document with:

```json
{
  "schema_version": 1,
  "phase": "generate_audio",
  "status": "completed",
  "completed_at": "2026-08-10T13:25:32.094340+00:00",
  "input_hash": "sha256:...",
  "config_hash": "sha256:...",
  "output_hash": "sha256:...",
  "validation": {
    "audio": {"ok": true, ...}
  },
  "data": {
    "duration_sec": 795.35
  }
}
```

## Hashes

| Field | What it covers |
|---|---|
| `input_hash` | SHA-256 of every upstream artifact that produced this phase's output. For `generate_audio`, it is the SHA-256 of `narration.txt`. |
| `config_hash` | SHA-256 of the relevant `ProjectContext` slice. For `generate_audio`, it is the SHA-256 of `context.tts` (engine, voice, instruct, language, ...). |
| `output_hash` | SHA-256 of every artifact this phase produced. For `generate_audio`, it is the SHA-256 of `audio/main.wav`. |

## Resume behaviour

`--resume` compares persisted hashes to current inputs/configs.
A phase is skipped only if:

- the checkpoint file exists
- the checkpoint has `status == "completed"`
- `input_hash` matches the current input
- `config_hash` matches the current config

If any of these fail, the phase is re-run automatically.

## Downstream invalidation

A change in `<phase>` invalidates every phase reachable from
`<phase>` in the dependency graph:

```
script changed         → narration → audio → transcript → visual plan → manifest → render
voice changed         → audio → transcript → visual plan → manifest → render
audio changed         → transcript → visual plan → manifest → render
images regenerated    → manifest → render
```

The dependency graph is `redsan.pipeline.checkpoints.DEPENDENCIES`.
The cascade runs in `CheckpointStore.invalidate_downstream(phase)`.

## Legacy migration

`motor-python/storage/<slug>/checkpoints/<phase>.done` files are
migrated on first read by `CheckpointStore.migrate_legacy(pid)`.
Each `.done` is copied (not moved) to the new
`projects/<slug>/checkpoints/<phase>.json`. Originals are never
deleted. Migrated files are marked with
`validation: {legacy_unverified: true}` so the resume logic does
not force a re-run for legacy phases until inputs are proven
different.

## Operational commands

```python
from redsun.pipeline.checkpoints import CheckpointStore
from redsun.workspace import ProjectWorkspace

ws = ProjectWorkspace.from_slug("kappa")
store = CheckpointStore(ws)

# Manual reset of one phase
store.invalidate("generate_audio")
store.invalidate_downstream("generate_audio")

# Inspect checkpoint
ck = store.read("generate_audio")
print(ck.status, ck.completed_at)
```