import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const demoRoot = fileURLToPath(new URL('.', import.meta.url))
const repoRoot = fileURLToPath(new URL('..', import.meta.url))
const frontendSrc = fileURLToPath(new URL('../frontend/src', import.meta.url))

export default defineConfig({
  root: demoRoot,
  base: '/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': frontendSrc,
    },
    dedupe: ['react', 'react-dom'],
  },
  server: {
    host: '0.0.0.0',
    fs: { allow: [repoRoot] },
  },
  build: {
    outDir: fileURLToPath(new URL('./dist-production', import.meta.url)),
    emptyOutDir: true,
  },
})
