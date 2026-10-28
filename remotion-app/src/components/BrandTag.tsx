import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';
import { brand, fonts, palette, radii } from '../theme';

export type BrandTagProps = {
  /** Nome exibido. */
  name?: string;
  /** Tagline opcional. */
  tagline?: string;
  /** Posição da tag na tela. */
  position?: 'top-left' | 'top-right';
};

/**
 * BrandTag — camada fixa e discreta de marca.
 *
 * Não compete com o conteúdo: canto superior oposto à área de foco,
 * opacidade baixa, tipografia mono em caixa alta e um pequeno marcador
 * com o gradiente da marca.
 */
export const BrandTag: React.FC<BrandTagProps> = ({
  name = brand.name,
  tagline,
  position = 'top-right',
}) => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [0, 20], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const isLeft = position === 'top-left';

  return (
    <div
      style={{
        position: 'absolute',
        top: 44,
        left: isLeft ? 64 : undefined,
        right: isLeft ? undefined : 64,
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        opacity: opacity * 0.78,
        zIndex: 60,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          width: 12,
          height: 12,
          borderRadius: radii.xs,
          background: `linear-gradient(135deg, ${palette.emerald} 0%, ${palette.cyan} 100%)`,
          boxShadow: `0 0 12px ${palette.emerald}55`,
          flexShrink: 0,
        }}
      />
      <div
        style={{
          fontFamily: fonts.mono,
          fontSize: 19,
          fontWeight: 700,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: palette.text,
        }}
      >
        {name}
      </div>
      {tagline ? (
        <>
          <div
            style={{
              width: 1,
              height: 18,
              background: palette.borderStrong,
            }}
          />
          <div
            style={{
              fontFamily: fonts.sans,
              fontSize: 15,
              fontWeight: 500,
              letterSpacing: '0.02em',
              color: palette.textMuted,
            }}
          >
            {tagline}
          </div>
        </>
      ) : null}
    </div>
  );
};
