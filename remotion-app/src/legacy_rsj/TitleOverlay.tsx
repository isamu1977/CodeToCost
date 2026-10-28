import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

export interface TitleOverlayProps {
  titleJa?: string;
  titleEn?: string;
  frame?: number;
  endScreenStartFrame?: number;
}

export function formatDisplayTitle(raw?: string): string {
  if (!raw) return '';
  const trimmed = raw.trim();
  // If raw is a hyphenated slug with no spaces, convert hyphens to spaces and title case
  if (trimmed.includes('-') && !trimmed.includes(' ')) {
    return trimmed
      .split('-')
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }
  return trimmed;
}

export const TitleOverlay: React.FC<TitleOverlayProps> = ({
  titleJa,
  titleEn,
  frame: explicitFrame,
  endScreenStartFrame,
}) => {
  const currentFrame = explicitFrame ?? useCurrentFrame();

  // A marca d'água persistente do canto superior esquerdo (title_ja + title_en pequenos)
  // DEVE permanecer com opacity: 0 do Frame 0 até o Frame 435 (14.5s).
  // Entre Frame 435 (14.5s) e Frame 480 (16.0s), a marca d'água faz fade-in suave (opacity: 0 -> 0.75)
  // e permanece ativa até os últimos 15 segundos do vídeo (onde dá lugar à Tela Final Atmosférica).
  const WATERMARK_START = 435; // 14.5s
  const WATERMARK_FULL = 480; // 16.0s

  if (currentFrame < WATERMARK_START) {
    return null;
  }

  if (endScreenStartFrame !== undefined && currentFrame >= endScreenStartFrame) {
    return null;
  }

  let opacity = 0.75;
  if (currentFrame < WATERMARK_FULL) {
    opacity = interpolate(currentFrame, [WATERMARK_START, WATERMARK_FULL], [0, 0.75], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  } else if (endScreenStartFrame !== undefined && currentFrame > endScreenStartFrame - 30) {
    opacity = interpolate(currentFrame, [endScreenStartFrame - 30, endScreenStartFrame], [0.75, 0], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  }

  const displayTitleJa = titleJa ? titleJa.trim() : '';
  const displayTitleEn = formatDisplayTitle(titleEn);

  if (!displayTitleJa && !displayTitleEn) return null;

  return (
    <div
      style={{
        position: 'absolute',
        top: 36,
        left: 44,
        opacity,
        pointerEvents: 'none',
        maxWidth: '60%',
        display: 'flex',
        flexDirection: 'column',
        gap: '4px',
        zIndex: 50,
      }}
    >
      {/* Linha 1 (Superior): Título em Japonês autêntico (title_ja), Noto Serif JP, 24px, opacidade 0.9, branca/marfim */}
      {displayTitleJa && (
        <div
          style={{
            fontFamily: '"Noto Serif JP", "Hiragino Mincho ProN", "Yu Mincho", serif',
            fontSize: '24px',
            fontWeight: 700,
            color: '#F8F6F0',
            opacity: 0.9,
            lineHeight: 1.3,
            letterSpacing: '0.1em',
            textShadow: '0 2px 12px rgba(0,0,0,0.95), 0 0 5px rgba(0,0,0,0.9)',
          }}
        >
          {displayTitleJa}
        </div>
      )}

      {/* Linha 2 (Inferior): Título em Romaji/Inglês (title_en), 14px, tracking 0.15em, âmbar suave (#e5a93c) */}
      {displayTitleEn && (
        <div
          style={{
            fontFamily: '"Cinzel", "Georgia", "Times New Roman", serif',
            fontSize: '14px',
            fontWeight: 600,
            color: '#E5A93C',
            opacity: 0.95,
            letterSpacing: '0.15em',
            textTransform: 'uppercase',
            lineHeight: 1.25,
            textShadow: '0 2px 8px rgba(0,0,0,0.95)',
          }}
        >
          {displayTitleEn}
        </div>
      )}
    </div>
  );
};
