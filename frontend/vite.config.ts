import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import dts from 'vite-plugin-dts'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [
    vue(),
    vuetify({ autoImport: true }),
    dts({
      include: ['src/**/*.ts', 'src/**/*.vue'],
      insertTypesEntry: true,
      rollupTypes: true,
      tsconfigPath: './tsconfig.json',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    lib: {
      entry: fileURLToPath(new URL('./src/index.ts', import.meta.url)),
      name: 'MoriMemoryUI',
      formats: ['es'],
      fileName: 'index',
      cssFileName: 'memory-ui',
    },
    rollupOptions: {
      external: ['vue', 'vuetify', /^vuetify\//, 'cytoscape'],
      output: {
        globals: {
          vue: 'Vue',
          vuetify: 'Vuetify',
          cytoscape: 'cytoscape',
        },
      },
    },
    sourcemap: true,
    emptyOutDir: true,
  },
})
