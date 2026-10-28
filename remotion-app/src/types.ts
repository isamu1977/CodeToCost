/**
 * CodeToCost — contrato de tipos do `timeline.json`.
 *
 * O `timeline.json` é gerado pelo pipeline de narração (Whisper) e descreve,
 * frame a frame, qual componente de negócio deve aparecer na tela.
 *
 * Convenção de unidades:
 *   - tempos de fala (`start`/`end` de `WordTimestamp`) em **segundos**;
 *   - tempos de cena (`startFrame`/`durationInFrames`) em **frames**;
 *   - coordenadas de diagrama (`x`/`y`) em **pixels** do canvas interno.
 */

/** Acentento visual associado a uma série/coluna/nó. */
export type AccentName = 'emerald' | 'cyan' | 'blue' | 'rose' | 'amber';

/** Timestamp de uma palavra individual, alinhado por Whisper. */
export type WordTimestamp = {
  /** Texto da palavra (pode incluir pontuação anexada). */
  word: string;
  /** Início em segundos, relativo ao início do áudio. */
  start: number;
  /** Fim em segundos, relativo ao início do áudio. */
  end: number;
};

/** Status de uma célula da tabela comparativa. */
export type ComparisonStatus = 'check' | 'cross' | 'warning';

/** Valor exibido em uma célula da tabela comparativa. */
export type ComparisonValue = {
  status: ComparisonStatus;
  /** Texto exibido (ex.: "Sim", "Não", "Limitado a 10k"). */
  label: string;
  /** Nota curta opcional, exibida em tooltip-style abaixo do label. */
  note?: string;
};

/** Coluna da tabela comparativa (um produto/plan being compared). */
export type SpecColumn = {
  id: string;
  /** Nome exibido no header (ex.: "n8n", "Make", "Zapier"). */
  name: string;
  /** Preço ou resumo textual destacado (ex.: "$20/mês"). */
  price: string;
  /** Acento da coluna. */
  accent: AccentName;
  /** Badge opcional no header (ex.: "MENOR CUSTO"). */
  badge?: string;
  /** Quando true, a coluna recebe destaque visual (borda + glow). */
  highlighted?: boolean;
};

/** Linha (critério) da tabela comparativa. */
export type SpecRow = {
  /** Chave estável, usada para casar com `SpecColumn.values`. */
  key: string;
  /** Rótulo do critério exibido na primeira coluna. */
  label: string;
  /** Linhas marcadas como "custo total" recebem peso visual extra. */
  emphasize?: boolean;
};

/** Valores da tabela: `values[rowKey][columnId]`. */
export type SpecRowValues = Record<string, Record<string, ComparisonValue>>;

/** Props da cena de tabela comparativa. */
export type SpecComparisonProps = {
  kind: 'spec-comparison';
  title: string;
  subtitle?: string;
  columns: SpecColumn[];
  rows: SpecRow[];
  /** Valores por linha: `values[rowKey][columnId]`. */
  values: SpecRowValues;
  /** Rodapé com a conclusão da cena. */
  footerNote?: string;
};

/** Uma barra do gráfico de custo (um volume). */
export type CostTier = {
  /** Rótulo do patamar (ex.: "10k", "50k", "250k"). */
  label: string;
  /** Número de operações no patamar. */
  operations: number;
  /** Custo por solução neste patamar. */
  values: CostValue[];
  /** Quando true, o patamar é destacado (ex.: o ponto de inflexão). */
  highlight?: boolean;
};

/** Custo de uma solução dentro de um patamar de volume. */
export type CostValue = {
  /** Nome da solução (ex.: "n8n self-hosted"). */
  name: string;
  /** Custo mensal em USD. */
  value: number;
  accent: AccentName;
};

/** Props da cena de gráfico de custo. */
export type CostChartProps = {
  kind: 'cost-chart';
  title: string;
  subtitle?: string;
  tiers: CostTier[];
  /** Unidade dos valores (ex.: "USD/mês"). */
  unit?: string;
  /** Nome da série destacada no rodapé. */
  highlightSeries?: string;
  footerNote?: string;
};

