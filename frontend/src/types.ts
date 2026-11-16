export type MemorySpaceKind = 'user' | 'character' | 'conversation'
export type MemoryType =
  | 'working'
  | 'short_term'
  | 'long_term'
  | 'episodic'
  | 'semantic'
  | 'associative'
export type MemoryCategory =
  | 'identity'
  | 'fact'
  | 'preference'
  | 'relationship'
  | 'event'
  | 'goal'
  | 'plan'
  | 'skill'
  | 'opinion'
  | 'habit'
  | 'context'
  | 'summary'
  | 'other'
export type SourceKind =
  | 'user_explicit'
  | 'ai_inference'
  | 'behavior_observation'
  | 'model_summary'
export type MemoryStatus = 'active' | 'archived' | 'superseded' | 'disputed' | 'deleted'
export type RelationType =
  | 'related_to'
  | 'supports'
  | 'contradicts'
  | 'derived_from'
  | 'part_of'
  | 'causes'
  | 'precedes'
  | 'same_entity'
  | 'merged_into'
  | 'consolidated_from'
export type AccessMode = 'manage' | 'recall'

export interface ApiEnvelope<T> {
  status: 'ok' | 'error'
  message: string
  data: T
  code?: string
}

export interface MemorySpace {
  id: string
  kind: MemorySpaceKind
  key: string
  name: string
  description: string
  user_id: string | null
  character_id: string | null
  conversation_id: string | null
  status: string
  settings: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
  memory_count?: number
  writable?: boolean
}

export interface Memory {
  id: string
  space_id: string
  space: MemorySpace | null
  title: string
  content: string
  memory_type: MemoryType
  category: MemoryCategory
  source_kind: SourceKind
  status: MemoryStatus
  importance: number
  confidence: number
  retention: number
  access_count: number
  reinforcement_count: number
  contradiction_count: number
  pinned: boolean
  tags: string[]
  attributes: Record<string, unknown>
  language: string
  occurred_at: string | null
  last_accessed_at: string | null
  last_reinforced_at: string | null
  expires_at: string | null
  version: number
  created_at: string | null
  updated_at: string | null
  evidence_count?: number
  relation_count?: number
  scores?: Record<string, number>
  reasons?: string[]
  token_estimate?: number
  score?: number
}

export interface MemoryEvidence {
  id: string
  memory_id: string
  source_kind: SourceKind
  conversation_id: string | null
  message_id: string | null
  role: string
  quote: string
  observed_at: string | null
  attributes: Record<string, unknown>
  created_at: string | null
}

export interface MemoryVersion {
  id: string
  memory_id: string
  version: number
  change_type: string
  changed_by: string
  created_at: string | null
  snapshot?: Record<string, unknown>
}

export interface MemoryRelation {
  id: string
  source_id: string
  target_id: string
  relation_type: RelationType
  strength: number
  confidence: number
  directed: boolean
  attributes: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
  other?: Memory | null
}

export interface MemoryEntity {
  id: string
  space_id: string | null
  name: string
  canonical_name: string
  entity_type: string
  aliases: string[]
  description: string
  attributes: Record<string, unknown>
}

export interface MemoryDetails extends Memory {
  evidence: MemoryEvidence[]
  versions: MemoryVersion[]
  relations: MemoryRelation[]
  entities: MemoryEntity[]
  versions_total: number
}

export interface MemoryListResponse {
  items: Memory[]
  total: number
  page: number
  per_page: number
  sort_by: string
  sort_order: 'asc' | 'desc'
  focus_id: string | null
  scope: AccessScopeInfo
}

export interface AccessScopeInfo {
  mode: string
  space_ids: string[]
  writable_space_ids: string[]
  granted_memory_ids: string[]
  space_count: number
}

export interface SearchResponse {
  query: string
  items: Memory[]
  candidates: number
  tokens_used: number
  token_budget: number
  truncated: boolean
  scope: AccessScopeInfo
}

export interface ContextResponse extends SearchResponse {
  context: string
  context_tokens: number
}

export interface StatBreakdown {
  [key: string]: number
}

export interface StatsResponse {
  total: number
  active: number
  by_status: StatBreakdown
  by_type: StatBreakdown
  by_category: StatBreakdown
  by_space_kind: StatBreakdown
  relations: number
  evidence: number
  embeddings: number
  pending_consolidation: number
  spaces: number
  spaces_detail: MemorySpace[]
  tasks: { total: number; pending: number; running: number }
  recent_events: MemoryEvent[]
  storage: {
    database_bytes: number
    fts_enabled: boolean
    vector_backend: string
    schema_version: number
  }
  embedding: { enabled: boolean; provider: string; model: string }
  access: { mode: string; space_count: number }
}

