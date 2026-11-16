import type { App, InjectionKey, Plugin } from 'vue'

import { MemoryApiClient, type MemoryApi, type MemoryApiClientOptions } from './api/client'
import MemoryExplorer from './components/MemoryExplorer.vue'
import type { LocaleCode } from './types'

export interface MemoryUiOptions {
  client?: MemoryApi
  api?: MemoryApiClientOptions
  locale?: LocaleCode
  pageSize?: number
  title?: string
}

export interface MemoryUiContext {
  client: MemoryApi
  locale: LocaleCode
  pageSize: number
  title?: string
}

export const memoryUiKey: InjectionKey<MemoryUiContext> = Symbol('mori-memory-ui')

export function resolveContext(options: MemoryUiOptions = {}): MemoryUiContext {
  const client =
    options.client ??
    (options.api ? new MemoryApiClient(options.api) : undefined)
  if (!client) {
    throw new Error('MoriMemory UI requires `client` or `api` options.')
  }
  return {
    client,
    locale: options.locale ?? 'zh-CN',
    pageSize: options.pageSize ?? 20,
    title: options.title,
  }
}

export function createMemoryUi(options: MemoryUiOptions = {}): Plugin {
  const context = resolveContext(options)
  return {
    install(app: App) {
      app.provide(memoryUiKey, context)
      app.component('MemoryExplorer', MemoryExplorer)
    },
  }
}
