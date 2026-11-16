<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useDisplay, useTheme } from 'vuetify'

import { resolveErrorMessage, triggerDownload, type MemoryApi } from '../api/client'
import { normalizeLocale, useMessages } from '../i18n'
import { memoryUiKey, type MemoryUiContext } from '../plugin'
import type {
  CreateMemoryInput,
  GraphEdge,
  GraphNode,
  GraphPathResponse,
  LocaleCode,
  Memory,
  MemoryDetails,
  MemoryFilters,
  MemoryGraphColors,
  MemorySpace,
  StatsResponse,
} from '../types'
import MemoryDetailsDrawer from './MemoryDetailsDrawer.vue'
import MemoryCreateDialog from './MemoryCreateDialog.vue'
import MemoryGraph from './MemoryGraph.vue'
import MemoryTable from './MemoryTable.vue'

const props = withDefaults(
  defineProps<{
    client?: MemoryApi
    locale?: LocaleCode
    pageSize?: number
    title?: string
    initialView?: 'table' | 'graph'
  }>(),
  {
    locale: undefined,
    pageSize: undefined,
    title: undefined,
    initialView: undefined,
  },
)

const injected = inject<MemoryUiContext | null>(memoryUiKey, null)
const context = computed<MemoryUiContext | null>(() => {
  if (props.client) {
    return {
      client: props.client,
      locale: props.locale ?? injected?.locale ?? 'zh-CN',
      pageSize: props.pageSize ?? injected?.pageSize ?? 20,
      title: props.title ?? injected?.title,
    }
  }
  if (!injected) return null
  return {
    client: injected.client,
    locale: props.locale ?? injected.locale,
    pageSize: props.pageSize ?? injected.pageSize,
    title: props.title ?? injected.title,
  }
})
const api = computed(() => context.value?.client ?? null)
const locale = computed<LocaleCode>(() => normalizeLocale(props.locale ?? context.value?.locale))
const { t } = useMessages(locale)
const theme = useTheme()
const { mdAndUp, lgAndUp } = useDisplay()

const graphColors = computed<MemoryGraphColors>(() => {
  const colors = theme.current.value.colors as Record<string, string>
  return {
    background: colors.background ?? '#07100c',
    surface: colors.surface ?? '#0c1712',
    text: colors['on-surface'] ?? '#e8f2ed',
    muted: colors.secondary ?? colors['on-surface-variant'] ?? '#8aa398',
    line: colors['surface-variant'] ?? '#13231c',
    primary: colors.primary ?? '#7be3b3',
    secondary: colors.secondary ?? '#57b997',
    accent: colors.accent ?? '#d8b26e',
    error: colors.error ?? '#ff7b72',
    info: colors.info ?? '#6fb8ff',
    success: colors.success ?? '#7be3b3',
    warning: colors.warning ?? '#e6c27a',
    dark: theme.current.value.dark,
  }
})

const stats = ref<StatsResponse | null>(null)
const spaces = ref<MemorySpace[]>([])
const memories = ref<Memory[]>([])
const total = ref(0)
const tableLoading = ref(true)
const graphLoading = ref(true)
const tablePage = ref(1)
const itemsPerPage = ref(props.pageSize ?? context.value?.pageSize ?? 20)
const sortBy = ref<Array<{ key: string; order?: 'asc' | 'desc' }>>([
  { key: 'updated_at', order: 'desc' },
])
const selectedIds = ref<string[]>([])
const selectedMemoryId = ref<string | null>(null)
const detailsOpen = ref(false)
const detailsLoading = ref(false)
const selectedMemory = ref<MemoryDetails | null>(null)

const graphNodes = ref<GraphNode[]>([])
const graphEdges = ref<GraphEdge[]>([])
const graphTruncated = ref(false)
const graphLimit = ref(150)
const graphDepth = ref(1)
const graphLayout = ref<'cose' | 'concentric' | 'grid' | 'breadthfirst' | 'circle'>('concentric')
const clusterBy = ref<'none' | 'space' | 'type'>('none')
const graphFullscreen = ref(false)
const pathMode = ref(false)
const pathSourceId = ref<string | null>(null)
const pathResult = ref<GraphPathResponse | null>(null)
const graphRef = ref<InstanceType<typeof MemoryGraph> | null>(null)

const searchInput = ref('')
const debouncedQuery = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null
const filtersOpen = ref(false)
const filters = reactive({
  spaceId: '',
  spaceKind: '',
  memoryTypes: [] as string[],
  categories: [] as string[],
  statuses: [] as string[],
  sourceKinds: [] as string[],
  dateRange: 'all',
  minImportance: undefined as number | undefined,
  minConfidence: undefined as number | undefined,
  pinned: false,
})
const activeFilterCount = computed(() => {
  let count = 0
  if (filters.spaceId) count += 1
  if (filters.spaceKind) count += 1
  count += filters.memoryTypes.length ? 1 : 0
  count += filters.categories.length ? 1 : 0
  count += filters.statuses.length ? 1 : 0
  count += filters.sourceKinds.length ? 1 : 0
  if (filters.dateRange !== 'all') count += 1
  if (filters.minImportance !== undefined) count += 1
  if (filters.minConfidence !== undefined) count += 1
  if (filters.pinned) count += 1
  return count
})

const importOpen = ref(false)
const importFile = ref<File | null>(null)
const importTargetSpace = ref('')
const importDeduplicate = ref(true)
const importing = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const createOpen = ref(false)
const creating = ref(false)

const activeView = ref<'table' | 'graph'>(props.initialView ?? 'table')

const visibleColumns = ref<string[]>(
  readJson('mori-memory.columns.v2', [
    'content',
    'memory_type',
    'category',
    'quality',
    'status',
    'updated_at',
    'actions',
  ]),
)
const allColumns = computed(() => [
  { key: 'content', title: t('table.content') },
  { key: 'memory_type', title: t('table.type') },
  { key: 'category', title: t('table.category') },
  { key: 'space', title: t('table.space') },
  { key: 'quality', title: t('table.quality') },
  { key: 'importance', title: t('table.importance') },
  { key: 'confidence', title: t('table.confidence') },
  { key: 'status', title: t('table.status') },
  { key: 'evidence_count', title: t('table.evidence') },
  { key: 'relation_count', title: t('table.relations') },
  { key: 'updated_at', title: t('table.updated') },
  { key: 'actions', title: t('table.actions') },
])

const snackbar = reactive({ show: false, message: '', color: 'success' as 'success' | 'error' | 'info' })

