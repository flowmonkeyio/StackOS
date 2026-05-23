import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

// StackOS UI build config.
// Per PLAN.md D8 the build output lands in ../stackos/ui_dist and
// is COMMITTED to the repo (no pnpm at user install time). The FastAPI
// daemon mounts that directory as static assets at "/".
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  base: '/',
  build: {
    outDir: path.resolve(__dirname, '../stackos/ui_dist'),
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: true,
    // The daemon listens on 5180 (PLAN.md). Proxy /api and /mcp for dev.
    proxy: {
      '/api': 'http://127.0.0.1:5180',
      '/mcp': 'http://127.0.0.1:5180',
    },
  },
})
