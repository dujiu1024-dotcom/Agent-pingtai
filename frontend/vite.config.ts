import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig(({ mode }) => ({
  // Both artifacts keep the same public /app/ URL contract. The reverse proxy
  // selects which artifact serves that URL; the build itself never overwrites
  // the currently deployed frontend when producing the parallel candidate.
  base: '/app/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
  },
  build: {
    outDir:
      mode === 'parallel'
        ? '../agent_platform/web/static-parallel'
        : '../agent_platform/web/static',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          icon: ['lucide-react'],
        },
      },
    },
  },
}))