export interface MemoryEvent {
  id: string
  memory_id: string | null
  space_id: string | null
  event_type: string
  actor: string
  payload: Record<string, unknown>
  created_at: string | null
  space?: MemorySpace | null
}

export interface GraphNode {
  id: string
  label: string
  title: string
  content: string
  space_id: string
  space_kind: MemorySpaceKind | null
  space_name: string | null
  memory_type: MemoryType
  category: MemoryCategory
  source_kind: SourceKind
  status: MemoryStatus
  importance: number
  confidence: number
  retention: number
  reinforcement_count: number
  access_count: number
  pinned: boolean
  tags: string[]
  occurred_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  relation_type: RelationType
  strength: number
  confidence: number
  directed: boolean
  attributes: Record<string, unknown>
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  total: number
  truncated: boolean
  center_id: string | null
  depth: number | null
}

export interface GraphPathResponse {
  found: boolean
  node_ids: string[]
  edge_ids: string[]
  depth?: number
}

export interface MemoryFilters {
  q?: string
  space_id?: string
  space_ids?: string[]
  space_kind?: string
  memory_type?: string[]
  category?: string[]
  status?: string[]
  source_kind?: string[]
  tags?: string[]
  min_importance?: number
  max_importance?: number
  min_confidence?: number
  max_confidence?: number
  created_after?: string
  created_before?: string
  occurred_after?: string
  occurred_before?: string
  pinned?: boolean
  include_deleted?: boolean
}

export interface MemoryListParams extends MemoryFilters {
  page?: number
  per_page?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  mode?: AccessMode
  focus_id?: string
}

export interface CreateMemoryInput {
  content: string
  title?: string
  memory_type?: string
  category?: string
  source_kind?: string
  scope?: string
  importance?: number | null
  confidence?: number | null
  tags?: string[]
  attributes?: Record<string, unknown>
  occurred_at?: string | null
  evidence?: Array<Record<string, unknown>>
  space_id?: string | null
  deduplicate?: boolean
  force?: boolean
}

export interface CreateMemoryResponse {
  action: 'created' | 'reinforced'
  memory: Memory
  duplicate_of: string | null
  embedding_pending: boolean
  relations: MemoryRelation[]
}

export interface BulkActionResponse {
  processed: string[]
  failed: Array<{ memory_id: string; error: string }>
}

export interface ImportResult {
  created: number
  reinforced: number
  failed: number
  errors: Array<{ row: string; error: string }>
  memory_ids: string[]
}

export interface TaskItem {
  id: string
  task_type: string
  status: string
  priority: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
  error: string
  attempts: number
  max_attempts: number
  dedupe_key: string | null
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TaskListResponse {
  items: TaskItem[]
  total: number
  page: number
  per_page: number
}

export interface EmbeddingConfig {
  enabled: boolean
  provider: string
  model: string
  base_url: string
  dimensions: number
  batch_size: number
  timeout_seconds: number
  normalize: boolean
  api_key_set: boolean
}

export interface RuntimeConfig {
  config_version: number
  server: { host: string; port: number }
  security: {
    default_user_id: string
    allow_character_management: boolean
    share_user_memory_by_default: boolean
  }
  embedding: EmbeddingConfig
  extraction: {
    mode: string
    max_candidates: number
    include_assistant: boolean
    llm: { base_url: string; model: string; api_key_set: boolean }
  }
  retrieval: Record<string, unknown>
  memory: Record<string, unknown>
  vector: { backend: string }
  tasks: Record<string, unknown>
  backup: Record<string, unknown>
}

export interface MetaResponse {
  memory_spaces: MemorySpaceKind[]
  memory_types: MemoryType[]
  categories: MemoryCategory[]
  source_kinds: SourceKind[]
  statuses: MemoryStatus[]
  relation_types: RelationType[]
  permissions: string[]
  access_modes: AccessMode[]
}

export interface HealthResponse {
  status: string
  service: string
  version: string
  uptime_seconds: number
  database: { ok: boolean; schema_version: number; fts5: boolean }
  embedding: { enabled: boolean; provider: string; model: string }
  vector: { backend: string }
  auth_required: boolean
}

export interface BackupItem {
  name: string
  size: number
  created_at: string
}

export interface MemoryTableOptions {
  page: number
  itemsPerPage: number
  sortBy: Array<{ key: string; order?: 'asc' | 'desc' }>
}

export type LocaleCode = 'zh-CN' | 'en-US'

export interface MemoryGraphColors {
  background: string
  surface: string
  text: string
  muted: string
  line: string
  primary: string
  secondary: string
  accent: string
  error: string
  info: string
  success: string
  warning: string
  dark: boolean
}
