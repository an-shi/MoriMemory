<script setup lang="ts">
import { computed } from 'vue'

import { useMessages } from '../i18n'
import type { LocaleCode, Memory, MemoryCategory, MemoryStatus, MemoryType } from '../types'

export interface MemoryTableHeader {
  title: string
  key: string
  sortable?: boolean
  width?: string | number
  align?: 'start' | 'center' | 'end'
  nowrap?: boolean
}

const props = withDefaults(
  defineProps<{
    items: Memory[]
    total: number
    loading?: boolean
    page: number
    itemsPerPage: number
    sortBy?: Array<{ key: string; order?: 'asc' | 'desc' }>
    selected?: string[]
    locale?: LocaleCode
    visibleColumns?: string[]
    mobile?: boolean
  }>(),
  {
    loading: false,
    sortBy: () => [{ key: 'updated_at', order: 'desc' }],
    selected: () => [],
    locale: 'zh-CN',
    visibleColumns: () => [
      'content',
      'memory_type',
      'category',
      'quality',
      'status',
      'updated_at',
      'actions',
    ],
    mobile: false,
  },
)

const emit = defineEmits<{
  (event: 'update:options', options: { page: number; itemsPerPage: number; sortBy: Array<{ key: string; order?: 'asc' | 'desc' }> }): void
  (event: 'update:selected', ids: string[]): void
  (event: 'row-click', item: Memory): void
  (event: 'view', item: Memory): void
  (event: 'locate', item: Memory): void
  (event: 'archive', item: Memory): void
  (event: 'restore', item: Memory): void
  (event: 'delete', item: Memory): void
}>()

const { t } = useMessages(() => props.locale)

const allHeaders = computed<MemoryTableHeader[]>(() => [
  {
    title: t('table.content'),
    key: 'content',
    sortable: true,
    width: props.mobile ? undefined : '40%',
  },
  { title: t('table.type'), key: 'memory_type', sortable: true, width: 100 },
  { title: t('table.category'), key: 'category', sortable: true, width: 92 },
  { title: t('table.space'), key: 'space', sortable: false, width: 120 },
  { title: t('table.quality'), key: 'quality', sortable: false, width: 110 },
  { title: t('table.importance'), key: 'importance', sortable: true, width: 112 },
  { title: t('table.confidence'), key: 'confidence', sortable: true, width: 112 },
  { title: t('table.status'), key: 'status', sortable: true, width: 80 },
  { title: t('table.evidence'), key: 'evidence_count', sortable: false, width: 68, align: 'center' },
  { title: t('table.relations'), key: 'relation_count', sortable: false, width: 68, align: 'center' },
  { title: t('table.updated'), key: 'updated_at', sortable: true, width: 144 },
  { title: t('table.actions'), key: 'actions', sortable: false, width: 104, align: 'end' },
])

const headers = computed(() =>
  allHeaders.value.filter((header) => props.visibleColumns.includes(header.key)),
)

const statusColors: Record<MemoryStatus, string> = {
  active: 'success',
  archived: 'secondary',
  superseded: 'warning',
  disputed: 'error',
  deleted: 'error',
}

const typeColors: Record<MemoryType, string> = {
  working: 'secondary',
  short_term: 'info',
  long_term: 'primary',
  episodic: 'warning',
  semantic: 'success',
  associative: 'accent',
}

const categoryColors: Record<MemoryCategory, string> = {
  identity: 'primary',
  fact: 'info',
  preference: 'accent',
  relationship: 'pink',
  event: 'warning',
  goal: 'success',
  plan: 'success',
  skill: 'teal',
  opinion: 'purple',
  habit: 'lime',
  context: 'secondary',
  summary: 'secondary',
  other: 'secondary',
}

function onOptions(options: {
  page: number
  itemsPerPage: number
  sortBy: Array<{ key: string; order?: 'asc' | 'desc' }>
}): void {
  emit('update:options', {
    page: options.page,
    itemsPerPage: options.itemsPerPage,
    sortBy: options.sortBy ?? [],
  })
}

