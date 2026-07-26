import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vitest reads this `test` block at runtime. Its bundled Vite differs from the
// project's Vite 8 (rolldown), so the `test` key is annotated to satisfy tsc.
export default defineConfig({
  plugins: [react()],
  // Pin an empty PostCSS config so Vite does not walk up to an unrelated
  // postcss.config.js outside the project (this UI uses plain CSS only).
  css: { postcss: { plugins: [] } },
  // @ts-expect-error vitest config merged into vite config (version-skewed vite types)
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
