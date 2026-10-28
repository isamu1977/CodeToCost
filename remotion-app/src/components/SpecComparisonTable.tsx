import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { ComparisonStatus, SpecComparisonProps } from '../types';
import { accentColor, accentDim, fonts, palette, radii, shadows } from '../theme';

export type SpecComparisonTableProps = SpecComparisonProps;

const STATUS_COLOR: Record<ComparisonStatus, string> = {
  check: palette.emerald,
  cross: palette.rose,
  warning: palette.amber,
};

const STATUS_GLYPH: Record<ComparisonStatus, string> = {
  check: '✓',
  cross: '✕',
  warning: '!',
};

/**
 * SpecComparisonTable — tabela comparativa animada (Make vs n8n vs Zapier).
 *
 * - Linhas entram em cascata com `spring()`.
 * - Cada célula tem um status (`check` / `cross` / `warning`) com cor semântica.
 * - Preços ficam destacado no header; a coluna `highlighted` recebe glow.
 */
export const SpecComparisonTable: React.FC<SpecComparisonTableProps> = ({
  title,
  subtitle,
  columns,
  rows,
  values,
  footerNote,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const headerSpring = spring({
    frame,
    fps,
    config: { damping: 200, mass: 0.7 },
    durationInFrames: 20,
  });

  const titleOpacity = interpolate(frame, [0, 14], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const labelWidth = 300;
  const columnWidth = 340;
  const tableWidth = labelWidth + columnWidth * columns.length;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 34,
        padding: '0 90px 150px',
      }}
    >
      {/* Título */}
      <div style={{ opacity: titleOpacity, textAlign: 'center'}}>
        <div
          style={{
            fontFamily: fonts.sans,
            fontSize: 46,
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
              marginTop: 10,
              fontFamily: fonts.sans,
              fontSize: 22,
              fontWeight: 400,
              color: palette.textMuted,
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>

      {/* Tabela */}
      <div
        style={{
          width: tableWidth,
          background: palette.surface,
          border: `1px solid ${palette.border}`,
          borderRadius: radii.lg,
          overflow: 'hidden',
          boxShadow: shadows.card,
          opacity: headerSpring,
          transform: `translateY(${(1 - headerSpring) * 18}px)`,
        }}
      >
        {/* Header: coluna de rótulo + colunas de produto */}
        <div
          style={{
            display: 'flex',
            background: palette.surfaceRaised,
            borderBottom: `1px solid ${palette.border}`,
          }}
        >
          <div
            style={{
              width: labelWidth,
              padding: '22px 26px',
              fontFamily: fonts.mono,
              fontSize: 14,
              fontWeight: 600,
              letterSpacing: '0.16em',
              textTransform: 'uppercase',
              color: palette.textFaint,
            }}
          >
            Criterion
          </div>
          {columns.map((column) => {
            const accent = accentColor(column.accent);
            return (
              <div
                key={column.id}
                style={{
                  width: columnWidth,
                  padding: '20px 26px',
                  borderLeft: `1px solid ${palette.border}`,
                  background: column.highlighted ? accentDim(column.accent) : 'transparent',
                  boxShadow: column.highlighted
                    ? `inset 0 2px 0 0 ${accent}`
                    : 'none',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                  }}
                >
                  <div
                    style={{
                      fontFamily: fonts.sans,
                      fontSize: 27,
                      fontWeight: 700,
                      letterSpacing: '-0.01em',
                      color: palette.text,
                    }}
                  >
                    {column.name}
                  </div>
                  {column.badge ? (
                    <div
                      style={{
                        fontFamily: fonts.mono,
                        fontSize: 11,
                        fontWeight: 700,
                        letterSpacing: '0.12em',
                        padding: '4px 9px',
                        borderRadius: radii.xs,
                        background: accentDim(column.accent),
                        color: accent,
                        border: `1px solid ${accent}55`,
                      }}
                    >
                      {column.badge}
                    </div>
                  ) : null}
                </div>
                <div
                  style={{
                    marginTop: 8,
                    fontFamily: fonts.mono,
                    fontSize: 22,
                    fontWeight: 600,
                    color: accent,
                  }}
                >
                  {column.price}
                </div>
              </div>
            );
          })}
        </div>

        {/* Linhas */}
        {rows.map((row, rowIndex) => {
          const delay = 12 + rowIndex * 5;
          const rowSpring = spring({
            frame: Math.max(0, frame - delay),
            fps,
            config: { damping: 200, mass: 0.6 },
            durationInFrames: 22,
          });

          return (
            <div
              key={row.key}
              style={{
                display: 'flex',
                borderBottom:
                  rowIndex < rows.length - 1
                    ? `1px solid ${palette.border}`
                    : 'none',
                opacity: rowSpring,
                transform: `translateX(${(1 - rowSpring) * -24}px)`,
              }}
            >
              <div
                style={{
                  width: labelWidth,
                  padding: '20px 26px',
                  fontFamily: fonts.sans,
                  fontSize: 19,
                  fontWeight: row.emphasize ? 600 : 500,
                  color: row.emphasize ? palette.text : palette.textMuted,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                }}
              >
                {row.emphasize ? (
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: 3,
                      background: palette.rose,
                      flexShrink: 0,
                    }}
                  />
                ) : null}
                {row.label}
              </div>

              {columns.map((column) => {
                const cell = values[row.key] ? values[row.key][column.id] : undefined;
                const status: ComparisonStatus = cell ? cell.status : 'warning';
                const color = STATUS_COLOR[status];

                return (
                  <div
                    key={column.id}
                    style={{
                      width: columnWidth,
                      padding: '20px 26px',
                      borderLeft: `1px solid ${palette.border}`,
                      background: column.highlighted
                        ? 'rgba(255,255,255,0.015)'
                        : 'transparent',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 14,
                    }}
                  >
                    <div
                      style={{
                        width: 26,
                        height: 26,
                        borderRadius: 8,
                        flexShrink: 0,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontFamily: fonts.mono,
                        fontSize: 14,
                        fontWeight: 700,
                        color,
                        background: `${color}1F`,
                        border: `1px solid ${color}55`,
                      }}
                    >
                      {STATUS_GLYPH[status]}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div
                        style={{
                          fontFamily: fonts.sans,
                          fontSize: 19,
                          fontWeight: 500,
                          color: palette.text,
                        }}
                      >
                        {cell ? cell.label : '—'}
                      </div>
                      {cell && cell.note ? (
                        <div
                          style={{
                            marginTop: 3,
                            fontFamily: fonts.mono,
                            fontSize: 13,
                            color: palette.textFaint,
                          }}
                        >
                          {cell.note}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Rodapé */}
      {footerNote ? (
        <div
          style={{
            opacity: interpolate(frame, [rows.length * 5 + 16, rows.length * 5 + 30], [0, 1], {
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
              background: palette.emerald,
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
