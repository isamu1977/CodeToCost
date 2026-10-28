import React, { useMemo } from 'react';
import { interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { AccentName, WordTimestamp } from '../types';
import { accentColor, fonts, palette, radii } from '../theme';

export type CaptionOverlayProps = {
  /** Timestamps de palavra do Whisper. */
  words: WordTimestamp[];
  /** Acento do texto ativo. */
  accent?: AccentName;
  /** Cor de fundo da placa da legenda. */
  background?: string;
};

type CaptionGroup = {
  words: WordTimestamp[];
  start: number;
  end: number;
};

const SENTENCE_ENDERS = /[.!?]+$/;
const CLAUSE_ENDERS = /[,;:—–]+$/;
const MAX_WORDS_PER_GROUP = 14;

/**
 * Agrupa palavras em legendas legíveis (frase ou oração).
 * Preferimos quebrar em fim de frase; depois em fim de oração com pelo menos
 * 5 palavras; por fim em pausas longas (> 0.45s) do áudio.
 */
export const buildCaptionGroups = (words: WordTimestamp[]): CaptionGroup[] => {
  if (!words || words.length === 0) return [];

  const groups: CaptionGroup[] = [];
  let current: WordTimestamp[] = [];

  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    current.push(word);

    const next = words[i + 1];
    const hasGap = !!next && next.start - word.end > 0.45;
    const count = current.length;

    const shouldBreak =
      SENTENCE_ENDERS.test(word.word) ||
      (CLAUSE_ENDERS.test(word.word) && count >= 5) ||
      (hasGap && count >= 3) ||
      count >= MAX_WORDS_PER_GROUP ||
      i === words.length - 1;

    if (shouldBreak) {
      groups.push({
        words: current,
        start: current[0].start,
        end: current[current.length - 1].end,
      });
      current = [];
    }
  }

  return groups;
};

/**
 * CaptionOverlay — legenda dinâmica de baixo nível (lower third).
 *
 * Estética deliberadamente sóbria: placa com blur, régua de acento vertical
 * e a palavra ativa levemente destacada. Sem efeito "kinetic" / TikTok.
 */
export const CaptionOverlay: React.FC<CaptionOverlayProps> = ({
  words,
  accent = 'cyan',
  background = 'rgba(9, 13, 22, 0.72)',
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const groups = useMemo(() => buildCaptionGroups(words), [words]);
  const currentTime = frame / fps;

  const group = useMemo(() => {
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      if (currentTime >= g.start && currentTime <= g.end + 0.25) {
        return g;
      }
    }
    return null;
  }, [groups, currentTime]);

  if (!group) return null;

  // Índice da palavra ativa dentro do grupo.
  let activeIndex = -1;
  for (let i = 0; i < group.words.length; i++) {
    if (currentTime >= group.words[i].start) {
      activeIndex = i;
    }
  }

  // Entrada/saída suaves — 10 frames.
  const groupStartFrame = group.start * fps;
  const enter = interpolate(frame - groupStartFrame, [0, 10], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = interpolate(
    group.end + 0.25 - currentTime,
    [0, 0.2],
    [0, 1],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );

  const accentHex = accentColor(accent);
  const visible = Math.min(enter, opacity);

  return (
    <div
      style={{
        position: 'absolute',
        left: 72,
        bottom: 62,
        maxWidth: 1240,
        display: 'flex',
        alignItems: 'stretch',
        gap: 18,
        opacity: visible,
        transform: `translateY(${(1 - enter) * 10}px)`,
        zIndex: 55,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          width: 3,
          borderRadius: radii.pill,
          background: `linear-gradient(180deg, ${accentHex} 0%, ${accentHex}00 100%)`,
          flexShrink: 0,
        }}
      />
      <div
        style={{
          background,
          border: `1px solid ${palette.border}`,
          borderRadius: radii.sm,
          backdropFilter: 'blur(10px)',
          WebkitBackdropFilter: 'blur(10px)',
          padding: '12px 22px',
          fontFamily: fonts.sans,
          fontSize: 34,
          fontWeight: 500,
          lineHeight: 1.35,
          letterSpacing: '-0.005em',
          color: palette.text,
          textShadow: '0 2px 10px rgba(0,0,0,0.55)',
        }}
      >
        {group.words.map((word, i) => (
          <span
            key={`${word.word}-${i}`}
            style={{
              color: i === activeIndex ? accentHex : palette.text,
              fontWeight: i === activeIndex ? 600 : 500,
            }}
          >
            {word.word}
            {i < group.words.length - 1 ? ' ' : ''}
          </span>
        ))}
      </div>
    </div>
  );
};
