import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// The build lands inside the Python package, so the wheel -- and the Docker
// image built from it -- carries the app with no Node at runtime.
const outDir = path.resolve(__dirname, "../src/housemaster/web/dist")

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // MapLibre's worker is an ES module that imports a chunk shared with the
  // main library, so it has to stay one.
  worker: { format: "es" },
  build: {
    outDir,
    emptyOutDir: true,
    target: "es2023",
  },
  server: {
    // `housemaster serve` answers the API; Vite answers everything else.
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
})
