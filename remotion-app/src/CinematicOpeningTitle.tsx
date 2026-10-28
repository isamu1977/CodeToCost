import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

export interface ParsedStoryTitle {
  mainTitleEn: string;
  subTitleEn: string;
}

/**
 * Splits title_en into mainTitleEn and optional subTitleEn if it contains ':' or '—' / ' - '.
 * Example: "The Peony Lantern: The 1666 Edo Ghost Story" ->
 *   mainTitleEn: "THE PEONY LANTERN"
 *   subTitleEn: "The 1666 Edo Ghost Story"
 * If no separator exists, mainTitleEn receives the whole title uppercase and subTitleEn is empty.
 */
export function parseStoryTitle(raw?: string): ParsedStoryTitle {
  if (!raw) return { mainTitleEn: '', subTitleEn: '' };
  const trimmed = raw.trim();

  // If slug with hyphens and no spaces, convert to words
  const clean =
    trimmed.includes('-') && !trimmed.includes(' ')
      ? trimmed
          .split('-')
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(' ')
      : trimmed;

  const match = clean.match(/^([^:—]+)(?:[:—]|\s+-\s+)(.+)$/);
  if (match) {
    return {
      mainTitleEn: match[1].trim().toUpperCase(),
      subTitleEn: match[2].trim(),
    };
  }

  return {
    mainTitleEn: clean.toUpperCase(),
    subTitleEn: '',
  };
}

export interface CinematicOpeningTitleProps {
  titleJa?: string;
  titleEn?: string;
  frame?: number;
}

/**
 * CinematicStoryTitleReveal:
 * Plays between Frame 150 (00:05) and Frame 420 (00:14) at 30fps over the opening scene.
 * - Entry (fade-in + 12px -> 0px vertical lift): Frames 150 to 195 (5.0s - 6.5s)
 * - Hold: Frames 195 to 375 (6.5s - 12.5s)
 * - Exit (smooth fade-out): Frames 375 to 420 (12.5s - 14.0s)
 */
export const CinematicOpeningTitle: React.FC<CinematicOpeningTitleProps> = ({
  titleJa,
  titleEn,
  frame: explicitFrame,
}) => {
  const currentFrame = explicitFrame ?? useCurrentFrame();

  const START_FRAME = 150; // 5.0s
  const FADE_IN_END = 195; // 6.5s
  const FADE_OUT_START = 375; // 12.5s
  const END_FRAME = 420; // 14.0s

  if (currentFrame < START_FRAME || currentFrame > END_FRAME) {
    return null;
  }

  // Fade-in / fade-out opacity
  let opacity = 1;
  if (currentFrame < FADE_IN_END) {
    opacity = interpolate(currentFrame, [START_FRAME, FADE_IN_END], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  } else if (currentFrame > FADE_OUT_START) {
    opacity = interpolate(currentFrame, [FADE_OUT_START, END_FRAME], [1, 0], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  }

  // Vertical subtle rise: 12px -> 0px during entry
  const translateY = interpolate(currentFrame, [START_FRAME, FADE_IN_END], [12, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Animated central crimson divider: 0px -> 260px between frames 160 and 210
  const dividerWidth = interpolate(currentFrame, [160, 210], [0, 260], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const { mainTitleEn, subTitleEn } = parseStoryTitle(titleEn);
  const displayTitleJa = titleJa ? titleJa.trim() : '';

  if (!displayTitleJa && !mainTitleEn) return null;

  return (
    <div
      style={{
        position: 'absolute',
        top: '38%',
        left: 0,
        right: 0,
        transform: `translateY(calc(-50% + ${translateY}px))`,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        opacity,
        pointerEvents: 'none',
        zIndex: 52,
      }}
    >
      <div
        style={{
          background:
            'radial-gradient(ellipse at center, rgba(10, 10, 12, 0.78) 0%, rgba(10, 10, 12, 0) 75%)',
          padding: '36px 64px',
          borderRadius: '16px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          textAlign: 'center',
          maxWidth: '85%',
        }}
      >
        {/* Linha 1 (Kicker): RED SUN JAPAN  •  ARCHIVAL CHRONICLE */}
        <div
          style={{
            fontFamily: '"RedSunCinzel", "Cinzel", "Georgia", "Noto Serif", serif',
            fontSize: '14px',
            fontWeight: 600,
            color: '#e5a93c',
            letterSpacing: '0.3em',
            textTransform: 'uppercase',
            textShadow: '0 2px 8px rgba(0,0,0,0.95)',
            marginBottom: '8px',
          }}
        >
          RED SUN JAPAN &nbsp;•&nbsp; ARCHIVAL CHRONICLE
        </div>

        {/* Linha 2 (Título em Japonês - title_ja) */}
        {displayTitleJa && (
          <div
            style={{
              fontFamily: '"Noto Serif JP", "Hiragino Mincho ProN", "Yu Mincho", serif',
              fontSize: '54px',
              fontWeight: 700,
              color: '#f4f1ea',
              letterSpacing: '0.12em',
              lineHeight: 1.25,
              textShadow: '0 4px 24px rgba(0,0,0,0.95)',
            }}
          >
            {displayTitleJa}
          </div>
        )}

        {/* Divisor Central: Linha horizontal de 2px em vermelho-carmesim (#b91c1c) */}
        <div
          style={{
            width: `${dividerWidth}px`,
            height: '2px',
            backgroundColor: '#b91c1c',
            boxShadow: '0 0 8px rgba(185, 28, 28, 0.6)',
            marginTop: '10px',
            marginBottom: '14px',
          }}
        />

        {/* Linha 3 (Título Principal em Inglês - mainTitleEn) */}
        {mainTitleEn && (
          <div
            style={{
              fontFamily: '"RedSunCinzel", "Cinzel", "Playfair Display", Georgia, serif',
              fontSize: '38px',
              fontWeight: 700,
              color: '#ffffff',
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              lineHeight: 1.25,
              textShadow: '0 3px 16px rgba(0,0,0,0.95)',
            }}
          >
            {mainTitleEn}
          </div>
        )}

        {/* Linha 4 (Subtítulo Opcional - subTitleEn) */}
        {subTitleEn && (
          <div
            style={{
              fontFamily: '"Georgia", "Playfair Display", serif',
              fontStyle: 'italic',
              fontSize: '18px',
              color: '#d4d4d8',
              letterSpacing: '0.06em',
              lineHeight: 1.3,
              marginTop: '6px',
              textShadow: '0 2px 8px rgba(0,0,0,0.95)',
            }}
          >
            {subTitleEn}
          </div>
        )}
      </div>
    </div>
  );
};