const confirmState = reactive({
  open: false,
  title: '',
  message: '',
  confirmText: '',
  color: 'error',
  resolver: null as ((value: boolean) => void) | null,
})

const typeOptions = computed(() =>
  ['working', 'short_term', 'long_term', 'episodic', 'semantic', 'associative'].map((value) => ({
    value,
    title: t(`type.${value}`),
  })),
)
const categoryOptions = computed(() =>
  [
    'identity',
    'fact',
    'preference',
    'relationship',
    'event',
    'goal',
    'plan',
    'skill',
    'opinion',
    'habit',
    'context',
    'summary',
    'other',
  ].map((value) => ({ value, title: t(`category.${value}`) })),
)
const sourceOptions = computed(() =>
  ['user_explicit', 'ai_inference', 'behavior_observation', 'model_summary'].map((value) => ({
    value,
    title: t(`sourceKind.${value}`),
  })),
)
const statusOptions = computed(() =>
  ['active', 'disputed', 'archived', 'superseded'].map((value) => ({
    value,
    title: t(`status.${value}`),
  })),
)
const spaceOptions = computed(() =>
  spaces.value.map((space) => ({
    value: space.id,
    title: `${t(`spaceKind.${space.kind}`)} · ${space.name || space.key} (${space.memory_count ?? 0})`,
  })),
)

const legendItems = computed(() => [
  { label: t('type.long_term'), color: graphColors.value.info },
  { label: t('type.episodic'), color: graphColors.value.warning },
  { label: t('type.semantic'), color: graphColors.value.primary },
  { label: t('type.short_term'), color: graphColors.value.secondary },
  { label: t('type.associative'), color: graphColors.value.accent },
])

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function persist(): void {
  try {
    localStorage.setItem('mori-memory.columns.v2', JSON.stringify(visibleColumns.value))
  } catch {
    // Preferences are optional.
  }
}

function showSnackbar(message: string, color: 'success' | 'error' | 'info' = 'success'): void {
  snackbar.message = message
  snackbar.color = color
  snackbar.show = true
}

function showError(prefix: string, error: unknown): void {
  showSnackbar(`${prefix}: ${resolveErrorMessage(error)}`, 'error')
}

function buildFilters(): MemoryFilters {
  const result: MemoryFilters = {
    q: debouncedQuery.value.trim() || undefined,
    space_id: filters.spaceId || undefined,
    space_kind: filters.spaceKind || undefined,
    memory_type: filters.memoryTypes.length ? filters.memoryTypes : undefined,
    category: filters.categories.length ? filters.categories : undefined,
    status: filters.statuses.length ? filters.statuses : undefined,
    source_kind: filters.sourceKinds.length ? filters.sourceKinds : undefined,
    min_importance: filters.minImportance,
    min_confidence: filters.minConfidence,
    pinned: filters.pinned ? true : undefined,
  }
  if (filters.dateRange !== 'all') {
    const days =
      filters.dateRange === '7d'
        ? 7
        : filters.dateRange === '30d'
          ? 30
          : filters.dateRange === '90d'
            ? 90
            : 365
    const date =
      filters.dateRange === 'year'
        ? new Date(new Date().getFullYear(), 0, 1)
        : new Date(Date.now() - days * 86400000)
    result.created_after = date.toISOString()
  }
  return result
}

async function loadStats(): Promise<void> {
  if (!api.value) return
  try {
    stats.value = await api.value.stats('manage')
  } catch (error) {
    showError(t('errors.load'), error)
  }
}

async function loadSpaces(): Promise<void> {
  if (!api.value) return
  try {
    const data = await api.value.listSpaces('manage')
    spaces.value = data.items
  } catch (error) {
    showError(t('errors.load'), error)
  }
}

function apiSortKey(key: string): string {
  if (key === 'content') return 'title'
  return key
}

async function loadTable(focusId?: string): Promise<void> {
  if (!api.value) return
  tableLoading.value = true
  try {
    const sort = sortBy.value[0]
    const data = await api.value.listMemories({
      ...buildFilters(),
      page: tablePage.value,
      per_page: itemsPerPage.value,
      sort_by: sort ? apiSortKey(sort.key) : 'updated_at',
      sort_order: sort?.order ?? 'desc',
      mode: 'manage',
      focus_id: focusId,
    })
    memories.value = data.items
    total.value = data.total
    tablePage.value = data.page
    if (focusId && data.focus_id) {
      selectedIds.value = [focusId]
    }
  } catch (error) {
    showError(t('errors.load'), error)
  } finally {
    tableLoading.value = false
  }
}

async function loadGraph(centerId?: string, depth = 1, merge = false): Promise<void> {
  if (!api.value) return
  graphLoading.value = true
  try {
    const data = await api.value.graph({
      ...buildFilters(),
      center_id: centerId,
      depth,
      limit: graphLimit.value,
      mode: 'manage',
    })
    if (merge) {
      mergeGraph(data.nodes, data.edges)
      graphTruncated.value = graphTruncated.value || data.truncated
    } else {
      graphNodes.value = data.nodes
      graphEdges.value = data.edges
      graphTruncated.value = data.truncated
    }
  } catch (error) {
    showError(t('errors.graph'), error)
  } finally {
    graphLoading.value = false
  }
}

function mergeGraph(nodes: GraphNode[], edges: GraphEdge[]): void {
  const nodeMap = new Map(graphNodes.value.map((node) => [node.id, node]))
  for (const node of nodes) nodeMap.set(node.id, node)
  graphNodes.value = [...nodeMap.values()]
  const edgeMap = new Map(graphEdges.value.map((edge) => [edge.id, edge]))
  for (const edge of edges) edgeMap.set(edge.id, edge)
  graphEdges.value = [...edgeMap.values()]
}

async function loadAll(): Promise<void> {
  await Promise.all([loadStats(), loadSpaces(), loadTable(), loadGraph()])
}

async function refreshAll(): Promise<void> {
  await loadAll()
  showSnackbar(t('snackbar.refreshed'), 'success')
}

function applyFilters(): void {
  tablePage.value = 1
  pathResult.value = null
  pathSourceId.value = null
  void loadTable()
  void loadGraph()
}

function clearFilters(): void {
  filters.spaceId = ''
  filters.spaceKind = ''
  filters.memoryTypes = []
  filters.categories = []
  filters.statuses = []
  filters.sourceKinds = []
  filters.dateRange = 'all'
  filters.minImportance = undefined
  filters.minConfidence = undefined
  filters.pinned = false
  searchInput.value = ''
  debouncedQuery.value = ''
  applyFilters()
}

