// Component/browser specs use jsdom by default. Pure source, data, and state
// contracts explicitly opt into Node with a per-file @vitest-environment tag.
// Alias mirrors Vite; Playwright runs through its own configuration.

import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.spec.ts'],
    exclude: ['node_modules', 'dist', 'playwright'],
    setupFiles: ['./vitest.setup.ts'],
  },
})
