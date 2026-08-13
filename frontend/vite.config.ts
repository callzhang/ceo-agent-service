import react from "@vitejs/plugin-react";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const frontendDirectory = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  base: "/workbench-assets/",
  plugins: [react()],
  build: {
    outDir: resolve(frontendDirectory, "../app/static/workbench"),
    emptyOutDir: true,
    manifest: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