function onTableOptions(options: {
  page: number
  itemsPerPage: number
  sortBy: Array<{ key: string; order?: 'asc' | 'desc' }>
}): void {
  const pageChanged = options.page !== tablePage.value
  const sizeChanged = options.itemsPerPage !== itemsPerPage.value
  const sortChanged = JSON.stringify(options.sortBy) !== JSON.stringify(sortBy.value)
  tablePage.value = options.page
  itemsPerPage.value = options.itemsPerPage
  sortBy.value = options.sortBy.length ? options.sortBy : [{ key: 'updated_at', order: 'desc' }]
  if (pageChanged || sizeChanged || sortChanged) {
    void loadTable()
  }
}

async function openDetails(id: string): Promise<void> {
  if (!api.value) return
  selectedMemoryId.value = id
  detailsOpen.value = true
  detailsLoading.value = true
  try {
    selectedMemory.value = await api.value.getMemory(id)
  } catch (error) {
    showError(t('errors.details'), error)
    detailsOpen.value = false
  } finally {
    detailsLoading.value = false
  }
}

async function onNodeClick(id: string): Promise<void> {
  if (pathMode.value) {
    await handlePathClick(id)
    return
  }
  selectedMemoryId.value = id
  activeView.value = 'table'
  await loadTable(id)
  if (mdAndUp.value) {
    await openDetails(id)
  }
}

function onTableRowClick(item: Memory): void {
  selectedMemoryId.value = item.id
  void openDetails(item.id)
}

async function focusGraph(id: string): Promise<void> {
  if (graphRef.value?.focusNode(id)) return
  if (!api.value) return
  await loadGraph(id, 1, true)
  await nextTick()
  setTimeout(() => graphRef.value?.focusNode(id), 100)
}

async function locateInGraph(id: string): Promise<void> {
  selectedMemoryId.value = id
  detailsOpen.value = false
  activeView.value = 'graph'
  await nextTick()
  await focusGraph(id)
  setTimeout(() => graphRef.value?.focusNode(id), 180)
}

function switchView(view: 'table' | 'graph'): void {
  activeView.value = view
  detailsOpen.value = false
  if (view === 'graph') {
    // The graph is kept mounted with v-show, so re-fit after the page becomes
    // visible and restore the last table selection.
    setTimeout(() => {
      graphRef.value?.fit()
      if (selectedMemoryId.value) graphRef.value?.focusNode(selectedMemoryId.value)
    }, 120)
  }
}

async function expandNode(id: string): Promise<void> {
  await loadGraph(id, 1, true)
  await nextTick()
  setTimeout(() => graphRef.value?.focusNode(id), 100)
}

function togglePathMode(): void {
  pathMode.value = !pathMode.value
  pathSourceId.value = null
  pathResult.value = null
  if (pathMode.value) {
    showSnackbar(t('graph.pathActive'), 'info')
  }
}

async function handlePathClick(id: string): Promise<void> {
  if (!api.value) return
  if (!pathSourceId.value) {
    pathSourceId.value = id
    showSnackbar(t('graph.pathSource'), 'info')
    return
  }
  if (pathSourceId.value === id) {
    pathSourceId.value = null
    pathResult.value = null
    return
  }
  try {
    pathResult.value = await api.value.graphPath(pathSourceId.value, id)
    showSnackbar(
      pathResult.value.found
        ? t('graph.pathFound', { depth: pathResult.value.depth ?? pathResult.value.edge_ids.length })
        : t('graph.pathNotFound'),
      pathResult.value.found ? 'success' : 'info',
    )
  } catch (error) {
    showError(t('errors.graph'), error)
  }
}

function clearPath(): void {
  pathSourceId.value = null
  pathResult.value = null
  pathMode.value = false
}

function toggleFullscreen(): void {
  graphFullscreen.value = !graphFullscreen.value
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (detailsOpen.value) {
    detailsOpen.value = false
  } else if (graphFullscreen.value) {
    graphFullscreen.value = false
  }
}

function confirmAction(options: {
  title: string
  message: string
  confirmText: string
  color?: string
}): Promise<boolean> {
  confirmState.open = true
  confirmState.title = options.title
  confirmState.message = options.message
  confirmState.confirmText = options.confirmText
  confirmState.color = options.color ?? 'error'
  return new Promise<boolean>((resolve) => {
    confirmState.resolver = resolve
  })
}

function resolveConfirm(value: boolean): void {
  const resolver = confirmState.resolver
  confirmState.resolver = null
  confirmState.open = false
  resolver?.(value)
}

function onConfirmVisibility(value: boolean): void {
  if (!value) resolveConfirm(false)
}

async function saveDetails(payload: Record<string, unknown>): Promise<void> {
  if (!api.value || !selectedMemory.value) return
  try {
    selectedMemory.value = await api.value.updateMemory(selectedMemory.value.id, payload)
    showSnackbar(t('details.editSuccess'), 'success')
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.save'), error)
  }
}

async function archiveMemory(id: string): Promise<void> {
  if (!api.value) return
  const confirmed = await confirmAction({
    title: t('details.archive'),
    message: t('details.archiveConfirm'),
    confirmText: t('details.archive'),
    color: 'warning',
  })
  if (!confirmed) return
  try {
    await api.value.archiveMemory(id)
    showSnackbar(t('details.archiveSuccess'), 'success')
    if (selectedMemory.value?.id === id) selectedMemory.value = await api.value.getMemory(id)
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.archive'), error)
  }
}

async function restoreMemory(id: string): Promise<void> {
  if (!api.value) return
  try {
    await api.value.restoreMemory(id)
    showSnackbar(t('details.restoreSuccess'), 'success')
    if (selectedMemory.value?.id === id) selectedMemory.value = await api.value.getMemory(id)
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.restore'), error)
  }
}

async function deleteMemory(id: string): Promise<void> {
  if (!api.value) return
  const confirmed = await confirmAction({
    title: t('details.remove'),
    message: t('details.deleteConfirm'),
    confirmText: t('details.remove'),
    color: 'error',
  })
  if (!confirmed) return
  try {
    await api.value.deleteMemory(id)
    showSnackbar(t('details.deleteSuccess'), 'success')
    if (selectedMemory.value?.id === id) {
      selectedMemory.value = null
      detailsOpen.value = false
    }
    selectedIds.value = selectedIds.value.filter((value) => value !== id)
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.delete'), error)
  }
}