function onRowClick(event: Event, payload: unknown): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, .v-btn, .v-checkbox-btn, .v-selection-control')) return
  const item =
    payload && typeof payload === 'object' && 'item' in payload
      ? ((payload as { item?: Memory }).item ?? null)
      : (payload as Memory | null)
  if (item) emit('row-click', item)
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(props.locale, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function preview(content: string): string {
  return content.length > 150 ? `${content.slice(0, 150)}…` : content
}

function percent(value: number): number {
  return Math.round(Math.max(0, Math.min(1, value)) * 100)
}

const selectionModel = computed({
  get: () => props.selected,
  set: (value: string[]) => emit('update:selected', value),
})

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.itemsPerPage))))

const rangeStart = computed(() => (props.total === 0 ? 0 : (props.page - 1) * props.itemsPerPage + 1))

const rangeEnd = computed(() => Math.min(props.total, props.page * props.itemsPerPage))

function emitOptions(page: number, itemsPerPage: number): void {
  emit('update:options', {
    page,
    itemsPerPage,
    sortBy: props.sortBy,
  })
}

function onPage(value: number): void {
  if (value < 1 || value > pageCount.value || value === props.page) return
  emitOptions(value, props.itemsPerPage)
}

function onPageSize(value: unknown): void {
  const size = Number(value)
  if (!Number.isFinite(size) || size <= 0) return
  emitOptions(1, size)
}
</script>

