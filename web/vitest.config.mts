import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  // React 17+ automatic JSX runtime for .test.tsx component tests.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    // Component tests (.test.tsx) opt into jsdom per-file via the
    // `// @vitest-environment jsdom` pragma; pure-logic .test.ts files
    // stay on the faster node environment.
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