async function reinforceMemory(id: string): Promise<void> {
  if (!api.value) return
  try {
    selectedMemory.value = await api.value.reinforceMemory(id)
    showSnackbar(t('details.reinforceSuccess'), 'success')
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.save'), error)
  }
}

async function rollbackMemory(payload: { id: string; version: number }): Promise<void> {
  if (!api.value) return
  const confirmed = await confirmAction({
    title: t('details.rollback'),
    message: `${t('details.rollback')} v${payload.version}?`,
    confirmText: t('common.confirm'),
    color: 'warning',
  })
  if (!confirmed) return
  try {
    selectedMemory.value = await api.value.rollbackMemory(payload.id, payload.version)
    showSnackbar(t('details.rollbackSuccess'), 'success')
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.save'), error)
  }
}

async function deleteRelation(id: string): Promise<void> {
  if (!api.value || !selectedMemory.value) return
  try {
    await api.value.deleteRelation(id)
    selectedMemory.value = await api.value.getMemory(selectedMemory.value.id)
    showSnackbar(t('details.relationDeleted'), 'success')
    await loadGraph()
  } catch (error) {
    showError(t('errors.relation'), error)
  }
}

async function addEvidence(payload: Record<string, unknown>): Promise<void> {
  if (!api.value || !selectedMemory.value) return
  try {
    await api.value.addEvidence(selectedMemory.value.id, payload)
    selectedMemory.value = await api.value.getMemory(selectedMemory.value.id)
    showSnackbar(t('common.saved'), 'success')
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.save'), error)
  }
}

async function bulkAction(action: 'archive' | 'delete' | 'restore'): Promise<void> {
  if (!api.value || !selectedIds.value.length) return
  if (action === 'delete') {
    const confirmed = await confirmAction({
      title: t('table.delete'),
      message: t('details.deleteConfirm'),
      confirmText: t('table.delete'),
      color: 'error',
    })
    if (!confirmed) return
  }
  try {
    const result = await api.value.bulkAction([...selectedIds.value], action)
    showSnackbar(t('snackbar.bulkDone', { count: result.processed.length }), 'success')
    if (result.failed.length) {
      showSnackbar(t('snackbar.partial', { count: result.failed.length }), 'error')
    }
    selectedIds.value = []
    if (selectedMemory.value && result.processed.includes(selectedMemory.value.id)) {
      selectedMemory.value = null
      detailsOpen.value = false
    }
    await Promise.all([loadTable(), loadGraph(), loadStats()])
  } catch (error) {
    showError(t('errors.save'), error)
  }
}

async function exportMemories(format: 'json' | 'csv'): Promise<void> {
  if (!api.value) return
  try {
    const { blob, filename } = await api.value.exportMemories(buildFilters(), format)
    triggerDownload(blob, filename)
    showSnackbar(t('snackbar.exported'), 'success')
  } catch (error) {
    showError(t('errors.export'), error)
  }
}

async function createMemory(payload: Record<string, unknown>): Promise<void> {
  if (!api.value) return
  creating.value = true
  try {
    const result = await api.value.createMemory(payload as unknown as CreateMemoryInput)
    createOpen.value = false
    showSnackbar(
      result.action === 'reinforced' ? t('create.reinforced') : t('create.success'),
      'success',
    )
    await loadAll()
    if (result.action === 'created') {
      await openDetails(result.memory.id)
    }
  } catch (error) {
    showError(t('errors.save'), error)
  } finally {
    creating.value = false
  }
}

function openFilePicker(): void {
  fileInput.value?.click()
}

function onFileSelected(event: Event): void {
  const input = event.target as HTMLInputElement
  importFile.value = input.files?.[0] ?? null
}

function openImport(): void {
  importFile.value = null
  importTargetSpace.value = ''
  importDeduplicate.value = true
  importOpen.value = true
}

async function submitImport(): Promise<void> {
  if (!api.value) return
  if (!importFile.value) {
    showSnackbar(t('importDialog.fileRequired'), 'error')
    return
  }
  const name = importFile.value.name.toLowerCase()
  const format = name.endsWith('.csv') ? 'csv' : name.endsWith('.json') ? 'json' : null
  if (!format) {
    showSnackbar(t('importDialog.formatError'), 'error')
    return
  }
  importing.value = true
  try {
    const text = await importFile.value.text()
    const payload = format === 'json' ? JSON.parse(text) : text
    const result = await api.value.importMemories({
      format,
      payload,
      target_space_id: importTargetSpace.value || undefined,
      deduplicate: importDeduplicate.value,
    })
    showSnackbar(
      t('importDialog.success', {
        created: result.created,
        reinforced: result.reinforced,
        failed: result.failed,
      }),
      result.failed ? 'info' : 'success',
    )
    importOpen.value = false
    await loadAll()
  } catch (error) {
    showError(t('importDialog.error'), error)
  } finally {
    importing.value = false
  }
}

function onSearchInput(value: string | null): void {
  const next = value ?? ''
  searchInput.value = next
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    debouncedQuery.value = next
    applyFilters()
  }, 320)
}

watch(
  () => visibleColumns.value,
  () => persist(),
  { deep: true },
)

watch(
  () => graphLayout.value,
  () => setTimeout(() => graphRef.value?.runLayout(), 30),
)

watch(
  () => clusterBy.value,
  () => setTimeout(() => graphRef.value?.fit(), 220),
)

watch(
  () => context.value,
  (value) => {
    if (value) {
      itemsPerPage.value = props.pageSize ?? value.pageSize
      void loadAll()
    }
  },
)

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  if (api.value) {
    void loadAll()
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  if (searchTimer) clearTimeout(searchTimer)
})

const pageTitle = computed(() => props.title ?? context.value?.title ?? t('page.title'))
const graphPathNodeIds = computed(() => pathResult.value?.node_ids ?? [])
const graphPathEdgeIds = computed(() => pathResult.value?.edge_ids ?? [])
const effectiveColumns = computed(() => {
  if (!mdAndUp.value) return ['content']
  if (!lgAndUp.value) return ['content', 'quality', 'status', 'actions']
  return visibleColumns.value
})
</script>

