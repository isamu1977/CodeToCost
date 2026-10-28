import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { CostChartProps } from '../types';
import { accentColor, fonts, palette, radii, shadows } from '../theme';

export type CostExplosionChartProps = CostChartProps;

const CHART_HEIGHT = 380;
const CHART_WIDTH = 1320;

/**
 * CostExplosionChart — barras agrupadas por patamar de volume.
 *
 * Cada patamar (10k / 50k / 250k operações) é um grupo de barras, uma por
 * solução. As barras crescem com `spring()` em cascata e o patamar de
 * inflexão (o mais caro) recebe destaque em rose.
 */
export const CostExplosionChart: React.FC<CostExplosionChartProps> = ({
  title,
  subtitle,
  tiers,
  unit = 'USD/mês',
  highlightSeries,
  footerNote,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const maxValue = tiers.reduce((max, tier) => {
    const tierMax = tier.values.reduce((m, v) => Math.max(m, v.value), 0);
    return Math.max(max, tierMax);
  }, 0);

  // Escala do eixo Y: 4 linhas de grade.
  const gridLines = [0, 0.25, 0.5, 0.75, 1];
  const niceMax = maxValue > 0 ? Math.ceil(maxValue / 100) * 100 : 100;

  const titleOpacity = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Conjunto de séries (uma por solução) na ordem de aparição.
  const seriesNames: string[] = [];
  tiers.forEach((tier) => {
    tier.values.forEach((value) => {
      if (seriesNames.indexOf(value.name) === -1) {
        seriesNames.push(value.name);
      }
    });
  });

  const seriesAccent: Record<string, string> = {};
  tiers.forEach((tier) => {
    tier.values.forEach((value) => {
      if (!seriesAccent[value.name]) {
        seriesAccent[value.name] = accentColor(value.accent);
      }
    });
  });

  const legendOpacity = interpolate(frame, [8, 22], [0, 1], {
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
        gap: 26,
        padding: '0 90px 150px',
      }}
    >
      {/* Cabeçalho + legenda */}
      <div
        style={{
          width: CHART_WIDTH,
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          opacity: titleOpacity,
        }}
      >
        <div>
          <div
            style={{
              fontFamily: fonts.sans,
              fontSize: 44,
              fontWeight: 700,
              letterSpacing: '-0.02em',
              color: palette.text,
            }}
          >
            {title}
          </div>
          {subtitle ? (
            <div
              style={{
                marginTop: 8,
                fontFamily: fonts.sans,
                fontSize: 21,
                color: palette.textMuted,
              }}
            >
              {subtitle}
            </div>
          ) : null}
        </div>

        <div
          style={{
            display: 'flex',
            gap: 24,
            opacity: legendOpacity,
          }}
        >
          {seriesNames.map((name) => (
            <div
              key={name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
              }}
            >
              <span
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: 3,
                  background: seriesAccent[name],
                }}
              />
              <span
                style={{
                  fontFamily: fonts.mono,
                  fontSize: 15,
                  fontWeight: 600,
                  color: palette.textMuted,
                }}
              >
                {name}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Gráfico */}
      <div
        style={{
          width: CHART_WIDTH,
          background: palette.surface,
          border: `1px solid ${palette.border}`,
          borderRadius: radii.lg,
          boxShadow: shadows.card,
          padding: '28px 32px 22px 32px',
        }}
      >
        <div style={{ position: 'relative', height: CHART_HEIGHT }}>
          {/* Grade + eixo Y */}
          {gridLines.map((ratio) => {
            const y = CHART_HEIGHT - ratio * CHART_HEIGHT;
            return (
              <div
                key={`grid-${ratio}`}
                style={{
                  position: 'absolute',
                  left: 0,
                  right: 0,
                  top: y,
                  height: 1,
                  background: ratio === 0 ? palette.borderStrong : palette.border,
                }}
              />
            );
          })}

          {gridLines.map((ratio) => {
            const y = CHART_HEIGHT - ratio * CHART_HEIGHT;
            return (
              <div
                key={`ylabel-${ratio}`}
                style={{
                  position: 'absolute',
                  left: -8,
                  top: y - 11,
                  transform: 'translateX(-100%)',
                  fontFamily: fonts.mono,
                  fontSize: 14,
                  color: palette.textFaint,
                  whiteSpace: 'nowrap',
                }}
              >
                ${Math.round(niceMax * ratio).toLocaleString('en-US')}
              </div>
            );
          })}

          {/* Grupos de barras */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'flex-end',
              justifyContent: 'space-around',
            }}
          >
            {tiers.map((tier, tierIndex) => {
              const groupDelay = 10 + tierIndex * 10;
              const groupSpring = spring({
                frame: Math.max(0, frame - groupDelay),
                fps,
                config: { damping: 200, mass: 0.7 },
                durationInFrames: 24,
              });

              const isHighlightTier = !!tier.highlight;
              return (
                <div
                  key={tier.label}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-end',
                    justifyContent: 'center',
                    gap: 18,
                    height: CHART_HEIGHT,
                    flex: 1,
                    padding: isHighlightTier ? '0 6px' : 0,
                    borderRadius: radii.sm,
                    background: isHighlightTier
                      ? `rgba(244, 63, 94, ${0.05 * groupSpring})`
                      : 'transparent',
                    boxShadow: isHighlightTier
                      ? `inset 0 0 0 1px rgba(244, 63, 94, ${0.22 * groupSpring})`
                      : 'none',
                  }}
                >
                  {tier.values.map((value, valueIndex) => {
                    const barDelay = groupDelay + valueIndex * 4;
                    const barSpring = spring({
                      frame: Math.max(0, frame - barDelay),
                      fps,
                      config: { damping: 200, mass: 0.8 },
                      durationInFrames: 26,
                    });

                    const ratio = niceMax > 0 ? value.value / niceMax : 0;
                    const barHeight = Math.max(ratio * CHART_HEIGHT * barSpring, 3);
                    const color = seriesAccent[value.name];

                    return (
                      <div
                        key={value.name}
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'flex-end',
                          width: 92,
                          height: CHART_HEIGHT,
                        }}
                      >
                        <div
                          style={{
                            marginBottom: 10,
                            fontFamily: fonts.mono,
                            fontSize: 17,
                            fontWeight: 700,
                            color: color,
                            opacity: barSpring,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          ${value.value.toLocaleString('en-US')}
                        </div>
                        <div
                          style={{
                            width: 72,
                            height: barHeight,
                            borderRadius: `${radii.xs}px ${radii.xs}px 0 0`,
                            background: `linear-gradient(180deg, ${color} 0%, ${color}55 100%)`,
                            boxShadow: `0 0 20px ${color}33`,
                            borderTop: `2px solid ${color}`,
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>

        {/* Rótulos do eixo X */}
        <div
          style={{
            marginTop: 14,
            display: 'flex',
            justifyContent: 'space-around',
          }}
        >
          {tiers.map((tier) => (
            <div
              key={tier.label}
              style={{
                flex: 1,
                textAlign: 'center',
              }}
            >
              <div
                style={{
                  fontFamily: fonts.mono,
                  fontSize: 20,
                  fontWeight: 700,
                  color: tier.highlight ? palette.rose : palette.text,
                }}
              >
                {tier.label}
              </div>
              <div
                style={{
                  marginTop: 4,
                  fontFamily: fonts.sans,
                  fontSize: 14,
                  color: palette.textFaint,
                }}
              >
                {tier.operations.toLocaleString('en-US')} operations
              </div>
            </div>
          ))}
        </div>

        <div
          style={{
            marginTop: 14,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderTop: `1px solid ${palette.border}`,
            paddingTop: 12,
          }}
        >
          <span
            style={{
              fontFamily: fonts.mono,
              fontSize: 13,
              color: palette.textFaint,
            }}
          >
            Values in {unit}
          </span>
          {highlightSeries ? (
            <span
              style={{
                fontFamily: fonts.sans,
                fontSize: 15,
                color: palette.textMuted,
              }}
            >
              Highlight:{' '}
              <span style={{ color: seriesAccent[highlightSeries] ?? palette.text, fontWeight: 600 }}>
                {highlightSeries}
              </span>
            </span>
          ) : null}
        </div>
      </div>

      {footerNote ? (
        <div
          style={{
            width: CHART_WIDTH,
            opacity: interpolate(frame, [40, 56], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            }),
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '12px 22px',
            borderRadius: radii.md,
            border: `1px solid ${palette.border}`,
            background: palette.bgDeep,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 3,
              background: palette.rose,
            }}
          />
          <span
            style={{
              fontFamily: fonts.sans,
              fontSize: 19,
              color: palette.textMuted,
            }}
          >
            {footerNote}
          </span>
        </div>
      ) : null}
    </div>
  );
};
