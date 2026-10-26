import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
// Production output is served by FastAPI from api/static/workbench/ (same
// origin, no CDN). Relative base keeps assets resolvable under any mount path.
/// <reference types="vitest/config" />
export default defineConfig({
  plugins: [vue()],
  base: './',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../src/trademind/api/static/workbench',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.spec.ts'],
  },
})