<template>
  <div class="memory-explorer" :class="{ 'memory-explorer--dark': graphColors.dark }">
    <div v-if="!api" class="memory-explorer__unconfigured">
      <v-icon size="40" color="warning">mdi-connection</v-icon>
      <p>{{ t('errors.noClient') }}</p>
    </div>

    <template v-else>
      <header class="explorer-hero">
        <div class="explorer-hero__copy">
          <p class="explorer-eyebrow">{{ t('page.eyebrow') }}</p>
          <h1>{{ pageTitle }}</h1>
          <p>{{ t('page.subtitle') }}</p>
        </div>
        <div class="explorer-hero__stats">
          <div class="stat-card">
            <div class="stat-card__icon stat-card__icon--mint">
              <v-icon size="19">mdi-memory</v-icon>
            </div>
            <div>
              <span>{{ t('stats.total') }}</span>
              <strong>{{ stats?.total ?? '—' }}</strong>
              <small>{{ t('stats.active') }} · {{ stats?.active ?? 0 }}</small>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-card__icon stat-card__icon--blue">
              <v-icon size="19">mdi-graph-outline</v-icon>
            </div>
            <div>
              <span>{{ t('stats.relations') }}</span>
              <strong>{{ stats?.relations ?? '—' }}</strong>
              <small>{{ stats?.tasks.pending ?? 0 }} {{ t('stats.tasks') }}</small>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-card__icon stat-card__icon--amber">
              <v-icon size="19">mdi-layers-triple-outline</v-icon>
            </div>
            <div>
              <span>{{ t('stats.pending') }}</span>
              <strong>{{ stats?.pending_consolidation ?? '—' }}</strong>
              <small>{{ stats?.tasks.running ?? 0 }} running</small>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-card__icon stat-card__icon--violet">
              <v-icon size="19">mdi-database-outline</v-icon>
            </div>
            <div>
              <span>{{ t('stats.spaces') }}</span>
              <strong>{{ stats?.spaces ?? '—' }}</strong>
              <small>
                {{ stats?.storage.fts_enabled ? 'FTS5' : 'LIKE' }} ·
                {{ stats?.storage.vector_backend ?? 'sqlite' }}
              </small>
            </div>
          </div>
        </div>
      </header>

      <div class="explorer-toolbar">
        <v-text-field
          :model-value="searchInput"
          class="explorer-search"
          :placeholder="t('toolbar.search')"
          prepend-inner-icon="mdi-magnify"
          density="compact"
          variant="solo-filled"
          hide-details
          clearable
          @update:model-value="onSearchInput"
        />
        <v-btn
          variant="tonal"
          :color="activeFilterCount ? 'primary' : undefined"
          prepend-icon="mdi-filter-variant"
          class="toolbar-btn"
          @click="filtersOpen = !filtersOpen"
        >
          {{ t('toolbar.filters') }}
          <v-chip v-if="activeFilterCount" size="x-small" color="primary" class="ml-2">
            {{ activeFilterCount }}
          </v-chip>
        </v-btn>
        <v-btn icon variant="text" :title="t('common.refresh')" @click="refreshAll">
          <v-icon>mdi-refresh</v-icon>
        </v-btn>
        <v-spacer />
        <v-btn
          class="create-button"
          variant="flat"
          color="primary"
          prepend-icon="mdi-plus"
          :title="t('create.button')"
          @click="createOpen = true"
        >
          {{ t('create.button') }}
        </v-btn>
        <v-btn
          class="toolbar-import"
          variant="text"
          prepend-icon="mdi-import"
          :title="t('toolbar.import')"
          @click="openImport"
        >
          {{ t('toolbar.import') }}
        </v-btn>
        <v-menu location="bottom end" :offset="6">
          <template #activator="{ props: menuProps }">
            <v-btn
              class="toolbar-export"
              variant="tonal"
              color="primary"
              prepend-icon="mdi-export"
              :title="t('toolbar.export')"
              v-bind="menuProps"
            >
              {{ t('toolbar.export') }}
            </v-btn>
          </template>
          <v-list density="compact" class="toolbar-menu">
            <v-list-item @click="exportMemories('json')">
              <template #prepend><v-icon size="17">mdi-code-json</v-icon></template>
              <v-list-item-title>{{ t('toolbar.exportJson') }}</v-list-item-title>
            </v-list-item>
            <v-list-item @click="exportMemories('csv')">
              <template #prepend><v-icon size="17">mdi-file-delimited-outline</v-icon></template>
              <v-list-item-title>{{ t('toolbar.exportCsv') }}</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </div>

      <v-expand-transition>
        <div v-if="filtersOpen" class="explorer-filters">
          <v-select
            v-model="filters.spaceId"
            :items="spaceOptions"
            item-title="title"
            item-value="value"
            :label="t('filters.space')"
            density="compact"
            clearable
            hide-details
            @update:model-value="applyFilters"
          />
          <v-select
            v-model="filters.memoryTypes"
            :items="typeOptions"
            item-title="title"
            item-value="value"
            :label="t('filters.type')"
            density="compact"
            multiple
            chips
            closable-chips
            hide-details
            @update:model-value="applyFilters"
          />
          <v-select
            v-model="filters.categories"
            :items="categoryOptions"
            item-title="title"
            item-value="value"
            :label="t('filters.category')"
            density="compact"
            multiple
            chips
            closable-chips
            hide-details
            @update:model-value="applyFilters"
          />
          <v-select
            v-model="filters.statuses"
            :items="statusOptions"
            item-title="title"
            item-value="value"
            :label="t('filters.status')"
            density="compact"
            multiple
            chips
            closable-chips
            hide-details
            @update:model-value="applyFilters"
          />
          <v-select
            v-model="filters.sourceKinds"
            :items="sourceOptions"
            item-title="title"
            item-value="value"
            :label="t('filters.source')"
            density="compact"
            multiple
            chips
            closable-chips
            hide-details
            @update:model-value="applyFilters"
          />
          <v-select
            v-model="filters.dateRange"
            :items="[
              { value: 'all', title: t('toolbar.allTime') },
              { value: '7d', title: t('toolbar.last7') },
              { value: '30d', title: t('toolbar.last30') },
              { value: '90d', title: t('toolbar.last90') },
              { value: 'year', title: t('toolbar.thisYear') },
            ]"
            item-title="title"
            item-value="value"
            :label="t('toolbar.timeRange')"
            density="compact"
            hide-details
            @update:model-value="applyFilters"
          />
          <div class="filter-slider">
            <span>{{ t('toolbar.importance') }}</span>
            <v-slider
              v-model="filters.minImportance"
              :min="0"
              :max="1"
              :step="0.05"
              color="warning"
              hide-details
              density="compact"
              @update:model-value="applyFilters"
            />
          </div>
          <div class="filter-slider">
            <span>{{ t('toolbar.confidence') }}</span>
            <v-slider
              v-model="filters.minConfidence"
              :min="0"
              :max="1"
              :step="0.05"
              color="success"
              hide-details
              density="compact"
              @update:model-value="applyFilters"
            />
          </div>
          <v-checkbox
            v-model="filters.pinned"
            :label="t('toolbar.pinned')"
            density="compact"
            hide-details
            @update:model-value="applyFilters"
          />
          <v-btn variant="text" color="primary" @click="clearFilters">{{ t('toolbar.clear') }}</v-btn>
        </div>
      </v-expand-transition>

      <div class="view-switch" role="tablist" :aria-label="t('view.switchLabel')">
        <button
          type="button"
          class="view-tab"
          :class="{ 'view-tab--active': activeView === 'table' }"
          role="tab"
          :aria-selected="activeView === 'table'"
          @click="switchView('table')"
        >
          <v-icon size="18">mdi-format-list-bulleted</v-icon>
          <span>{{ t('table.content') }}</span>
          <em>{{ total }}</em>
        </button>
        <button
          type="button"
          class="view-tab"
          :class="{ 'view-tab--active': activeView === 'graph' }"
          role="tab"
          :aria-selected="activeView === 'graph'"
          @click="switchView('graph')"
        >
          <v-icon size="18">mdi-graph-outline</v-icon>
          <span>{{ t('graph.title') }}</span>
          <em>{{ graphNodes.length }}</em>
        </button>
        <span class="view-switch__hint">{{ t('view.hint') }}</span>
      </div>

      <section v-show="activeView === 'table'" class="explorer-panel explorer-table" role="tabpanel">
        <header class="panel-head panel-head--table">
          <div>
            <p class="panel-kicker">{{ t('table.content') }}</p>
            <div class="panel-meta">
              <span>{{ total }} {{ t('stats.total') }}</span>
              <span v-if="selectedIds.length" class="selection-count">
                · {{ t('table.selected', { count: selectedIds.length }) }}
              </span>
            </div>
          </div>
          <div class="table-controls">
            <template v-if="selectedIds.length">
              <v-btn size="small" variant="text" @click="selectedIds = []">
                {{ t('table.clearSelection') }}
              </v-btn>
              <v-btn
                size="small"
                variant="tonal"
                color="secondary"
                prepend-icon="mdi-archive-outline"
                @click="bulkAction('archive')"
              >
                {{ t('table.archive') }}
              </v-btn>
              <v-btn
                size="small"
                variant="tonal"
                color="error"
                prepend-icon="mdi-delete-outline"
                @click="bulkAction('delete')"
              >
                {{ t('table.delete') }}
              </v-btn>
            </template>
            <v-menu location="bottom end" :offset="6">
              <template #activator="{ props: menuProps }">
                <v-btn icon variant="text" size="small" :title="t('table.columns')" v-bind="menuProps">
                  <v-icon>mdi-view-column-outline</v-icon>
                </v-btn>
              </template>
              <v-list density="compact" class="columns-menu">
                <v-list-item v-for="column in allColumns" :key="column.key">
                  <template #prepend>
                    <v-checkbox-btn v-model="visibleColumns" :value="column.key" density="compact" />
                  </template>
                  <v-list-item-title>{{ column.title }}</v-list-item-title>
                </v-list-item>
              </v-list>
            </v-menu>
          </div>
        </header>

        <div class="table-stage">
          <MemoryTable
            :items="memories"
            :total="total"
            :loading="tableLoading"
            :page="tablePage"
            :items-per-page="itemsPerPage"
            :sort-by="sortBy"
            :selected="selectedIds"
            :locale="locale"
            :visible-columns="effectiveColumns"
            :mobile="!mdAndUp"
            @update:options="onTableOptions"
            @update:selected="selectedIds = $event"
            @row-click="onTableRowClick"
            @view="openDetails($event.id)"
            @locate="locateInGraph($event.id)"
            @archive="archiveMemory($event.id)"
            @restore="restoreMemory($event.id)"
            @delete="deleteMemory($event.id)"
          />
        </div>
      </section>

      <section
        v-show="activeView === 'graph'"
        class="explorer-panel explorer-graph"
        :class="{ 'explorer-graph--fullscreen': graphFullscreen }"
        role="tabpanel"
      >
        <header class="panel-head">
          <div>
            <p class="panel-kicker">{{ t('graph.title') }}</p>
            <div class="panel-meta">
              <span>{{ t('graph.nodes', { count: graphNodes.length }) }}</span>
              <span>·</span>
              <span>{{ t('graph.edges', { count: graphEdges.length }) }}</span>
              <v-chip v-if="graphTruncated" size="x-small" color="warning" variant="tonal" class="ml-2">
                {{ t('graph.truncated', { count: graphLimit }) }}
              </v-chip>
            </div>
          </div>
          <div class="graph-controls">
            <v-select
              v-model="graphLayout"
              :items="[
                { value: 'cose', title: t('graph.layouts.cose') },
                { value: 'concentric', title: t('graph.layouts.concentric') },
                { value: 'grid', title: t('graph.layouts.grid') },
                { value: 'breadthfirst', title: t('graph.layouts.breadthfirst') },
                { value: 'circle', title: t('graph.layouts.circle') },
              ]"
              item-title="title"
              item-value="value"
              density="compact"
              variant="outlined"
              hide-details
              class="graph-select"
            />
            <v-select
              v-model="clusterBy"
              :items="[
                { value: 'none', title: t('graph.clusterNone') },
                { value: 'space', title: t('graph.clusterSpace') },
                { value: 'type', title: t('graph.clusterType') },
              ]"
              item-title="title"
              item-value="value"
              density="compact"
              variant="outlined"
              hide-details
              class="graph-select"
            />
            <v-select
              v-model="graphLimit"
              :items="[100, 150, 250, 400]"
              density="compact"
              variant="outlined"
              hide-details
              class="graph-limit"
              @update:model-value="loadGraph()"
            />
            <v-btn icon variant="text" size="small" :title="t('graph.path')" :color="pathMode ? 'accent' : undefined" @click="togglePathMode">
              <v-icon>mdi-map-marker-path</v-icon>
            </v-btn>
            <v-btn icon variant="text" size="small" :title="t('graph.fit')" @click="graphRef?.fit()">
              <v-icon>mdi-fit-to-page-outline</v-icon>
            </v-btn>
            <v-btn icon variant="text" size="small" :title="graphFullscreen ? t('graph.exitFullscreen') : t('graph.fullscreen')" @click="toggleFullscreen">
              <v-icon>{{ graphFullscreen ? 'mdi-fullscreen-exit' : 'mdi-fullscreen' }}</v-icon>
            </v-btn>
          </div>
        </header>

        <div v-if="pathMode" class="path-banner">
          <v-icon size="16" color="accent">mdi-map-marker-path</v-icon>
          <span v-if="!pathSourceId">{{ t('graph.pathActive') }}</span>
          <span v-else-if="!pathResult">{{ t('graph.pathSource') }}</span>
          <span v-else>
            {{ pathResult.found ? t('graph.pathFound', { depth: pathResult.depth ?? pathResult.edge_ids.length }) : t('graph.pathNotFound') }}
          </span>
          <v-btn size="x-small" variant="text" color="accent" class="ml-auto" @click="clearPath">
            {{ t('graph.pathClear') }}
          </v-btn>
        </div>

        <div class="graph-stage">
          <MemoryGraph
            ref="graphRef"
            :nodes="graphNodes"
            :edges="graphEdges"
            :selected-id="pathMode ? pathSourceId : selectedMemoryId"
            :path-node-ids="graphPathNodeIds"
            :path-edge-ids="graphPathEdgeIds"
            :locale="locale"
            :colors="graphColors"
            :layout="graphLayout"
            :cluster-by="clusterBy"
            :loading="graphLoading"
            :fullscreen="graphFullscreen"
            :compact="!mdAndUp"
            @node-click="onNodeClick"
            @node-expand="expandNode"
            @background-click="pathSourceId = null"
          />
        </div>

        <footer class="graph-legend">
          <span v-for="item in legendItems" :key="item.label" class="legend-item">
            <i :style="{ background: item.color }" />
            {{ item.label }}
          </span>
        </footer>
      </section>

      <MemoryDetailsDrawer
        v-model="detailsOpen"
        :memory="selectedMemory"
        :loading="detailsLoading"
        :locale="locale"
        @save="saveDetails"
        @archive="archiveMemory"
        @restore="restoreMemory"
        @remove="deleteMemory"
        @reinforce="reinforceMemory"
        @locate="locateInGraph"
        @rollback="rollbackMemory"
        @delete-relation="deleteRelation"
        @add-evidence="addEvidence"
      />

      <input ref="fileInput" type="file" accept=".json,.csv" class="hidden-input" @change="onFileSelected" />

      <MemoryCreateDialog
        v-model="createOpen"
        :spaces="spaces"
        :locale="locale"
        :saving="creating"
        @save="createMemory"
      />

      <v-dialog v-model="importOpen" max-width="580" persistent>
        <v-card class="import-dialog">
          <v-card-title class="import-dialog__title">
            <v-icon color="primary" class="mr-2">mdi-import</v-icon>
            {{ t('importDialog.title') }}
          </v-card-title>
          <v-card-text>
            <div class="import-file" @click="openFilePicker">
              <v-icon size="28" color="primary">mdi-file-upload-outline</v-icon>
              <div>
                <strong>{{ importFile?.name || t('importDialog.choose') }}</strong>
                <small>{{ t('importDialog.file') }} · JSON / CSV</small>
              </div>
              <v-btn size="small" variant="tonal" color="primary">{{ t('importDialog.file') }}</v-btn>
            </div>
            <v-select
              v-model="importTargetSpace"
              :items="spaceOptions"
              item-title="title"
              item-value="value"
              :label="t('importDialog.targetSpace')"
              density="compact"
              clearable
              :placeholder="t('importDialog.autoSpace')"
              class="mt-4"
            />
            <v-checkbox
              v-model="importDeduplicate"
              :label="t('importDialog.deduplicate')"
              density="compact"
              hide-details
            />
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" :disabled="importing" @click="importOpen = false">
              {{ t('common.cancel') }}
            </v-btn>
            <v-btn color="primary" variant="flat" :loading="importing" @click="submitImport">
              {{ importing ? t('importDialog.importing') : t('importDialog.submit') }}
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-dialog
        :model-value="confirmState.open"
        max-width="460"
        @update:model-value="onConfirmVisibility"
      >
        <v-card class="confirm-dialog">
          <v-card-title>{{ confirmState.title || t('confirm.title') }}</v-card-title>
          <v-card-text>{{ confirmState.message }}</v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn variant="text" @click="resolveConfirm(false)">{{ t('common.cancel') }}</v-btn>
            <v-btn :color="confirmState.color" variant="flat" @click="resolveConfirm(true)">
              {{ confirmState.confirmText || t('common.confirm') }}
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

      <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="4200" location="bottom right">
        {{ snackbar.message }}
        <template #actions>
          <v-btn variant="text" @click="snackbar.show = false">{{ t('common.close') }}</v-btn>
        </template>
      </v-snackbar>
    </template>
  </div>
