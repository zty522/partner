import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Flask serves the built frontend under /static/ (app.static_url_path).
  // Without base the built index.html references /assets/... which 404s.
  base: "/static/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/healthz": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "../static",
    sourcemap: true,
  },
});
