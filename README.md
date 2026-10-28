# CodeToCost

> **Clear Architecture. Real Math. Zero Fluff.**  
> A deterministic programmatic engine for producing technical B2B video intelligence on software architecture, SaaS unit economics, and production-grade workflows.

---

## 💡 What is CodeToCost?

**CodeToCost** is a developer-first media and intelligence platform built for software engineers, micro-SaaS founders, and automation agency leaders.

Instead of bloated 30-minute click-through tutorials or surface-level marketing reviews, CodeToCost delivers concise, high-density 5-to-8 minute visual breakdowns analyzing:
- **Scale Math:** Uncovering the real inflection points where managed SaaS billing explodes compared to self-hosted alternatives.
- **Architectural Blueprints:** Production-ready architectures for AI agents, queue workers, self-hosted Docker stacks, and serverless databases.
- **Unit Economics:** Side-by-side cost comparisons, hidden API rate limits, and vendor lock-in trade-offs across Starter, Growth, and Scale tiers.

---

## ⚙️ The Engine Architecture

The channel operates on an automated, deterministic local pipeline running on Apple Silicon (macOS):
- **Intelligence & Fact-Checking:** Autonomous research, documentation scraping, and cost simulations orchestrated via local agents (Hermes Agent / JingJing).
- **Audio & Acoustic Timing:** Neural voice synthesis via StepFun TTS with phonetic technical normalization, mapped frame-by-frame via Faster-Whisper.
- **Kinetic Visualization:** 100% code-driven animated charts, flow diagrams, and pricing tables rendered programmatically in React via Remotion.
- **Operator Dashboard:** A local Streamlit control plane for script auditing, multi-scene audio previewing, and one-click rendering.

---

## 📁 Repository Structure

```text
CodeToCost/
├── MASTER_BLUEPRINT.md          # Technical, operational & business master spec
├── PROJECT_CONTEXT.md           # Editorial constitution (niche, boundaries, ICP)
├── agent_workspace/             # Agent skills, phonetic dictionary & affiliate catalog
│   ├── skills/
│   │   └── codetocost-producer.md   # Producer role: rules, gates, narrative anatomy
│   ├── phonetic_dictionary.json     # TTS normalization for English jargon
│   └── affiliate_catalog.json       # Affiliate + own-product links
├── pipeline/
│   ├── core/                    # Reusable TTS, Whisper & Scraping engines
│   │   ├── tts_engine.py            # Multi-provider TTS (StepFun / MiniMax / Kokoro / Qwen)
│   │   ├── stepfun_client.py        # StepFun StepAudio client (production TTS)
│   │   ├── whisper_engine.py        # faster-whisper word-level timestamps
│   │   ├── llm_client.py            # OpenRouter client
│   │   ├── scraper.py               # Source scraping
│   │   ├── pronunciation.py         # Phonetic normalization for spoken text
│   │   ├── database.py              # SQLAlchemy layer
│   │   ├── schemas.py               # Data contracts
│   │   ├── workspace.py             # Project workspace helpers
│   │   ├── config/                  # pronunciation_map.json, social_links.json, voices
│   │   ├── requirements.txt
│   │   └── .env.example             # Tracked template — real .env is gitignored
│   ├── rsj_reference/           # Red Sun Japan orchestrators — PATTERN MINING ONLY
│   ├── dashboard.py             # Local Streamlit operator control room
│   └── build_pipeline.py        # Deterministic timeline & audio orchestrator
├── projects/                    # Isolated project workspaces per video
│   └── 001-n8n-vs-make-scale/   # 01_research → 05_lead_magnet → output
└── remotion-app/                # React/Remotion application & B2B visual components
    └── src/
        ├── MainComposition.tsx      # Root composition wiring
        ├── components/              # ArchitectureFlow, CostExplosionChart, ...
        └── legacy_rsj/              # Red Sun Japan scaffold — reference only
```

---

## 🧭 The Deterministic Pipeline

Every video follows the same reproducible six-stage contract. Each stage writes to an
isolated folder under `projects/`, and the source of truth is always the **spec** — never
the rendered media.

```text
01_research/    scrape + dossier          ← every number carries a source URL + date
02_spec/        video_spec.json           ← GATE: spec is reviewed and approved before TTS
03_audio/       TTS .wav per scene → faster-whisper word timestamps
04_assets/      logos, backgrounds, stills
05_lead_magnet/ the sellable companion artifact
output/         video_final.mp4 + youtube_metadata.md
```