<template>
  <div class="memory-table-wrap">
    <v-data-table-server
      v-model="selectionModel"
      class="memory-table"
      :headers="headers"
      :items="items"
      :items-length="total"
      :loading="loading"
      :page="page"
      :items-per-page="itemsPerPage"
      :sort-by="sortBy"
      item-value="id"
      show-select
      density="comfortable"
      hover
      fixed-header
      must-sort
      hide-default-footer
      @update:options="onOptions"
      @click:row="onRowClick"
    >
    <template #item.content="{ item }">
      <div class="memory-cell">
        <div class="memory-cell__title">
          <v-icon v-if="item.pinned" size="13" color="warning" class="mr-1">mdi-pin</v-icon>
          {{ item.title || preview(item.content) }}
        </div>
        <div v-if="item.title && item.content !== item.title" class="memory-cell__preview">{{ preview(item.content) }}</div>
        <div class="memory-cell__meta">
          <v-chip
            v-if="!visibleColumns.includes('memory_type')"
            size="x-small"
            variant="tonal"
            :color="typeColors[item.memory_type]"
            class="mr-1"
          >
            {{ t(`type.${item.memory_type}`) }}
          </v-chip>
          <v-chip
            v-if="!visibleColumns.includes('category')"
            size="x-small"
            variant="text"
            :color="categoryColors[item.category]"
            class="memory-cell__category"
          >
            {{ t(`category.${item.category}`) }}
          </v-chip>
          <v-chip
            v-if="item.tags.length"
            size="x-small"
            variant="text"
            class="memory-cell__tag"
          >
            #{{ item.tags.slice(0, 2).join(' #') }}
          </v-chip>
        </div>
      </div>
    </template>

    <template #item.memory_type="{ item }">
      <v-chip size="small" variant="tonal" :color="typeColors[item.memory_type]">
        {{ t(`type.${item.memory_type}`) }}
      </v-chip>
    </template>

    <template #item.category="{ item }">
      <v-chip size="small" variant="text" :color="categoryColors[item.category]">
        {{ t(`category.${item.category}`) }}
      </v-chip>
    </template>

    <template #item.space="{ item }">
      <div v-if="item.space" class="space-cell">
        <v-icon size="14" color="primary">
          {{
            item.space.kind === 'character'
              ? 'mdi-account-box-outline'
              : item.space.kind === 'conversation'
                ? 'mdi-message-text-outline'
                : 'mdi-account-outline'
          }}
        </v-icon>
        <span>{{ t(`spaceKind.${item.space.kind}`) }}</span>
      </div>
      <span v-else class="text-disabled">—</span>
    </template>

    <template #item.quality="{ item }">
      <div class="quality-cell">
        <div>
          <span>I</span>
          <v-progress-linear
            :model-value="percent(item.importance)"
            :color="item.importance >= 0.75 ? 'warning' : 'primary'"
            height="4"
            rounded
          />
          <em>{{ percent(item.importance) }}</em>
        </div>
        <div>
          <span>C</span>
          <v-progress-linear
            :model-value="percent(item.confidence)"
            :color="item.confidence >= 0.75 ? 'success' : 'info'"
            height="4"
            rounded
          />
          <em>{{ percent(item.confidence) }}</em>
        </div>
      </div>
    </template>

    <template #item.importance="{ item }">
      <div class="meter">
        <v-progress-linear
          :model-value="percent(item.importance)"
          :color="item.importance >= 0.75 ? 'warning' : 'primary'"
          height="5"
          rounded
        />
        <span>{{ percent(item.importance) }}%</span>
      </div>
    </template>

    <template #item.confidence="{ item }">
      <div class="meter">
        <v-progress-linear
          :model-value="percent(item.confidence)"
          :color="item.confidence >= 0.75 ? 'success' : 'info'"
          height="5"
          rounded
        />
        <span>{{ percent(item.confidence) }}%</span>
      </div>
    </template>

    <template #item.status="{ item }">
      <v-chip size="small" variant="tonal" :color="statusColors[item.status]">
        {{ t(`status.${item.status}`) }}
      </v-chip>
    </template>

    <template #item.evidence_count="{ item }">
      <v-icon v-if="item.evidence_count" size="16" color="info">mdi-file-document-check-outline</v-icon>
      <span class="count">{{ item.evidence_count ?? 0 }}</span>
    </template>

    <template #item.relation_count="{ item }">
      <v-icon v-if="item.relation_count" size="16" color="secondary">mdi-graph-outline</v-icon>
      <span class="count">{{ item.relation_count ?? 0 }}</span>
    </template>

    <template #item.updated_at="{ item }">
      <span class="muted-time">{{ formatDate(item.updated_at) }}</span>
    </template>

    <template #item.actions="{ item }">
      <div class="row-actions" @click.stop>
        <v-btn icon size="x-small" variant="text" :title="t('table.view')" @click="emit('view', item)">
          <v-icon size="17">mdi-eye-outline</v-icon>
        </v-btn>
        <v-btn icon size="x-small" variant="text" :title="t('table.locate')" @click="emit('locate', item)">
          <v-icon size="17">mdi-crosshairs-gps</v-icon>
        </v-btn>
        <v-menu location="bottom end" :offset="4">
          <template #activator="{ props: menuProps }">
            <v-btn icon size="x-small" variant="text" v-bind="menuProps">
              <v-icon size="17">mdi-dots-vertical</v-icon>
            </v-btn>
          </template>
          <v-list density="compact" class="memory-menu">
            <v-list-item v-if="item.status === 'archived'" @click="emit('restore', item)">
              <template #prepend><v-icon size="17">mdi-archive-arrow-up-outline</v-icon></template>
              <v-list-item-title>{{ t('table.restore') }}</v-list-item-title>
            </v-list-item>
            <v-list-item v-else @click="emit('archive', item)">
              <template #prepend><v-icon size="17">mdi-archive-outline</v-icon></template>
              <v-list-item-title>{{ t('table.archive') }}</v-list-item-title>
            </v-list-item>
            <v-list-item class="text-error" @click="emit('delete', item)">
              <template #prepend><v-icon size="17" color="error">mdi-delete-outline</v-icon></template>
              <v-list-item-title>{{ t('table.delete') }}</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-menu>
      </div>
    </template>

    <template #no-data>
      <div class="memory-table__empty">
        <v-icon size="36" color="primary" class="mb-2">mdi-database-search-outline</v-icon>
        <span>{{ t('table.empty') }}</span>
      </div>
    </template>

    <template #loading>
      <v-skeleton-loader type="table-row@6" />
    </template>
    </v-data-table-server>

    <footer class="memory-table__footer">
      <span class="memory-table__range">
        {{ t('table.showing', { start: rangeStart, end: rangeEnd, total }) }}
      </span>
      <v-spacer />
      <v-select
        :model-value="itemsPerPage"
        :items="[10, 20, 50, 100]"
        density="compact"
        variant="plain"
        hide-details
        class="memory-table__size"
        :aria-label="t('table.perPage')"
        @update:model-value="onPageSize"
      />
      <v-btn
        icon
        size="x-small"
        variant="text"
        :disabled="page <= 1"
        :aria-label="t('table.prevPage')"
        @click="onPage(page - 1)"
      >
        <v-icon size="18">mdi-chevron-left</v-icon>
      </v-btn>
      <span class="memory-table__page">{{ page }} / {{ pageCount }}</span>
      <v-btn
        icon
        size="x-small"
        variant="text"
        :disabled="page >= pageCount"
        :aria-label="t('table.nextPage')"
        @click="onPage(page + 1)"
      >
        <v-icon size="18">mdi-chevron-right</v-icon>
      </v-btn>
    </footer>
  </div>
