import React, { useMemo } from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { ArchitectureProps, FlowNode, FlowNodeKind } from '../types';
import { fonts, palette, radii, shadows } from '../theme';

export type ArchitectureFlowProps = ArchitectureProps;

const CANVAS_WIDTH = 1560;
const CANVAS_HEIGHT = 620;

const NODE_WIDTH = 250;
const NODE_HEIGHT = 96;

const KIND_ACCENT: Record<FlowNodeKind, string> = {
  webhook: palette.cyan,
  vps: palette.blue,
  queue: palette.amber,
  database: palette.emerald,
  api: palette.cyan,
  storage: palette.emerald,
};

const KIND_LABEL: Record<FlowNodeKind, string> = {
  webhook: 'Webhook',
  vps: 'Runtime',
  queue: 'Queue',
  database: 'Database',
  api: 'API',
  storage: 'Storage',
};

/**
 * ArchitectureFlow — diagrama de blocos com conexões dirigidas.
 *
 * Os nós entram em cascata com `spring()` e as arestas são "desenhadas"
 * progressivamente (stroke-dashoffset animado frame a frame).
 */
export const ArchitectureFlow: React.FC<ArchitectureFlowProps> = ({
  title,
  subtitle,
  nodes,
  edges,
  footerNote,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const nodeById: Record<string, FlowNode> = useMemo(() => {
    const map: Record<string, FlowNode> = {};
    nodes.forEach((node) => {
      map[node.id] = node;
    });
    return map;
  }, [nodes]);

  const titleOpacity = interpolate(frame, [0, 14], [0, 1], {
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
        gap: 28,
        padding: '0 90px 150px',
      }}
    >
      <div style={{ textAlign: 'center', opacity: titleOpacity }}>
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
          width: CANVAS_WIDTH,
          height: CANVAS_HEIGHT,
          position: 'relative',
          background: palette.surface,
          border: `1px solid ${palette.border}`,
          borderRadius: radii.lg,
          boxShadow: shadows.card,
        }}
      >
        {/* Arestas */}
        <svg
          width={CANVAS_WIDTH}
          height={CANVAS_HEIGHT}
          style={{ position: 'absolute', inset: 0 }}
        >
          <defs>
            <marker
              id="flow-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={palette.borderStrong} />
            </marker>
          </defs>

          {edges.map((edge, index) => {
            const from = nodeById[edge.from];
            const to = nodeById[edge.to];
            if (!from || !to) return null;

            const delay = 14 + index * 7;
            const progress = spring({
              frame: Math.max(0, frame - delay),
              fps,
              config: { damping: 200, mass: 0.6 },
              durationInFrames: 20,
            });

            // Recorta a linha na borda retangular de cada nó.
            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const length = Math.sqrt(dx * dx + dy * dy) || 1;
            const ux = dx / length;
            const uy = dy / length;

            const boundary = (vx: number, vy: number): number => {
              const bx =
                Math.abs(vx) > 0.0001 ? NODE_WIDTH / 2 / Math.abs(vx) : Number.POSITIVE_INFINITY;
              const by =
                Math.abs(vy) > 0.0001 ? NODE_HEIGHT / 2 / Math.abs(vy) : Number.POSITIVE_INFINITY;
              return Math.min(bx, by);
            };

            const fromPad = boundary(ux, uy) + 10;
            const toPad = boundary(ux, uy) + 18;
            const x1 = from.x + ux * fromPad;
            const y1 = from.y + uy * fromPad;
            const x2 = to.x - ux * toPad;
            const y2 = to.y - uy * toPad;
            const segLength = Math.max(Math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2), 1);

            const midX = (x1 + x2) / 2;
            const midY = (y1 + y2) / 2;

            return (
              <g key={`${edge.from}-${edge.to}-${index}`}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={palette.borderStrong}
                  strokeWidth={2}
                  strokeDasharray={edge.dashed ? '8 8' : undefined}
                  markerEnd="url(#flow-arrow)"
                  opacity={0.9 * progress}
                />
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={palette.cyan}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  strokeDasharray={segLength}
                  strokeDashoffset={segLength * (1 - progress)}
                  fill="none"
                />
                {edge.label ? (
                  <text
                    x={midX}
                    y={midY - 14}
                    textAnchor="middle"
                    fill={palette.textMuted}
                    fontFamily={fonts.mono}
                    fontSize={13}
                    stroke={palette.surface}
                    strokeWidth={5}
                    paintOrder="stroke"
                    opacity={progress}
                  >
                    {edge.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>

        {/* Nós */}
        {nodes.map((node, index) => {
          const delay = 6 + index * 6;
          const nodeSpring = spring({
            frame: Math.max(0, frame - delay),
            fps,
            config: { damping: 200, mass: 0.7 },
            durationInFrames: 22,
          });

          const accent = KIND_ACCENT[node.kind];

          return (
            <div
              key={node.id}
              style={{
                position: 'absolute',
                left: node.x - NODE_WIDTH / 2,
                top: node.y - NODE_HEIGHT / 2,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
                padding: '16px 20px',
                borderRadius: radii.md,
                background: palette.surfaceRaised,
                border: `1px solid ${palette.border}`,
                borderLeft: `3px solid ${accent}`,
                boxShadow: shadows.soft,
                opacity: nodeSpring,
                transform: `scale(${0.92 + nodeSpring * 0.08}) translateY(${(1 - nodeSpring) * 10}px)`,
                transformOrigin: 'center center',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                gap: 4,
              }}
            >
              <div
                style={{
                  fontFamily: fonts.mono,
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: '0.16em',
                  textTransform: 'uppercase',
                  color: accent,
                }}
              >
                {KIND_LABEL[node.kind]}
              </div>
              <div
                style={{
                  fontFamily: fonts.sans,
                  fontSize: 23,
                  fontWeight: 700,
                  letterSpacing: '-0.01em',
                  color: palette.text,
                }}
              >
                {node.label}
              </div>
              {node.sublabel ? (
                <div
                  style={{
                    fontFamily: fonts.mono,
                    fontSize: 13,
                    color: palette.textFaint,
                  }}
                >
                  {node.sublabel}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {footerNote ? (
        <div
          style={{
            opacity: interpolate(frame, [nodes.length * 6 + 12, nodes.length * 6 + 28], [0, 1], {
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
              background: palette.cyan,
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
