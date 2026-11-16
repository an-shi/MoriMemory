import type {
  ApiEnvelope,
  BackupItem,
  BulkActionResponse,
  ContextResponse,
  CreateMemoryInput,
  CreateMemoryResponse,
  GraphPathResponse,
  GraphResponse,
  HealthResponse,
  ImportResult,
  Memory,
  MemoryDetails,
  MemoryFilters,
  MemoryListParams,
  MemoryListResponse,
  MemoryRelation,
  MemorySpace,
  MemoryEvidence,
  MemoryVersion,
  MetaResponse,
  RuntimeConfig,
  SearchResponse,
  StatsResponse,
  TaskListResponse,
  AccessMode,
} from '../types'

export interface MemoryApiClientOptions {
  baseUrl?: string
  token?: string | (() => string | null | undefined | Promise<string | null | undefined>)
  headers?: () => Record<string, string>
  fetch?: typeof fetch
  timeoutMs?: number
}

export class MemoryApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(message: string, status: number, code = 'request_failed', details?: unknown) {
    super(message)
    this.name = 'MemoryApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

type QueryValue = string | number | boolean | null | undefined | Array<string | number>

function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== '' && item !== undefined && item !== null) params.append(key, String(item))
      }
    } else {
      params.append(key, String(value))
    }
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

function filenameFromDisposition(value: string | null, fallback: string): string {
  if (!value) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(value)
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1])
    } catch {
      return fallback
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(value)
  return plain?.[1] ?? fallback
}