/** Tipos de nó do diagrama de arquitetura. */
export type FlowNodeKind =
  | 'webhook'
  | 'vps'
  | 'queue'
  | 'database'
  | 'api'
  | 'storage';

/** Nó do diagrama de arquitetura. */
export type FlowNode = {
  id: string;
  /** Nome do nó (ex.: "Webhook", "Postgres"). */
  label: string;
  /** Subtítulo técnico (ex.: "n8n / Docker"). */
  sublabel?: string;
  kind: FlowNodeKind;
  /** Posição X em pixels do canvas interno do diagrama. */
  x: number;
  /** Posição Y em pixels do canvas interno do diagrama. */
  y: number;
};

/** Conexão dirigida entre dois nós. */
export type FlowEdge = {
  from: string;
  to: string;
  /** Rótulo opcional sobre a linha (ex.: "POST /events"). */
  label?: string;
  /** Linhas tracejadas representam caminho assíncrono / optional. */
  dashed?: boolean;
};

/** Props da cena de diagrama de arquitetura. */
export type ArchitectureProps = {
  kind: 'architecture';
  title: string;
  subtitle?: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
  footerNote?: string;
};

/** Props da cena de abertura. */
export type TitleProps = {
  kind: 'title';
  title: string;
  subtitle?: string;
  /** Eyebrow / kicker acima do título. */
  kicker?: string;
  /** Destaque em destaque (verde = economia, vermelho = custo). */
  highlight?: string;
};

/** Props da cena de encerramento. */
export type OutroProps = {
  kind: 'outro';
  title: string;
  subtitle?: string;
  /** Chamada para ação. */
  cta?: string;
  highlight?: string;
};

/**
 * União discriminada das props de cena. O campo `kind` define qual
 * componente de negócio é renderizado.
 */
export type ComponentProps =
  | TitleProps
  | SpecComparisonProps
  | CostChartProps
  | ArchitectureProps
  | OutroProps;

/** Identificadores dos tipos de cena suportados. */
export type SceneType =
  | 'title'
  | 'spec-comparison'
  | 'cost-chart'
  | 'architecture'
  | 'outro';

/** Uma cena do timeline. */
export type Scene = {
  /** Id estável (usado como key do `Sequence`). */
  id: string;
  /** Frame inicial da cena (inclusivo). */
  startFrame: number;
  /** Duração da cena em frames. */
  durationInFrames: number;
  /** Tipo da cena — espelha `props.kind`. */
  type: SceneType;
  /** Payload específico do componente. */
  props: ComponentProps;
  /**
   * Narração da cena, relativa a `remotion-app/public/`
   * (ex.: `001-n8n-vs-make-scale/scene_01.wav`).
   * Quando presente, a cena toca o seu próprio WAV dentro do `Sequence`.
   */
  audioFile?: string;
};

/** Identidade da marca exibida na camada fixa. */
export type BrandConfig = {
  name: string;
  tagline?: string;
};

/** Raiz do `timeline.json`. */
export type Timeline = {
  /** Versão do contrato (para migrações futuras). */
  version: number;
  /** Id do vídeo no pipeline. */
  videoId: string;
  /** Título do vídeo. */
  title: string;
  fps: number;
  width: number;
  height: number;
  /** Duração total da composição em frames. */
  durationInFrames: number;
  /** Arquivo de narração em `public/` (opcional — o mock não tem áudio). */
  audioFilename?: string;
  brand?: BrandConfig;
  /** Cenas em ordem cronológica. */
  scenes: Scene[];
  /** Timestamps de palavra do Whisper (usados pelas legendas). */
  words?: WordTimestamp[];
};

/**
 * Retorna a cena ativa para um frame dado.
 * Retorna `null` se o frame estiver fora de qualquer cena.
 */
export const findSceneAtFrame = (
  scenes: Scene[],
  frame: number,
): Scene | null => {
  for (let i = 0; i < scenes.length; i++) {
    const scene = scenes[i];
    if (frame >= scene.startFrame && frame < scene.startFrame + scene.durationInFrames) {
      return scene;
    }
  }
  return null;
};
