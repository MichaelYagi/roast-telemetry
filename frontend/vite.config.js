import { execSync } from "node:child_process";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import pkg from "./package.json" with { type: "json" };

// Build identifier: the git short commit hash at build time -- always
// accurate, zero ongoing maintenance (no manually-bumped counter to
// forget). Falls back to "dev" outside a git checkout (e.g. a source
// tarball) so a build never hard-fails just because .git isn't present.
function gitShortHash() {
  try {
    return execSync("git rev-parse --short HEAD", { cwd: import.meta.dirname }).toString().trim();
  } catch {
    return "dev";
  }
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __APP_BUILD__: JSON.stringify(gitShortHash()),
  },
  plugins: [react()],
  build: {
    // Default is 500kB; the real bundle sits a bit over that (Chart.js
    // pulled in for RoastChart/RoastComparisonView) but gzips down to
    // ~160kB, fine for a self-hosted app with no first-time-visitor/
    // slow-connection concern -- not worth real code-splitting effort
    // for. Raised just to silence the advisory, not chosen to exactly
    // fit today's size (that'd just make this go stale the next time
    // the bundle grows a little).
    chunkSizeWarningLimit: 600,
  },
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
