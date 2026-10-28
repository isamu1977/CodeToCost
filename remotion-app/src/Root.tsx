import React from 'react';
import { Composition } from 'remotion';
import { MainComposition } from './MainComposition';
import { Timeline } from './types';
import mockTimelineJson from './mock_timeline.json';

/**
 * Root — registro das composições do canal CodeToCost.
 *
 * Fica propositalmente limpo: apenas a composição principal do tema atual.
 *
 * A linha editorial antiga (Red Sun Japan) foi arquivada em `src/legacy_rsj/` —
 * ver `src/legacy_rsj/README.md` para reativá-la.
 */

/**
 * O `resolveJsonModule` do TypeScript amplia strings para `string`, o que
 * quebraria a união discriminada `ComponentProps`. O cast restaura o
 * contrato tipado do `timeline.json`.
 */
const mockTimeline = mockTimelineJson as unknown as Timeline;

/**
 * Metadados da composição derivados do `timeline` injetado via `--props`.
 *
 * Sem isto, a composição ficaria travada na duração do mock (60s) mesmo
 * quando o pipeline gera uma timeline de outro tamanho.
 */
const calculateMetadata = ({ props }: { props: Record<string, unknown> }) => {
  const timeline = (props as { timeline?: Timeline }).timeline;
  if (!timeline) {
    return {
      durationInFrames: mockTimeline.durationInFrames,
      fps: mockTimeline.fps,
      width: mockTimeline.width,
      height: mockTimeline.height,
    };
  }
  return {
    durationInFrames: timeline.durationInFrames || mockTimeline.durationInFrames,
    fps: timeline.fps || mockTimeline.fps,
    width: timeline.width || mockTimeline.width,
    height: timeline.height || mockTimeline.height,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="MainComposition"
      component={MainComposition}
      durationInFrames={mockTimeline.durationInFrames}
      fps={mockTimeline.fps}
      width={mockTimeline.width}
      height={mockTimeline.height}
      defaultProps={{ timeline: mockTimeline }}
      calculateMetadata={calculateMetadata}
    />
  );
};
