import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { OutroProps, TitleProps } from '../types';
import { fonts, palette, radii } from '../theme';

export type TitleSceneProps = TitleProps;

/**
 * TitleScene — abertura do vídeo.
 * Kicker em mono + título grande + destaque opcional.
 */
export const TitleScene: React.FC<TitleSceneProps> = ({
  title,
  subtitle,
  kicker,
  highlight,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const rise = spring({
    frame,
    fps,
    config: { damping: 200, mass: 0.8 },
    durationInFrames: 28,
  });

  const rule = interpolate(frame, [10, 34], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 22,
        padding: '0 140px',
      }}
    >
      {kicker ? (
        <div
          style={{
            fontFamily: fonts.mono,
            fontSize: 17,
            fontWeight: 600,
            letterSpacing: '0.28em',
            textTransform: 'uppercase',
            color: palette.cyan,
            opacity: interpolate(frame, [0, 18], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {kicker}
        </div>
      ) : null}

      <div
        style={{
          fontFamily: fonts.sans,
          fontSize: 78,
          fontWeight: 800,
          letterSpacing: '-0.03em',
          lineHeight: 1.08,
          textAlign: 'center',
          color: palette.text,
          opacity: rise,
          transform: `translateY(${(1 - rise) * 26}px)`,
        }}
      >
        {title}
      </div>

      <div
        style={{
          width: 320 * rule,
          height: 3,
          borderRadius: radii.pill,
          background: `linear-gradient(90deg, ${palette.emerald} 0%, ${palette.cyan} 100%)`,
        }}
      />

      {subtitle ? (
        <div
          style={{
            fontFamily: fonts.sans,
            fontSize: 28,
            fontWeight: 400,
            color: palette.textMuted,
            textAlign: 'center',
            opacity: interpolate(frame, [14, 30], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {subtitle}
        </div>
      ) : null}

      {highlight ? (
        <div
          style={{
            marginTop: 8,
            padding: '14px 26px',
            borderRadius: radii.md,
            border: `1px solid ${palette.border}`,
            background: palette.surface,
            fontFamily: fonts.mono,
            fontSize: 24,
            fontWeight: 700,
            color: palette.emerald,
            opacity: interpolate(frame, [22, 40], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {highlight}
        </div>
      ) : null}
    </div>
  );
};

export type OutroSceneProps = OutroProps;

/**
 * OutroScene — encerramento com chamada para ação.
 */
export const OutroScene: React.FC<OutroSceneProps> = ({
  title,
  subtitle,
  cta,
  highlight,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const rise = spring({
    frame: Math.max(0, frame - 4),
    fps,
    config: { damping: 200, mass: 0.8 },
    durationInFrames: 26,
  });

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 20,
        padding: '0 140px',
      }}
    >
      <div
        style={{
          fontFamily: fonts.sans,
          fontSize: 66,
          fontWeight: 800,
          letterSpacing: '-0.03em',
          textAlign: 'center',
          color: palette.text,
          opacity: rise,
          transform: `translateY(${(1 - rise) * 22}px)`,
        }}
      >
        {title}
      </div>

      {subtitle ? (
        <div
          style={{
            fontFamily: fonts.sans,
            fontSize: 26,
            color: palette.textMuted,
            textAlign: 'center',
            opacity: interpolate(frame, [12, 28], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {subtitle}
        </div>
      ) : null}

      {highlight ? (
        <div
          style={{
            fontFamily: fonts.mono,
            fontSize: 26,
            fontWeight: 700,
            color: palette.emerald,
            opacity: interpolate(frame, [20, 36], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {highlight}
        </div>
      ) : null}

      {cta ? (
        <div
          style={{
            marginTop: 10,
            padding: '16px 32px',
            borderRadius: radii.pill,
            background: palette.surfaceRaised,
            border: `1px solid ${palette.borderStrong}`,
            fontFamily: fonts.sans,
            fontSize: 22,
            fontWeight: 600,
            color: palette.text,
            opacity: interpolate(frame, [28, 44], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
          }}
        >
          {cta}
        </div>
      ) : null}
    </div>
  );
};
