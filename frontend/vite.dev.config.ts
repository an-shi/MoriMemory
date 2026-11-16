import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  root: fileURLToPath(new URL('./dev', import.meta.url)),
  plugins: [vue(), vuetify({ autoImport: true })],
  resolve: {
    alias: {
      '@moriai/memory-ui': fileURLToPath(new URL('./src/index.ts', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:6190',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: fileURLToPath(new URL('./dev-dist', import.meta.url)),
    emptyOutDir: true,
  },
})
