import { defineConfig } from "vite";

// The binviz backend (`binviz serve`) listens on 127.0.0.1:8000 by default.
// Override with BINVIZ_API=http://host:port when running `npm run dev`.
const target = process.env.BINVIZ_API ?? "http://127.0.0.1:8000";

// The API requires a token (S1a). Two ways to supply
// it in dev, both of which work:
//
//   BINVIZ_TOKEN=… npm run dev     — the proxy attaches it, browser unaware
//   open http://localhost:5173/?token=…  — src/auth.ts picks it up
//
// Starting the server with `binviz serve --token $BINVIZ_TOKEN` makes the
// first form stable across restarts.
const token = process.env.BINVIZ_TOKEN;

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        // also rewrites Host to the target, which is what the backend's
        // TrustedHost allowlist wants to see
        changeOrigin: true,
        ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
