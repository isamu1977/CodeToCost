# legacy_rsj — Red Sun Japan (arquivado)

Componentes da antiga linha editorial do canal **Red Sun Japan** (documentário histórico,
narrado em japonês/inglês). Foram movidos para cá em `2026-10-28` durante a refatoração
para o tema **CodeToCost — Arquitetura de Automação, SaaS e Finanças B2B**.

Motivo: manter o histórico do renderer acessível sem poluir a árvore de `src/` do tema
atual. Nenhum arquivo desta pasta é importado por `Root.tsx`.

## Conteúdo

| Arquivo | Descrição |
| --- | --- |
| `SamuraiVideo.tsx` | Composição long-form 16:9 com slideshow Ken Burns, crossfade e legendas por frase. |
| `ShortsVideo.tsx` | Composição vertical 9:16 (Shorts) com legendas cinéticas. |
| `ChannelIntro.tsx` | Bumper de abertura do canal (logo + slideshow). |
| `CinematicOpeningTitle.tsx` | Revelação cinematográfica do título na cena de abertura. |
| `TitleOverlay.tsx` | Marca d'água persistente com título (JA / EN). |
| `fonts.css` | Fontes embutidas (`RedSunKinetic`, `RedSunCinzel`) em base64. |
| `RedSunKinetic.woff2`, `RedSunKinetic.woff2` | Fontes raw (mesma origem do `fonts.css`). |

## Como reativar (se necessário)

1. Copie/ajuste os `import` relativo de `../../public/manifest.json` caso mova os arquivos.
2. Importe as fontes no CSS global do tema atual (`src/index.css`) — o `fonts.css` local usa
   `@font-face` com `src: url(data:...)` e não é carregado automaticamente.
3. Registre a composição em `src/Root.tsx`:

```tsx
import { SamuraiVideo } from './legacy_rsj/SamuraiVideo';

<Composition
  id="RedSunJapanVideo"
  component={SamuraiVideo}
  durationInFrames={1800}
  fps={30}
  width={1920}
  height={1080}
  defaultProps={{ titleJa: '', titleEn: '' }}
/>;
```

> Os componentes legados dependem de assets de imagem/logotipo que **não** existem no
> repositório atual (`public/` só contém `manifest.json` e `.gitkeep`). Reativá-los exige
> restaurar esses assets do projeto original.