export class MemoryApiClient {
  readonly baseUrl: string
  private readonly tokenProvider: MemoryApiClientOptions['token']
  private readonly headerProvider?: MemoryApiClientOptions['headers']
  private readonly fetchImpl: typeof fetch
  private readonly timeoutMs: number
  constructor(options: MemoryApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl || '/api/v1').replace(/\/+$/, '')
    this.tokenProvider = options.token
    this.headerProvider = options.headers
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis)
    this.timeoutMs = options.timeoutMs ?? 30000
  }

  private async authHeaders(): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(this.headerProvider?.() ?? {}),
    }
    let token: string | null | undefined
    if (typeof this.tokenProvider === 'function') {
      token = await this.tokenProvider()
    } else {
      token = this.tokenProvider
    }
    if (token) headers.Authorization = `Bearer ${token}`
    return headers
  }

  private async raw(
    path: string,
    init: RequestInit & { query?: Record<string, QueryValue> } = {},
  ): Promise<Response> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    if (init.signal) {
      if (init.signal.aborted) controller.abort()
      else init.signal.addEventListener('abort', () => controller.abort(), { once: true })
    }
    try {
      const headers = await this.authHeaders()
      if (init.body && !(init.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json'
      }
      return await this.fetchImpl(`${this.baseUrl}${path}${buildQuery(init.query)}`, {
        ...init,
        headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
        signal: controller.signal,
      })
    } finally {
      clearTimeout(timer)
    }
  }

  private async parse<T>(response: Response): Promise<T> {
    const text = await response.text()
    let payload: unknown = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = null
      }
    }
    if (!response.ok) {
      const envelope = payload as Partial<ApiEnvelope<unknown>> | null
      const message =
        (envelope && typeof envelope.message === 'string' && envelope.message) ||
        response.statusText ||
        'request failed'
      const code =
        (envelope && typeof envelope.code === 'string' && envelope.code) || `http_${response.status}`
      throw new MemoryApiError(message, response.status, code, envelope?.data)
    }
    if (payload && typeof payload === 'object' && 'data' in payload) {
      return (payload as ApiEnvelope<T>).data
    }
    return payload as T
  }

  private async request<T>(path: string, init: RequestInit & { query?: Record<string, QueryValue> } = {}): Promise<T> {
    return this.parse<T>(await this.raw(path, init))
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health')
  }

  meta(): Promise<MetaResponse> {
    return this.request<MetaResponse>('/meta')
  }

  stats(mode: AccessMode = 'manage'): Promise<StatsResponse> {
    return this.request<StatsResponse>('/stats', { query: { mode } })
  }

  listSpaces(mode: AccessMode = 'manage', kind?: string[]): Promise<{ items: MemorySpace[] }> {
    return this.request<{ items: MemorySpace[] }>('/spaces', {
      query: { mode, kind },
    })
  }

  listMemories(params: MemoryListParams = {}): Promise<MemoryListResponse> {
    return this.request<MemoryListResponse>('/memories', {
      query: params as Record<string, QueryValue>,
    })
  }

  getMemory(memoryId: string): Promise<MemoryDetails> {
    return this.request<MemoryDetails>(`/memories/${encodeURIComponent(memoryId)}`)
  }

  createMemory(input: CreateMemoryInput): Promise<CreateMemoryResponse> {
    return this.request<CreateMemoryResponse>('/memories', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  }

  updateMemory(memoryId: string, input: Record<string, unknown>): Promise<MemoryDetails> {
    return this.request<MemoryDetails>(`/memories/${encodeURIComponent(memoryId)}`, {
      method: 'PATCH',
      body: JSON.stringify(input),
    })
  }

  deleteMemory(memoryId: string): Promise<Record<string, never>> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' })
  }

  archiveMemory(memoryId: string): Promise<MemoryDetails> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/archive`, { method: 'POST' })
  }

  restoreMemory(memoryId: string): Promise<MemoryDetails> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/restore`, { method: 'POST' })
  }

  reinforceMemory(
    memoryId: string,
    evidence?: Record<string, unknown>,
  ): Promise<MemoryDetails> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/reinforce`, {
      method: 'POST',
      body: JSON.stringify(evidence ? { evidence } : {}),
    })
  }

  bulkAction(ids: string[], action: string): Promise<BulkActionResponse> {
    return this.request<BulkActionResponse>('/memories/bulk', {
      method: 'POST',
      body: JSON.stringify({ ids, action }),
    })
  }

  addEvidence(
    memoryId: string,
    evidence: Record<string, unknown>,
  ): Promise<MemoryEvidence> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/evidence`, {
      method: 'POST',
      body: JSON.stringify(evidence),
    })
  }

  listVersions(memoryId: string): Promise<{ items: MemoryVersion[]; total: number }> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/versions`)
  }

  rollbackMemory(memoryId: string, version: number): Promise<MemoryDetails> {
    return this.request(`/memories/${encodeURIComponent(memoryId)}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ version }),
    })
  }

  mergeMemory(targetId: string, sourceId: string): Promise<Record<string, unknown>> {
    return this.request(`/memories/${encodeURIComponent(targetId)}/merge`, {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId }),
    })
  }

  createRelation(input: {
    source_id: string
    target_id: string
    relation_type?: string
    strength?: number
    confidence?: number
    attributes?: Record<string, unknown>
  }): Promise<MemoryRelation> {
    return this.request('/relations', { method: 'POST', body: JSON.stringify(input) })
  }

  deleteRelation(relationId: string): Promise<Record<string, never>> {
    return this.request(`/relations/${encodeURIComponent(relationId)}`, { method: 'DELETE' })
  }

  search(
    query: string,
    options: Partial<MemoryFilters> & {
      mode?: AccessMode
      limit?: number
      token_budget?: number
      record_access?: boolean
      expand_relations?: boolean
    } = {},
  ): Promise<SearchResponse> {
    return this.request<SearchResponse>('/search', {
      method: 'POST',
      body: JSON.stringify({ query, ...options }),
    })
  }

  context(
    query: string,
    options: Partial<MemoryFilters> & {
      mode?: AccessMode
      limit?: number
      token_budget?: number
      record_access?: boolean
    } = {},
  ): Promise<ContextResponse> {
    return this.request<ContextResponse>('/context', {
      method: 'POST',
      body: JSON.stringify({ query, ...options }),
    })
  }

  graph(
    params: Partial<MemoryFilters> & {
      center_id?: string
      depth?: number
      limit?: number
      mode?: AccessMode
    } = {},
  ): Promise<GraphResponse> {
    return this.request<GraphResponse>('/graph', {
      query: params as Record<string, QueryValue>,
    })
  }

  graphPath(
    sourceId: string,
    targetId: string,
    maxDepth = 6,
  ): Promise<GraphPathResponse> {
    return this.request<GraphPathResponse>('/graph/path', {
      query: { source_id: sourceId, target_id: targetId, max_depth: maxDepth },
    })
  }

  async exportMemories(
    filters: MemoryFilters,
    format: 'json' | 'csv' = 'json',
  ): Promise<{ blob: Blob; filename: string }> {
    const response = await this.raw('/transfer/export', {
      query: { ...filters, format } as Record<string, QueryValue>,
    })
    if (!response.ok) {
      await this.parse(response)
    }
    const filename = filenameFromDisposition(
      response.headers.get('content-disposition'),
      `morimemory.${format}`,
    )
    return { blob: await response.blob(), filename }
  }

  importMemories(input: {
    format: 'json' | 'csv'
    payload: unknown
    target_space_id?: string
    deduplicate?: boolean
  }): Promise<ImportResult> {
    return this.request('/transfer/import', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  }

  listTasks(params: { status?: string[]; task_type?: string[]; page?: number; per_page?: number } = {}): Promise<TaskListResponse> {
    return this.request('/tasks', { query: params as Record<string, QueryValue> })
  }

  retryTask(taskId: string): Promise<unknown> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/retry`, { method: 'POST' })
  }

  getConfig(): Promise<RuntimeConfig> {
    return this.request<RuntimeConfig>('/config')
  }

  updateConfig(patch: Record<string, unknown>): Promise<RuntimeConfig> {
    return this.request('/config', { method: 'PATCH', body: JSON.stringify(patch) })
  }

  testEmbedding(input: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return this.request('/config/embedding/test', {
      method: 'POST',
      body: JSON.stringify(input),
    })
  }

  listBackups(): Promise<{ items: BackupItem[] }> {
    return this.request('/backups')
  }

  createBackup(): Promise<{ name: string; size: number }> {
    return this.request('/backups', { method: 'POST' })
  }

  async downloadBackup(name: string): Promise<Blob> {
    const response = await this.raw(`/backups/${encodeURIComponent(name)}`)
    if (!response.ok) await this.parse(response)
    return response.blob()
  }

  async restoreBackup(file: File): Promise<Record<string, unknown>> {
    const form = new FormData()
    form.append('file', file)
    return this.request('/backups/restore', { method: 'POST', body: form })
  }
}

export function resolveErrorMessage(error: unknown, fallback = 'request failed'): string {
  if (error instanceof MemoryApiError) return error.message
  if (error instanceof Error) return error.message || fallback
  return fallback
}

/** Public shape used by embedders and component props. */
export type MemoryApi = Pick<MemoryApiClient, keyof MemoryApiClient>

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