</template>

<style scoped>
.memory-explorer {
  --memory-hero: rgb(var(--v-theme-surface));
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
  padding: 4px 2px 20px;
  color: rgb(var(--v-theme-on-surface));
}

.memory-explorer__unconfigured {
  display: flex;
  min-height: 360px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 48px;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 18px;
  color: rgba(var(--v-theme-on-surface), 0.62);
  text-align: center;
}

.explorer-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  padding: 6px 4px 2px;
}

.explorer-hero__copy {
  max-width: 520px;
}

.explorer-eyebrow,
.panel-kicker {
  margin: 0 0 5px;
  color: rgb(var(--v-theme-primary));
  font-size: 9.5px;
  font-weight: 750;
  letter-spacing: 0.2em;
  text-transform: uppercase;
}

.explorer-hero__copy h1 {
  margin: 0;
  font-size: clamp(24px, 2.6vw, 34px);
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1.1;
}

.explorer-hero__copy > p:last-child {
  margin: 9px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 12px;
  line-height: 1.6;
}

.explorer-hero__stats {
  display: grid;
  flex: 1;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 9px;
  max-width: 760px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.075);
  border-radius: 13px;
  background:
    linear-gradient(145deg, rgba(var(--v-theme-on-surface), 0.025), transparent 62%),
    rgb(var(--v-theme-surface));
}