</template>

<style scoped>
.memory-table {
  background: transparent;
  font-size: 12px;
  min-height: 0;
}

.memory-table :deep(table) {
  table-layout: fixed;
  width: 100%;
  min-width: 100%;
}

.memory-table :deep(.v-data-table__td),
.memory-table :deep(.v-data-table__th) {
  overflow: hidden;
}

.memory-table-wrap {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}

.memory-table-wrap :deep(.v-data-table) {
  flex: 1;
  min-height: 0;
}

.memory-table__footer {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 6px 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  background: rgba(var(--v-theme-on-surface), 0.012);
}

.memory-table__range {
  color: rgba(var(--v-theme-on-surface), 0.48);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.memory-table__page {
  min-width: 42px;
  color: rgba(var(--v-theme-on-surface), 0.58);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.memory-table__size {
  width: 76px;
  max-width: 76px;
  flex: none;
}

.memory-table__size :deep(.v-field) {
  font-size: 10.5px;
}

.memory-table :deep(.v-data-table__th) {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.memory-table :deep(.v-data-table__tr:hover) {
  background: rgba(var(--v-theme-primary), 0.045) !important;
  cursor: pointer;
}

.memory-table :deep(.v-data-table__td) {
  padding-top: 10px;
  padding-bottom: 10px;
}

.memory-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
  padding: 2px 0;
}

.memory-cell__title {
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface));
  font-size: 12.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-cell__preview {
  overflow: hidden;
  max-width: 100%;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 11px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-cell__meta {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 2px;
}

.memory-cell__tag {
  max-width: 150px;
  overflow: hidden;
  color: rgba(var(--v-theme-on-surface), 0.45);
  text-overflow: ellipsis;
}

.memory-cell__category {
  max-width: 70px;
  color: rgba(var(--v-theme-on-surface), 0.52);
}

.quality-cell {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 3px;
}

.quality-cell > div {
  display: grid;
  grid-template-columns: 9px 1fr 22px;
  align-items: center;
  gap: 5px;
}

.quality-cell span {
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-size: 8px;
  font-weight: 700;
}

.quality-cell em {
  color: rgba(var(--v-theme-on-surface), 0.52);
  font-size: 9px;
  font-style: normal;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.space-cell {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 11px;
  white-space: nowrap;
}

.meter {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 80px;
}

.meter span {
  width: 30px;
  color: rgba(var(--v-theme-on-surface), 0.54);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.count {
  margin-left: 2px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

.muted-time {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 10.5px;
  white-space: nowrap;
}

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0;
}

.memory-table__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 42px 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 12px;
}

.memory-menu {
  min-width: 160px;
}
</style>
