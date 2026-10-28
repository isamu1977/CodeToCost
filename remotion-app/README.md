# CodeToCost — Remotion App

Programmatic visuals for the CodeToCost B2B channel (en-US).
Everything on screen is **data-driven**: the Python pipeline emits
`projects/<id>/02_spec/video_spec.json`, and the React components render from it.

## Commands

```console
npm install       # install dependencies
npm run dev       # open Remotion Studio (preview)
npm run check     # typecheck (tsc --noEmit)
npm run build     # bundle
npm run lint      # eslint + tsc
```

## Rendering

```console
# from remotion-app/
npx remotion render <CompositionId> out/<name>.mp4
```

Composition IDs are declared in `src/Root.tsx`.

## The 6 Master Components (target architecture)

Per `MASTER_BLUEPRINT.md` §4, the channel runs on exactly six highly-polished
components. Dark mode, Vercel/Linear/Raycast aesthetic, Tailwind CSS v4 +
Remotion `spring()`, official SVG icons via `simple-icons`.

| Component | Job |
|---|---|
| `<ArchitectureFlow/>` | System boxes connected by animated dashed lines that light up in narration order. |
| `<PricingCurveChart/>` | Animated bar/line chart showing where a cheap tool becomes the expensive one. |
| `<SpecComparisonTable/>` | Side-by-side table where each row enters in sync with audio, marked ✓ / ⚠ / ✗. |
| `<BottleneckCallout/>` | Alert card exposing hidden doc limits (rate limits, 30s timeouts, vendor lock-in). |
| `<BlueprintPromoBanner/>` | Elegant lower-third CTA, ~6s, entering around 35% of the video. |
| `<VerdictMatrix/>` | Closing "Use A if… / Use B if…" matrix that holds retention to the last second. |

## ⚠️ Status of the inherited `src/`

`src/` was copied surgically from the **Red Sun Japan** pipeline as a **scaffold and
pattern reference only**. `SamuraiVideo.tsx`, `ShortsVideo.tsx`, `ChannelIntro.tsx`,
`CinematicOpeningTitle.tsx` and `TitleOverlay.tsx` are coupled to the Japanese
horror-channel content model and **must be replaced** with the six master components
above.

What is genuinely reusable as-is:
- `src/Root.tsx` — composition registration + `calculateMetadata` pattern (keep, rewrite props)
- `src/index.ts`, `src/index.css`, `src/fonts.css` — entry point and styling baseline
- `src/Composition.tsx` — wrapper pattern
- `src/*.woff2` — display fonts
- `remotion.config.ts` — Tailwind v4 enablement via `Config.overrideWebpackConfig`

`public/` holds only `manifest.json` (required by the static import in `Root.tsx`).
Real per-project assets are synced in by the pipeline at render time and are gitignored.