.stat-card__icon {
  display: grid;
  width: 36px;
  height: 36px;
  flex: none;
  place-items: center;
  border-radius: 10px;
}

.stat-card__icon--mint {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}

.stat-card__icon--blue {
  background: rgba(var(--v-theme-info), 0.12);
  color: rgb(var(--v-theme-info));
}

.stat-card__icon--amber {
  background: rgba(var(--v-theme-warning), 0.12);
  color: rgb(var(--v-theme-warning));
}

.stat-card__icon--violet {
  background: rgba(var(--v-theme-secondary), 0.12);
  color: rgb(var(--v-theme-secondary));
}

.stat-card > div:last-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.stat-card span {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.stat-card strong {
  overflow: hidden;
  font-size: 21px;
  font-weight: 680;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
  line-height: 1.15;
  text-overflow: ellipsis;
}

.stat-card small {
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.42);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.explorer-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.075);
  border-radius: 14px;
  background: rgba(var(--v-theme-surface), 0.82);
  backdrop-filter: blur(10px);
}

.explorer-search {
  max-width: 440px;
}

.explorer-search :deep(.v-field) {
  border-radius: 10px;
}

.toolbar-btn {
  text-transform: none;
}

.toolbar-menu,
.columns-menu {
  min-width: 190px;
}

