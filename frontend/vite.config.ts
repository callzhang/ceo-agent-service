import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/workbench-assets/",
  plugins: [react()],
  build: {
    outDir: decodeURIComponent(
      new URL("../app/static/workbench", import.meta.url).pathname,
    ),
    emptyOutDir: true,
    manifest: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
