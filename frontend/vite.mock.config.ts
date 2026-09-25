import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { tasksMock } from "./dev-mock/tasksMock";

// Tasks pages on synthetic data; every other /api call goes to the running console.
export default defineConfig({
  plugins: [react(), tasksMock()],
  server: { port: 5199, proxy: { "/api": "http://localhost:8765" } },
});
