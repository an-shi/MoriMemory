/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

interface ImportMetaEnv {
  readonly VITE_MORIMEMORY_API?: string
  readonly VITE_MORIMEMORY_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
