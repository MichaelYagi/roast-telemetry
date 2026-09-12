import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // WSL + a Windows-mounted path (/mnt/c/...) doesn't deliver inotify
    // events, so Vite's default watcher silently misses on-disk edits.
    // Polling trades a little CPU for HMR that actually fires.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      // Backend now serves everything under /api itself, so this proxies
      // straight through with no path rewrite.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
