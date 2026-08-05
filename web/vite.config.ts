import { defineConfig } from "vite";

// The binviz backend (`binviz serve`) listens on 127.0.0.1:8000 by default.
// Override with BINVIZ_API=http://host:port when running `npm run dev`.
const target = process.env.BINVIZ_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