.explorer-filters {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr));
  gap: 10px 14px;
  padding: 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.075);
  border-radius: 14px;
  background: rgba(var(--v-theme-surface), 0.9);
}

.filter-slider {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.filter-slider span {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 10px;
}

.view-switch {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 14px;
  background: rgba(var(--v-theme-surface), 0.82);
}

.view-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.55);
  cursor: pointer;
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  transition:
    background 0.16s ease,
    color 0.16s ease,
    box-shadow 0.16s ease;
}

.view-tab:hover {
  background: rgba(var(--v-theme-primary), 0.06);
  color: rgb(var(--v-theme-on-surface));
}

.view-tab--active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-on-surface));
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary), 0.18);
}

.view-tab--active :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}

.view-tab em {
  min-width: 20px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.52);
  font-size: 9px;
  font-style: normal;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.view-tab--active em {
  background: rgba(var(--v-theme-primary), 0.16);
  color: rgb(var(--v-theme-primary));
}

.view-switch__hint {
  margin-left: auto;
  padding-right: 10px;
  color: rgba(var(--v-theme-on-surface), 0.36);
  font-size: 10px;
}

.explorer-panel {
  display: flex;
  width: 100%;
  height: clamp(540px, calc(100vh - 405px), 960px);
  min-width: 0;
  flex-direction: column;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 16px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.explorer-graph {
  position: relative;
}

.explorer-graph--fullscreen {
  position: fixed;
  inset: 14px;
  z-index: 2300;
  width: auto !important;
  height: auto !important;
  border-radius: 18px;
  box-shadow: 0 40px 120px rgba(0, 0, 0, 0.55);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 13px 15px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.065);
}

.panel-head > div:first-child {
  min-width: 150px;
  flex: 1 1 150px;
}

.panel-head--table {
  align-items: center;
}

.panel-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  color: rgba(var(--v-theme-on-surface), 0.46);
  font-size: 10.5px;
}

.selection-count {
  color: rgb(var(--v-theme-primary));
}

.graph-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
}

.graph-select {
  width: 112px;
}

.graph-limit {
  width: 78px;
}

.graph-select :deep(.v-field),
.graph-limit :deep(.v-field) {
  border-radius: 8px;
  font-size: 11px;
}

.graph-stage {
  position: relative;
  flex: 1;
  min-height: 0;
}

.path-banner {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  background: rgba(var(--v-theme-accent), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 10.5px;
}

.graph-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 9px 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  background: rgba(var(--v-theme-on-surface), 0.012);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 9.5px;
}

.legend-item i {
  width: 7px;
  height: 7px;
  border-radius: 999px;
}

.legend-note {
  margin-left: auto;
  color: rgba(var(--v-theme-on-surface), 0.38);
  font-size: 9px;
}

.explorer-table {
  width: 100%;
}

.table-controls {
  display: flex;
  align-items: center;
  gap: 4px;
}

.table-stage {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}

.table-stage :deep(.v-data-table__wrapper) {
  overflow: auto;
}

.hidden-input {
  display: none;
}

.import-dialog__title {
  display: flex;
  align-items: center;
  font-size: 16px;
  font-weight: 650;
}

.import-file {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  border: 1px dashed rgba(var(--v-theme-primary), 0.35);
  border-radius: 12px;
  background: rgba(var(--v-theme-primary), 0.035);
  cursor: pointer;
  transition: background 0.15s ease;
}

.import-file:hover {
  background: rgba(var(--v-theme-primary), 0.07);
}

.import-file > div {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.import-file strong {
  overflow: hidden;
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-file small {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 10px;
}

.confirm-dialog {
  border-radius: 14px !important;
}

@media (max-width: 1280px) {
  .explorer-hero {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
  }

  .explorer-hero__stats {
    width: 100%;
    max-width: none;
  }
}

@media (max-width: 960px) {
  .explorer-panel {
    height: auto;
  }

  .explorer-graph {
    height: 520px;
  }

  .explorer-table {
    height: 680px;
    max-height: 680px;
  }

  .view-switch__hint {
    display: none;
  }

  .explorer-filters {
    grid-template-columns: repeat(2, minmax(140px, 1fr));
  }

  .explorer-hero__stats {
    grid-template-columns: repeat(2, minmax(140px, 1fr));
  }
}

@media (max-width: 600px) {
  .memory-explorer {
    padding: 0 0 12px;
  }

  .explorer-toolbar {
    flex-wrap: wrap;
  }

  .explorer-toolbar .v-spacer {
    display: none;
  }

  .create-button {
    min-width: 0 !important;
    padding-inline: 12px !important;
  }

  .create-button :deep(.v-btn__content) {
    display: none;
  }

  .toolbar-import,
  .toolbar-export {
    min-width: 0 !important;
    padding-inline: 12px !important;
  }

  .toolbar-import :deep(.v-btn__content),
  .toolbar-export :deep(.v-btn__content) {
    display: none;
  }

  .explorer-search {
    max-width: none;
    width: 100%;
    flex: 1 1 100%;
  }

  .view-switch {
    width: 100%;
  }

  .view-tab {
    flex: 1;
    justify-content: center;
    padding: 0 8px;
  }

  .explorer-graph {
    height: 460px;
  }

  .explorer-table {
    height: 640px;
    max-height: 640px;
  }

  .explorer-hero__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stat-card {
    padding: 10px;
  }

  .stat-card strong {
    font-size: 18px;
  }

  .explorer-filters {
    grid-template-columns: 1fr;
  }

  .graph-controls {
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 2px;
  }

  .graph-controls .graph-select {
    width: 84px;
  }

  .graph-controls .graph-limit {
    width: 70px;
  }

  .graph-controls :deep(.v-btn) {
    width: 28px;
    min-width: 28px;
  }

  .graph-legend {
    display: none;
  }
}
</style>
