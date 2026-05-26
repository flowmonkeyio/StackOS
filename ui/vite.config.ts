import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

function trimGeneratedTrailingWhitespace(): Plugin {
  const trim = (source: string) => source.replace(/[ \t]+$/gm, '')

  return {
    name: 'stackos-trim-generated-trailing-whitespace',
    enforce: 'post',
    renderChunk(code) {
      return trim(code)
    },
    generateBundle(_, bundle) {
      for (const output of Object.values(bundle)) {
        if (output.type === 'asset' && typeof output.source === 'string') {
          output.source = trim(output.source)
        }
      }
    },
  }
}

// StackOS UI build config.
// Per the setup contract, the build output lands in ../stackos/ui_dist and
// is COMMITTED to the repo (no pnpm at user install time). The FastAPI
// daemon mounts that directory as static assets at "/".
export default defineConfig({
  plugins: [vue(), trimGeneratedTrailingWhitespace()],
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
    // The daemon listens on 5180. Proxy /api and /mcp for dev.
    proxy: {
      '/api': 'http://127.0.0.1:5180',
      '/mcp': 'http://127.0.0.1:5180',
    },
  },
})
