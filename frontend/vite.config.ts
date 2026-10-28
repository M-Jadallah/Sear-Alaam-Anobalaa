import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', 'VITE_').VITE_API_TARGET || 'http://127.0.0.1:8000';
  return {
  plugins: [react()],
  server: {
    proxy: { "/api": target },
    // Wait for formatters/editors to finish writing before transforming a file.
    watch: { awaitWriteFinish: { stabilityThreshold: 150, pollInterval: 20 } },
  },
  preview: { proxy: { "/api": target } },
  };
});