**The gate is real:** TTS never runs before the video spec has been reviewed and approved.
Generated media (`03_audio/`, `output/`) is never committed — only the `.gitkeep`
placeholders are tracked so the folder structure survives a fresh clone.

### Orchestration

The local agent (Hermes Agent / JingJing) is the **production engineer and intelligence
analyst**, not a text generator. It owns five skills, defined in
[`agent_workspace/skills/codetocost-producer.md`](agent_workspace/skills/codetocost-producer.md):

| Skill | Job |
|---|---|
| `skill_market_scraper` | Anti-hallucination engine. Scrapes live `/pricing`, `/limits` and docs before any script exists; saves a dated, sourced research dossier. |
| `skill_cost_simulator` | Python cost math across 3 scale tiers. Produces the numbers that feed the Remotion charts. |
| `skill_script_compiler` | Dossier → `video_spec.json`, strictly separating `spoken_text` (phonetically normalized for TTS) from `visual_component` + `component_props`. |
| `skill_pipeline_runner` | Local orchestration: TTS → faster-whisper timestamps → timeline merge → Remotion render. |
| `skill_asset_packager` | Generates the companion asset (docker-compose, JSON workflow, calculator) and the YouTube description with timestamps and mapped affiliate links. |

---

## 🎨 Master Visual Components

The channel runs on a small set of highly-polished React/Remotion components
(dark mode, technical B2B aesthetic, Tailwind v4 + Remotion `spring()`):

| Component | Purpose |
|---|---|
| `ArchitectureFlow` | Animated service topology and request-routing diagrams |
| `CostExplosionChart` | Log-scale cost curves across Starter / Growth / Scale tiers |
| `SpecComparisonTable` | Feature and limit matrix with progressive reveal |
| `CaptionOverlay` | Frame-accurate burned-in captions driven by Whisper timestamps |
| `TitleScene` | Cold-open title sequence and chapter cards |
| `BrandTag` | Lower-third brand and handle overlay |

`remotion-app/src/legacy_rsj/` holds the Red Sun Japan components as a **scaffold and
pattern reference**. See [`remotion-app/README.md`](remotion-app/README.md) for exactly
what is reusable as-is.

---

## 🚀 Getting Started

### 1. Python engines

```bash
cd pipeline/core
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then fill in your API keys
```

### 2. Remotion app

```bash
cd ../../remotion-app
npm install
npm run check              # typecheck
npm run dev                # Remotion Studio preview
```

### 3. Operator dashboard

```bash
./run_dashboard.sh          # Streamlit control room at http://localhost:8501
```

### Model cache

`pipeline/.hf_cache/` is a local cache for Kokoro-82M and faster-whisper, so models do
**not** re-download (~595 MB). Point `HF_HOME` at it inside `.env` to reuse it.

---

## 🔐 Secrets & Repository Safety

Real API keys (OpenRouter, StepFun, MiniMax, Gemini) live in `pipeline/core/.env`, which
is **gitignored** and never committed. `pipeline/core/.env.example` is the tracked
template — keep it in sync when adding a new variable.

The root [`.gitignore`](.gitignore) is deliberately defensive and blocks:

- **Secrets:** `.env`, `**/.env`, `*.env*`, `token.json`, `client_secret.json`, `*.pem`,
  `*.key`, `.streamlit/secrets.toml`
- **Generated media:** `*.mp4`, `*.wav`, `*.mp3`, and the contents of
  `projects/**/03_audio/` and `projects/**/output/` (only `.gitkeep` is tracked)
- **Runtime state:** `node_modules/`, `.venv/`, `**/__pycache__/`, `*.pyc`,
  `pipeline/.hf_cache/`, `.DS_Store`

Verify before every commit:

```bash
git check-ignore -v pipeline/core/.env
git check-ignore -v projects/001-n8n-vs-make-scale/03_audio/voice_test_stepfun.wav
```

Both commands must print the matching `.gitignore` rule.

---

## 📄 Governance

The two governing documents are **[`MASTER_BLUEPRINT.md`](MASTER_BLUEPRINT.md)** (technical,
operational and business specification) and **[`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md)**
(niche definition, editorial boundaries, monetization thesis). **Read both before approving
any topic.**

---

## 📜 License

Private. Not for redistribution.
