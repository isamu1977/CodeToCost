/**
 * CodeToCost — design tokens.
 *
 * Tema dark-mode moderno para conteúdo Dev/Infra (Arquitetura de Automação,
 * SaaS e Finanças B2B). Base slate-escura, acentos semânticos:
 *
 *   emerald → economias / sucesso / self-hosted
 *   cyan    → APIs / webhooks / cloud
 *   blue    → infra / VPS / orquestração
 *   rose    → custo alto / erro / risco
 *   amber   → atenção / limites / avisos
 *
 * Todos os componentes do tema devem consumir estas tokens — nunca cores inline.
 */

/** Nomes de acento suportados pela paleta. */
export type AccentName = 'emerald' | 'cyan' | 'blue' | 'rose' | 'amber';

export const palette = {
  /** Fundo principal (slate 950). */
  bg: '#0B0F19',
  /** Fundo profundo — usado em vinhetas e rodapés. */
  bgDeep: '#090D16',
  /** Cards / containers. */
  surface: '#111827',
  /** Containers levemente elevados (headers, chips). */
  surfaceRaised: '#151D2C',
  /** Borda sutil. */
  border: '#1F2937',
  /** Borda forte (colunas em destaque, inputs). */
  borderStrong: '#374151',

  text: '#E5E7EB',
  textMuted: '#9CA3AF',
  textFaint: '#6B7280',

  emerald: '#10B981',
  cyan: '#06B6D4',
  blue: '#3B82F6',
  rose: '#F43F5E',
  amber: '#F59E0B',
} as const;

/** Cores de acento com a variante translúcida para fundos de chip/card. */
export const accents: Record<AccentName, { base: string; dim: string; label: string }> = {
  emerald: { base: palette.emerald, dim: 'rgba(16, 185, 129, 0.14)', label: 'Economia' },
  cyan: { base: palette.cyan, dim: 'rgba(6, 182, 212, 0.14)', label: 'API / Cloud' },
  blue: { base: palette.blue, dim: 'rgba(59, 130, 246, 0.14)', label: 'Infra' },
  rose: { base: palette.rose, dim: 'rgba(244, 63, 94, 0.14)', label: 'Custo alto' },
  amber: { base: palette.amber, dim: 'rgba(245, 158, 11, 0.14)', label: 'Atenção' },
};

export const radii = {
  xs: 6,
  sm: 10,
  md: 14,
  lg: 18,
  xl: 26,
  pill: 999,
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 40,
  xxl: 64,
} as const;

/**
 * Fontes: preferimos Inter (títulos/corpo) e JetBrains Mono (números, código,
 * rótulos técnicos). Fallbacks de sistema garantem render offline.
 */
export const fonts = {
  sans: "'Inter', 'SF Pro Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
  mono: "'JetBrains Mono', 'SF Mono', ui-monospace, Menlo, Consolas, 'Liberation Mono', monospace",
} as const;

export const shadows = {
  card: '0 18px 40px rgba(0, 0, 0, 0.45)',
  soft: '0 8px 20px rgba(0, 0, 0, 0.32)',
  glowEmerald: '0 0 0 1px rgba(16,185,129,0.35), 0 12px 32px rgba(16,185,129,0.12)',
  glowRose: '0 0 0 1px rgba(244,63,94,0.40), 0 12px 32px rgba(244,63,94,0.14)',
} as const;

/** Identidade da marca — usada pela camada fixa de marca do `MainComposition`. */
export const brand = {
  name: 'CodeToCost',
  tagline: 'Clear Architecture, Real Math, Zero Fluff.',
} as const;

/**
 * Resolve um nome de acento para a cor base.
 * Retorna o accent neutro (cyan) para valores desconhecidos.
 */
export const accentColor = (name: AccentName | string | undefined): string => {
  if (name && Object.prototype.hasOwnProperty.call(accents, name)) {
    return accents[name as AccentName].base;
  }
  return accents.cyan.base;
};

/** Resolve um nome de acento para a variante translúcida. */
export const accentDim = (name: AccentName | string | undefined): string => {
  if (name && Object.prototype.hasOwnProperty.call(accents, name)) {
    return accents[name as AccentName].dim;
  }
  return accents.cyan.dim;
};
