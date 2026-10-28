# CodeToCost

**Programmatic B2B video engine for Automation Architecture, SaaS Cost Engineering and AI Workflows — in English (`en-US`).**

> *Clear Architecture, Real Math, Zero Fluff.*

CodeToCost turns a research question ("what does this tool actually cost at 100k operations?")
into a 5–8 minute video with animated cost curves, architecture diagrams and a clear verdict —
without a screen recording, without a 25-minute tutorial, and without a single number that
wasn't scraped from a live pricing page.

The two governing documents are **[`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md)** (technical,
operational and business specification) and **[`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md)**
(niche definition, editorial boundaries, monetization thesis). **Read both before approving
any topic.**

---

## What this is not

Not a tech-news channel. Not a curiosity channel about AI. Not a beginner tutorial channel.
Every video answers one question a technical decision-maker asks before choosing a tool:
*"How do I architect this cleanly, what will it really cost when volume grows, where are the
hidden traps, and which stack do I pick today?"*

---

## Directory structure

```
CodeToCost/
├── MASTER_BLUEPRINT.md          Business + technical master spec
├── PROJECT_CONTEXT.md           Editorial constitution (niche, boundaries, ICP)
├── agent_workspace/             JingJing's operating area
│   ├── skills/
│   │   └── codetocost-producer.md    Producer role: rules, gates, narrative anatomy
│   ├── phonetic_dictionary.json      TTS normalization for English jargon
│   └── affiliate_catalog.json        Affiliate + own-product links (status: pending)
├── pipeline/
│   ├── core/                    Reusable engines — the real CodeToCost foundation
│   │   ├── tts_engine.py            Multi-provider TTS (StepFun / MiniMax / Kokoro / Qwen)
│   │   ├── whisper_engine.py        faster-whisper word-level timestamps
│   │   ├── llm_client.py            OpenRouter client
│   │   ├── database.py              SQLAlchemy layer
│   │   ├── schemas.py               Data contracts
│   │   ├── scraper.py               Source scraping
│   │   ├── config/                  pronunciation_map.json, social_links.json
│   │   ├── requirements.txt
│   │   └── .env                     Secrets — gitignored, chmod 600
│   ├── rsj_reference/           Red Sun Japan orchestrators — PATTERN MINING ONLY
│   └── .hf_cache/               Symlinks to Kokoro-82M + faster-whisper models
├── projects/
│   └── 001-n8n-vs-make-scale/   One isolated, reproducible folder per video
│       ├── 01_research/             Scraped facts, every number sourced
│       ├── 02_spec/                 video_spec.json (script + component props)
│       ├── 03_audio/                TTS .wav per scene + Whisper timestamps
│       ├── 04_assets/               Logos, backgrounds
│       ├── 05_lead_magnet/          The sellable/downloadable artifact
│       └── output/                  video_final.mp4 + youtube_metadata.md
└── remotion-app/                React/Remotion — the 6 master visual components
```

### `pipeline/rsj_reference/` — read this before importing anything

`main.py` (213 KB), `run_pipeline_complete.py` (95 KB), `redsun/`, `services/` and `prompts/`
were copied from the **Red Sun Japan** pipeline as scaffolding. They are coupled to the
Japanese horror-channel content model — `main.py` imports `redsun.services` and carries
literary-content logic that has no place here. Treat them as **reference for orchestration
patterns only**; strip the content logic before importing anything into `pipeline/core/`.

---

## Workflow — JingJing as orchestrator

JingJing (Hermes Agent, running locally on the MacBook Pro with terminal, filesystem, browser
and persistent memory) is the **production engineer and intelligence analyst**, not a text
generator. She owns five skills, defined in
[`agent_workspace/skills/codetocost-producer.md`](agent_workspace/skills/codetocost-producer.md):

| Skill | Job |
|---|---|
| `skill_market_scraper` | Anti-hallucination engine. Scrapes live `/pricing`, `/limits` and docs before any script exists; saves a dated, sourced `01_research/research_dossier.md`. |
| `skill_cost_simulator` | Python cost math across 3 scale tiers. Produces the numbers that feed the Remotion charts. |
| `skill_script_compiler` | Dossier → `02_video_spec.json`, strictly separating `spoken_text` (phonetically normalized for TTS) from `visual_component` + `component_props`. |
| `skill_pipeline_runner` | Local orchestration: TTS → faster-whisper timestamps → timeline merge → `npx remotion render`. |
| `skill_asset_packager` | Generates the companion asset (docker-compose, JSON workflow, calculator) and the YouTube description with timestamps and mapped affiliate links. |

### The pipeline, end to end

```
01_research/    scrape + dossier          ← every number carries a source URL + date
02_spec/        video_spec.json           ← GATE: Isamu reviews and approves before TTS
03_audio/       TTS .wav per scene → faster-whisper word timestamps
04_assets/      logos, backgrounds, stills
05_lead_magnet/ the sellable artifact
output/         video_final.mp4 + youtube_metadata.md
```

**The gate is real:** TTS never runs before Isamu has approved `02_video_spec.json`.

---

## The 6 master Remotion components

The channel runs on exactly six highly-polished components
(dark mode, Vercel/Linear/Raycast aesthetic, Tailwind v4 + Remotion `spring()`):
`ArchitectureFlow`, `PricingCurveChart`, `SpecComparisonTable`, `BottleneckCallout`,
`BlueprintPromoBanner`, `VerdictMatrix`.

`remotion-app/src/` currently holds the Red Sun Japan components as a **scaffold and pattern
reference**. They must be replaced by the six above. See
[`remotion-app/README.md`](remotion-app/README.md) for exactly what is reusable as-is.

---

## Getting started

```bash
# 1. Python engines
cd pipeline/core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env already exists with real keys and HF_HOME pointed at pipeline/.hf_cache

# 2. Remotion app
cd ../../remotion-app
npm install
npm run check      # typecheck
npm run dev        # Remotion Studio preview
```

### Model cache

`pipeline/.hf_cache/` symlinks to the HuggingFace cache shared with Red Sun Japan, so
Kokoro-82M and faster-whisper do **not** re-download (~595 MB). To make this project
fully self-contained, replace the symlinks with a local download:

```bash
rm -rf pipeline/.hf_cache
mkdir -p pipeline/.hf_cache
HF_HOME=$PWD/pipeline/.hf_cache python -c "from faster_whisper import WhisperModel; WhisperModel('base')"
HF_HOME=$PWD/pipeline/.hf_cache python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
```

---

## Secrets

`pipeline/core/.env` holds live API keys (OpenRouter, StepFun, MiniMax, Gemini) and is
**gitignored and `chmod 600`**. It is never committed. `.env.example` is the tracked
template — keep it in sync when adding a new variable.

## License

Private. Not for redistribution.
