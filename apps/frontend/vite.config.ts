import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build for the static SPA. Public path is relative so the same build works
// whether the frontend is served at the root of its own domain or behind a
// path prefix on a shared host.
//
// VITE_API_BASE lets the same build target different BFFs at build time.
// Leave empty to use same-origin (works when nginx proxies /api/*, or when
// the frontend and dashboard are co-deployed on the same origin).
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2020",
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE || "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
  define: {
    "import.meta.env.VITE_API_BASE": JSON.stringify(
      process.env.VITE_API_BASE || ""
    ),
  },
}));
