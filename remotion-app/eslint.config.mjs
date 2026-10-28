import { config } from "@remotion/eslint-config-flat";

export default [
  {
    // Arquivo histórico do canal Red Sun Japan — fora do lint do tema atual.
    // Ver src/legacy_rsj/README.md.
    ignores: ["src/legacy_rsj/**"],
  },
  ...config,
];
