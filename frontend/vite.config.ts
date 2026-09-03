import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // The FastAPI app serves this directory (see app/main.py::_mount_frontend).
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // `npm run dev` talks to a locally running uvicorn, so the browser can hit
    // the same relative /api and /media paths it uses in production.
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
    },
  },
});
