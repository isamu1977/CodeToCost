# CodeToCost — Remotion App

Programmatic visuals for the **CodeToCost** B2B channel (en-US).
Everything on screen is **data-driven**: the pipeline emits a `timeline.json`
(the render-ready projection of `02_video_spec.json`), and the React components
render from it.

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
npx remotion render MainComposition out/<name>.mp4
npx remotion still  MainComposition out/frame.png --frame=300
```

Composition IDs are declared in `src/Root.tsx` (currently: `MainComposition`).

## Architecture

```
src/
  Root.tsx              Composição registrada (mantido limpo)
  MainComposition.tsx   Componente mestre: monta as cenas + camadas fixas
  types.ts              Contrato TypeScript do timeline.json
  theme.ts              Design tokens (paleta, tipografia, espaçamento)
  mock_timeline.json    Timeline fictício (vídeo 001 · n8n vs Make)
  components/
    BrandTag.tsx            Camada fixa de marca (canto superior)
    CaptionOverlay.tsx      Legenda dinâmica alinhada ao Whisper
    TitleScene.tsx          Abertura / encerramento
    SpecComparisonTable.tsx Tabela comparativa (Make vs n8n vs Zapier)
    CostExplosionChart.tsx  Custo por patamar de volume
    ArchitectureFlow.tsx    Diagrama de blocos (webhook → VPS → Postgres → APIs)
  legacy_rsj/           Linha antiga "Red Sun Japan" (arquivada, fora do lint)
```

### `timeline.json` — contrato

Definido em `src/types.ts`. Raiz:

| Campo | Tipo | Descrição |
|---|---|---|
| `version` | `number` | Versão do contrato. |
| `videoId` / `title` | `string` | Identidade do vídeo. |
| `fps` / `width` / `height` | `number` | Dimensões da composição. |
| `durationInFrames` | `number` | Duração total. |
| `audioFilename` | `string?` | Narração em `public/`. |
| `brand` | `BrandConfig?` | `{ name, tagline? }` da camada de marca. |
| `scenes` | `Scene[]` | Cenas em ordem cronológica. |
| `words` | `WordTimestamp[]?` | Timestamps de palavra do Whisper (alimentam a legenda). |

`Scene` = `{ id, startFrame, durationInFrames, type, props }`.
`ComponentProps` é uma união discriminada por `kind`
(`title` · `spec-comparison` · `cost-chart` · `architecture` · `outro`),
e `Scene.type` espelha esse discriminante.

Convenções: tempos de fala em **segundos**; tempos de cena em **frames**;
coordenadas do diagrama em **pixels** do canvas interno (1560×620).

### Mapeamento a partir do `02_video_spec.json`

O blueprint (`MASTER_BLUEPRINT.md` §3) define o contrato de produção; o
`timeline.json` é a projeção de render. O merge (pipeline Python) resolve:

| `02_video_spec.json` | `timeline.json` |
|---|---|
| `video_id` | `videoId` |
| `scenes[].scene_id` | `scenes[].id` |
| `scenes[].visual_component` | `scenes[].type` (+ `props.kind`) |
| `scenes[].component_props` | `scenes[].props` |
| `scenes[].display_subtitle` | `words[]` (agregado do Whisper) |
| — | `scenes[].startFrame` / `durationInFrames` (calculados do áudio) |

### Tema

Dark mode slate (`src/theme.ts`):

| Token | Valor | Uso |
|---|---|---|
| `palette.bg` | `#0B0F19` | Fundo principal |
| `palette.bgDeep` | `#090D16` | Rodapés / placas |
| `palette.surface` | `#111827` | Cards e containers |
| `palette.border` / `borderStrong` | `#1F2937` / `#374151` | Bordas sutis / fortes |
| `palette.emerald` | `#10B981` | Economias e sucesso |
| `palette.cyan` / `blue` | `#06B6D4` / `#3B82F6` | APIs, cloud e infraestrutura |
| `palette.rose` | `#F43F5E` | Custos altos, erros e risco |
| `palette.amber` | `#F59E0B` | Limites e avisos |

## Linha legada (`src/legacy_rsj/`)

Componentes da antiga linha editorial **Red Sun Japan**, movidos para fora de
`src/` em 2026-10-28 e excluídos do lint. Nenhum é registrado em `Root.tsx`.
Ver `src/legacy_rsj/README.md` para o inventário e as instruções de reativação.

## Notas

- `public/` contém apenas `manifest.json` (seed). Os assets reais de cada vídeo
  são sincronizados pelo pipeline no momento do render e são gitignored.
- `src/index.css` carrega Inter + JetBrains Mono (Google Fonts) e o Tailwind v4
  (`remotion.config.ts` habilita o plugin via `enableTailwind()`).
