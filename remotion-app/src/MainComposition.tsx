import React, { useMemo } from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import { ComponentProps, Scene, Timeline, findSceneAtFrame } from './types';
import { brand, fonts, palette } from './theme';
import { BrandTag } from './components/BrandTag';
import { CaptionOverlay } from './components/CaptionOverlay';
import { SpecComparisonTable } from './components/SpecComparisonTable';
import { CostExplosionChart } from './components/CostExplosionChart';
import { ArchitectureFlow } from './components/ArchitectureFlow';
import { OutroScene, TitleScene } from './components/TitleScene';

export type MainCompositionProps = {
  /** Contrato completo do `timeline.json`. */
  timeline: Timeline;
};

/**
 * SceneRenderer — despacha a cena para o componente de negócio correspondente.
 * O `kind` de `ComponentProps` é o discriminante da união.
 */
const SceneRenderer: React.FC<{ props: ComponentProps }> = ({ props }) => {
  switch (props.kind) {
    case 'title':
      return <TitleScene {...props} />;
    case 'spec-comparison':
      return <SpecComparisonTable {...props} />;
    case 'cost-chart':
      return <CostExplosionChart {...props} />;
    case 'architecture':
      return <ArchitectureFlow {...props} />;
    case 'outro':
      return <OutroScene {...props} />;
    default:
      return null;
  }
};

/**
 * MainComposition — composição mestre do canal CodeToCost.
 *
 * Recebe o `timeline.json` via props, monta cada cena dentro de um `Sequence`
 * (respeitando os frames calculados a partir dos timestamps do Whisper) e
 * sobrepõe duas camadas fixas:
 *
 *   1. `BrandTag` — marca discreta no canto superior;
 *   2. `CaptionOverlay` — legenda dinâmica alinhada à narração.
 *
 * As transições entre cenas são apenas um fade-in vertical suave — sem cortes
 * secos e sem estética de short-form.
 */
export const MainComposition: React.FC<MainCompositionProps> = ({ timeline }) => {
  const frame = useCurrentFrame();

  const scenes = useMemo(() => timeline.scenes || [], [timeline.scenes]);
  const brandConfig = timeline.brand ?? brand;
  const words = timeline.words || [];

  /** Alguma cena traz a própria narração? (senão vale a trilha mestre) */
  const hasSceneAudio = useMemo(
    () => scenes.some((scene) => Boolean(scene.audioFile)),
    [scenes],
  );

  const activeScene: Scene | null = useMemo(
    () => findSceneAtFrame(scenes, frame),
    [scenes, frame],
  );

  // Fade-in inicial do conteúdo (evita "pop" no frame 0).
  const introFade = interpolate(frame, [0, 16], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: palette.bg,
        fontFamily: fonts.sans,
        color: palette.text,
      }}
    >
      {/* Fundo: gradiente radial + grade sutil */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(1200px 700px at 18% 8%, rgba(6,182,212,0.10) 0%, rgba(11,15,25,0) 62%),
                       radial-gradient(1000px 640px at 86% 92%, rgba(16,185,129,0.08) 0%, rgba(11,15,25,0) 60%)`,
        }}
      />
      <AbsoluteFill
        style={{
          backgroundImage:
            'linear-gradient(rgba(31,41,55,0.35) 1px, transparent 1px), linear-gradient(90deg, rgba(31,41,55,0.35) 1px, transparent 1px)',
          backgroundSize: '80px 80px',
          opacity: 0.35,
        }}
      />

      {/* Cenas — cada uma no seu intervalo de frames */}
      <AbsoluteFill style={{ opacity: introFade }}>
        {scenes.map((scene) => (
          <Sequence
            key={scene.id}
            from={scene.startFrame}
            durationInFrames={scene.durationInFrames}
          >
            {/* Narração da cena — o WAV vive em `public/<project>/` e é
                endereçado por `staticFile()`. Sem este `<Audio>` a cena é
                muda (bug do render silencioso). */}
            {scene.audioFile ? <Audio src={staticFile(scene.audioFile)} /> : null}
            <SceneRenderer props={scene.props} />
          </Sequence>
        ))}
      </AbsoluteFill>

      {/* Camada fixa de marca */}
      <BrandTag name={brandConfig.name} tagline={brandConfig.tagline} />

      {/* Legenda dinâmica (Whisper) */}
      {words.length > 0 ? (
        <CaptionOverlay words={words} accent="cyan" />
      ) : null}

      {/* Progresso da cena atual — 2px no rodapé, discreto */}
      {activeScene ? (
        <div
          style={{
            position: 'absolute',
            left: 0,
            bottom: 0,
            height: 2,
            width: `${(frame / Math.max(timeline.durationInFrames, 1)) * 100}%`,
            background: `linear-gradient(90deg, ${palette.emerald} 0%, ${palette.cyan} 100%)`,
            opacity: 0.7,
            zIndex: 58,
          }}
        />
      ) : null}

      {/* Trilha mestre — usada apenas quando as cenas não trazem o próprio
          áudio (evita duplicar a narração quando `audioFile` existe). */}
      {timeline.audioFilename && !hasSceneAudio ? (
        <Audio src={staticFile(timeline.audioFilename)} />
      ) : null}
    </AbsoluteFill>
  );
};
