<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import { useMessages } from '../i18n'
import type {
  LocaleCode,
  MemoryDetails,
  MemoryEvidence,
  MemoryRelation,
  MemoryCategory,
  MemoryType,
} from '../types'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    memory: MemoryDetails | null
    loading?: boolean
    locale?: LocaleCode
  }>(),
  { loading: false, locale: 'zh-CN' },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'save', payload: Record<string, unknown>): void
  (event: 'archive', id: string): void
  (event: 'restore', id: string): void
  (event: 'remove', id: string): void
  (event: 'reinforce', id: string): void
  (event: 'locate', id: string): void
  (event: 'rollback', payload: { id: string; version: number }): void
  (event: 'delete-relation', id: string): void
  (event: 'add-evidence', payload: Record<string, unknown>): void
}>()

const { t } = useMessages(() => props.locale)
const tab = ref('overview')
const editing = ref(false)
const showEvidenceForm = ref(false)
const expandedVersion = ref<string | null>(null)

const form = reactive({
  title: '',
  content: '',
  category: 'other' as MemoryCategory,
  memory_type: 'long_term' as MemoryType,
  importance: 0.5,
  confidence: 0.6,
  tags: [] as string[],
})

const evidenceForm = reactive({
  quote: '',
  role: 'user',
  source_kind: 'user_explicit',
  message_id: '',
})

const typeItems = computed(() =>
  ['working', 'short_term', 'long_term', 'episodic', 'semantic', 'associative'].map((value) => ({
    value,
    title: t(`type.${value}`),
  })),
)
const categoryItems = computed(() =>
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
const sourceItems = computed(() =>
  ['user_explicit', 'ai_inference', 'behavior_observation', 'model_summary'].map((value) => ({
    value,
    title: t(`sourceKind.${value}`),
  })),
)

watch(
  () => props.memory?.id,
  () => {
    editing.value = false
    showEvidenceForm.value = false
    tab.value = 'overview'
    expandedVersion.value = null
    if (props.memory) {
      form.title = props.memory.title
      form.content = props.memory.content
      form.category = props.memory.category
      form.memory_type = props.memory.memory_type
      form.importance = props.memory.importance
      form.confidence = props.memory.confidence
      form.tags = [...props.memory.tags]
    }
  },
  { immediate: true },
)

const sourceColors: Record<string, string> = {
  user_explicit: 'success',
  ai_inference: 'warning',
  behavior_observation: 'info',
  model_summary: 'secondary',
}

const statusColors: Record<string, string> = {
  active: 'success',
  archived: 'secondary',
  superseded: 'warning',
  disputed: 'error',
  deleted: 'error',
}

function percent(value: number): number {
  return Math.round(Math.max(0, Math.min(1, value)) * 100)
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value.endsWith('Z') ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(props.locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function startEdit(): void {
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  if (props.memory) {
    form.title = props.memory.title
    form.content = props.memory.content
    form.category = props.memory.category
    form.memory_type = props.memory.memory_type
    form.importance = props.memory.importance
    form.confidence = props.memory.confidence
    form.tags = [...props.memory.tags]
  }
}

function save(): void {
  if (!props.memory) return
  emit('save', {
    title: form.title,
    content: form.content,
    category: form.category,
    memory_type: form.memory_type,
    importance: form.importance,
    confidence: form.confidence,
    tags: form.tags,
  })
  editing.value = false
}

function submitEvidence(): void {
  if (!props.memory || !evidenceForm.quote.trim()) return
  emit('add-evidence', {
    memory_id: props.memory.id,
    quote: evidenceForm.quote.trim(),
    role: evidenceForm.role,
    source_kind: evidenceForm.source_kind,
    message_id: evidenceForm.message_id.trim() || undefined,
    reinforce: true,
  })
  evidenceForm.quote = ''
  evidenceForm.message_id = ''
  showEvidenceForm.value = false
}

function handleDrawer(value: boolean): void {
  emit('update:modelValue', value)
}

function evidenceLabel(item: MemoryEvidence): string {
  return t(`sourceKind.${item.source_kind}`)
}

function relationLabel(item: MemoryRelation): string {
  return t(`relation.${item.relation_type}`)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="modelValue"
    location="right"
    temporary
    width="580"
    class="memory-drawer"
    @update:model-value="handleDrawer"
  >
    <div class="memory-drawer__inner">
      <header class="memory-drawer__header">
        <div>
          <p class="memory-drawer__eyebrow">{{ t('details.eyebrow') }}</p>
          <h2>{{ t('details.title') }}</h2>
        </div>
        <div class="memory-drawer__header-actions">
          <v-btn
            v-if="memory && !editing"
            icon
            variant="text"
            size="small"
            :title="t('details.edit')"
            @click="startEdit"
          >
            <v-icon size="19">mdi-pencil-outline</v-icon>
          </v-btn>
          <v-btn icon variant="text" size="small" @click="handleDrawer(false)">
            <v-icon size="20">mdi-close</v-icon>
          </v-btn>
        </div>
      </header>

      <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

      <div v-if="!memory && !loading" class="memory-drawer__empty">
        <v-icon size="40" color="primary">mdi-memory</v-icon>
        <p>{{ t('details.noEvidence') }}</p>
      </div>

      <template v-if="memory">
        <div class="memory-drawer__summary">
          <div class="memory-drawer__chips">
            <v-chip size="small" variant="tonal" :color="sourceColors[memory.source_kind]">
              {{ t(`sourceKind.${memory.source_kind}`) }}
            </v-chip>
            <v-chip size="small" variant="tonal" :color="statusColors[memory.status]">
              {{ t(`status.${memory.status}`) }}
            </v-chip>
            <v-chip size="small" variant="tonal" color="primary">
              {{ t(`type.${memory.memory_type}`) }}
            </v-chip>
            <v-chip size="small" variant="text" color="secondary">
              {{ t(`category.${memory.category}`) }}
            </v-chip>
          </div>
          <p class="memory-drawer__content">{{ memory.content }}</p>
          <div class="memory-drawer__meters">
            <div>
              <span>{{ t('details.importance') }}</span>
              <v-progress-linear
                :model-value="percent(memory.importance)"
                color="warning"
                height="5"
                rounded
              />
              <strong>{{ percent(memory.importance) }}%</strong>
            </div>
            <div>
              <span>{{ t('details.confidence') }}</span>
              <v-progress-linear
                :model-value="percent(memory.confidence)"
                color="success"
                height="5"
                rounded
              />
              <strong>{{ percent(memory.confidence) }}%</strong>
            </div>
            <div>
              <span>{{ t('details.retention') }}</span>
              <v-progress-linear
                :model-value="percent(memory.retention)"
                color="info"
                height="5"
                rounded
              />
              <strong>{{ percent(memory.retention) }}%</strong>
            </div>
          </div>
        </div>

        <v-tabs v-model="tab" density="compact" color="primary" class="memory-drawer__tabs">
          <v-tab value="overview">{{ t('details.overview') }}</v-tab>
          <v-tab value="evidence">
            {{ t('details.evidence') }}
            <v-badge :content="memory.evidence.length" inline color="primary" class="ml-1" />
          </v-tab>
          <v-tab value="versions">{{ t('details.versions') }}</v-tab>
          <v-tab value="relations">
            {{ t('details.relations') }}
            <v-badge :content="memory.relations.length" inline color="secondary" class="ml-1" />
          </v-tab>
        </v-tabs>

        <div class="memory-drawer__body">
          <div v-if="editing" class="edit-form">
            <v-text-field v-model="form.title" :label="t('details.titleField')" density="compact" />
            <v-textarea
              v-model="form.content"
              :label="t('details.content')"
              rows="5"
              auto-grow
              density="compact"
            />
            <div class="edit-form__grid">
              <v-select
                v-model="form.memory_type"
                :items="typeItems"
                item-title="title"
                item-value="value"
                :label="t('details.type')"
                density="compact"
              />
              <v-select
                v-model="form.category"
                :items="categoryItems"
                item-title="title"
                item-value="value"
                :label="t('details.category')"
                density="compact"
              />
            </div>
            <div class="edit-form__slider">
              <span>{{ t('details.importance') }} · {{ percent(form.importance) }}%</span>
              <v-slider v-model="form.importance" :min="0" :max="1" :step="0.01" color="warning" hide-details />
            </div>
            <div class="edit-form__slider">
              <span>{{ t('details.confidence') }} · {{ percent(form.confidence) }}%</span>
              <v-slider v-model="form.confidence" :min="0" :max="1" :step="0.01" color="success" hide-details />
            </div>
            <v-combobox
              v-model="form.tags"
              :label="t('details.tags')"
              multiple
              chips
              closable-chips
              density="compact"
            />
            <div class="edit-form__actions">
              <v-btn variant="text" @click="cancelEdit">{{ t('common.cancel') }}</v-btn>
              <v-btn color="primary" variant="flat" @click="save">{{ t('details.save') }}</v-btn>
            </div>
          </div>

          <template v-else>
            <div v-if="tab === 'overview'" class="overview">
              <div class="detail-grid">
                <div>
                  <span>{{ t('details.space') }}</span>
                  <strong>
                    {{ t(`spaceKind.${memory.space?.kind ?? 'user'}`) }}
                    <small v-if="memory.space?.name"> · {{ memory.space.name }}</small>
                  </strong>
                </div>
                <div>
                  <span>{{ t('details.version') }}</span>
                  <strong>v{{ memory.version }}</strong>
                </div>
                <div>
                  <span>{{ t('details.accessCount') }}</span>
                  <strong>{{ memory.access_count }}</strong>
                </div>
                <div>
                  <span>{{ t('details.reinforcement') }}</span>
                  <strong>{{ memory.reinforcement_count }}</strong>
                </div>
                <div>
                  <span>{{ t('details.contradiction') }}</span>
                  <strong>{{ memory.contradiction_count }}</strong>
                </div>
                <div>
                  <span>{{ t('details.occurredAt') }}</span>
                  <strong>{{ formatDate(memory.occurred_at) }}</strong>
                </div>
                <div>
                  <span>{{ t('details.createdAt') }}</span>
                  <strong>{{ formatDate(memory.created_at) }}</strong>
                </div>
                <div>
                  <span>{{ t('details.updatedAt') }}</span>
                  <strong>{{ formatDate(memory.updated_at) }}</strong>
                </div>
              </div>

              <div class="detail-section">
                <h4>{{ t('details.source') }}</h4>
                <v-alert
                  :type="memory.source_kind === 'user_explicit' ? 'success' : 'warning'"
                  variant="tonal"
                  density="compact"
                  class="mb-3"
                >
                  {{ t(`sourceKind.${memory.source_kind}`) }}
                  <template v-if="memory.source_kind !== 'user_explicit'">
                    · {{ t('details.sourceKind') }}: {{ t('sourceKind.ai_inference') }}
                  </template>
                </v-alert>
                <div v-if="memory.tags.length" class="tag-list">
                  <v-chip v-for="tag in memory.tags" :key="tag" size="small" variant="tonal" color="primary">
                    #{{ tag }}
                  </v-chip>
                </div>
              </div>

              <div v-if="memory.entities.length" class="detail-section">
                <h4>{{ t('details.entities') }}</h4>
                <div class="tag-list">
                  <v-chip
                    v-for="entity in memory.entities"
                    :key="entity.id"
                    size="small"
                    variant="text"
                    color="info"
                  >
                    {{ entity.name }} · {{ entity.entity_type }}
                  </v-chip>
                </div>
              </div>
            </div>

            <div v-else-if="tab === 'evidence'" class="evidence-list">
              <div class="section-head">
                <h4>{{ t('evidence.title') }}</h4>
                <v-btn
                  size="small"
                  variant="tonal"
                  color="primary"
                  prepend-icon="mdi-plus"
                  @click="showEvidenceForm = !showEvidenceForm"
                >
                  {{ t('evidence.add') }}
                </v-btn>
              </div>
              <v-card v-if="showEvidenceForm" variant="outlined" class="mb-3 pa-3">
                <v-select
                  v-model="evidenceForm.source_kind"
                  :items="sourceItems"
                  item-title="title"
                  item-value="value"
                  :label="t('evidence.source')"
                  density="compact"
                />
                <v-text-field v-model="evidenceForm.role" :label="t('evidence.role')" density="compact" />
                <v-text-field
                  v-model="evidenceForm.message_id"
                  :label="t('details.evidenceMessageId')"
                  density="compact"
                />
                <v-textarea
                  v-model="evidenceForm.quote"
                  :label="t('evidence.quote')"
                  rows="3"
                  density="compact"
                />
                <v-btn color="primary" size="small" variant="flat" @click="submitEvidence">
                  {{ t('common.save') }}
                </v-btn>
              </v-card>

              <div v-if="!memory.evidence.length" class="empty-block">{{ t('details.noEvidence') }}</div>
              <v-card
                v-for="item in memory.evidence"
                :key="item.id"
                variant="tonal"
                class="evidence-card"
              >
                <div class="evidence-card__head">
                  <v-chip size="x-small" variant="tonal" :color="sourceColors[item.source_kind]">
                    {{ evidenceLabel(item) }}
                  </v-chip>
                  <span v-if="item.role">{{ item.role }}</span>
                  <span>{{ formatDate(item.observed_at || item.created_at) }}</span>
                </div>
                <p class="evidence-card__quote">{{ item.quote }}</p>
                <div class="evidence-card__meta">
                  <span v-if="item.conversation_id">{{ t('details.conversation') }}: {{ item.conversation_id }}</span>
                  <span v-if="item.message_id">{{ t('details.message') }}: {{ item.message_id }}</span>
                  <span v-if="item.attributes.synthetic" class="synthetic">
                    {{ t('evidence.synthetic') }}
                  </span>
                </div>
              </v-card>
            </div>

            <div v-else-if="tab === 'versions'" class="versions-list">
              <div v-if="!memory.versions.length" class="empty-block">{{ t('details.noVersions') }}</div>
              <div v-for="item in memory.versions" :key="item.id" class="version-row">
                <div class="version-row__main">
                  <div class="version-row__title">
                    <v-chip size="x-small" variant="tonal" color="primary">v{{ item.version }}</v-chip>
                    <strong>{{ item.change_type }}</strong>
                    <span>{{ item.changed_by }}</span>
                  </div>
                  <span class="version-row__time">{{ formatDate(item.created_at) }}</span>
                </div>
                <div class="version-row__actions">
                  <v-btn size="x-small" variant="text" @click="expandedVersion = expandedVersion === item.id ? null : item.id">
                    {{ t('details.viewSnapshot') }}
                  </v-btn>
                  <v-btn
                    size="x-small"
                    variant="text"
                    color="warning"
                    @click="emit('rollback', { id: memory.id, version: item.version })"
                  >
                    {{ t('details.rollback') }}
                  </v-btn>
                </div>
                <pre v-if="expandedVersion === item.id" class="version-snapshot">{{ JSON.stringify(item.snapshot, null, 2) }}</pre>
              </div>
            </div>

            <div v-else class="relations-list">
              <div v-if="!memory.relations.length" class="empty-block">{{ t('details.noRelations') }}</div>
              <v-card
                v-for="item in memory.relations"
                :key="item.id"
                variant="outlined"
                class="relation-card"
              >
                <div class="relation-card__head">
                  <v-chip size="x-small" variant="tonal" color="secondary">
                    {{ relationLabel(item) }}
                  </v-chip>
                  <span>{{ percent(item.strength) }}%</span>
                  <v-btn
                    icon
                    size="x-small"
                    variant="text"
                    color="error"
                    class="ml-auto"
                    :title="t('details.deleteRelation')"
                    @click="emit('delete-relation', item.id)"
                  >
                    <v-icon size="16">mdi-link-off</v-icon>
                  </v-btn>
                </div>
                <p v-if="item.other" class="relation-card__content">{{ item.other.content }}</p>
                <small v-if="item.other">{{ t(`type.${item.other.memory_type}`) }} · {{ item.other.id.slice(0, 8) }}</small>
              </v-card>
            </div>
          </template>
        </div>

        <footer class="memory-drawer__footer">
          <v-btn size="small" variant="text" prepend-icon="mdi-crosshairs-gps" @click="emit('locate', memory.id)">
            {{ t('details.locate') }}
          </v-btn>
          <v-btn size="small" variant="text" prepend-icon="mdi-arm-flex" @click="emit('reinforce', memory.id)">
            {{ t('details.reinforce') }}
          </v-btn>
          <v-spacer />
          <v-btn
            v-if="memory.status === 'archived'"
            size="small"
            variant="tonal"
            color="success"
            @click="emit('restore', memory.id)"
          >
            {{ t('details.restore') }}
          </v-btn>
          <v-btn
            v-else
            size="small"
            variant="tonal"
            color="secondary"
            @click="emit('archive', memory.id)"
          >
            {{ t('details.archive') }}
          </v-btn>
          <v-btn size="small" variant="tonal" color="error" @click="emit('remove', memory.id)">
            {{ t('details.remove') }}
          </v-btn>
        </footer>
      </template>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.memory-drawer {
  border-left: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgb(var(--v-theme-surface)) !important;
}

.memory-drawer__inner {
  display: flex;
  height: 100%;
  flex-direction: column;
  min-height: 0;
}

.memory-drawer__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 20px 22px 12px;
}

.memory-drawer__eyebrow {
  margin: 0 0 2px;
  color: rgb(var(--v-theme-primary));
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.18em;
}

.memory-drawer__header h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.memory-drawer__header-actions {
  display: flex;
  gap: 2px;
}

.memory-drawer__summary {
  padding: 4px 22px 12px;
}

.memory-drawer__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 10px;
}

.memory-drawer__content {
  margin: 0 0 16px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.memory-drawer__meters {
  display: grid;
  gap: 9px;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  border-radius: 12px;
  background: rgba(var(--v-theme-on-surface), 0.018);
}

.memory-drawer__meters > div {
  display: grid;
  grid-template-columns: 74px 1fr 38px;
  align-items: center;
  gap: 9px;
}

.memory-drawer__meters span {
  color: rgba(var(--v-theme-on-surface), 0.54);
  font-size: 10.5px;
}

.memory-drawer__meters strong {
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.memory-drawer__tabs {
  padding: 0 16px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.memory-drawer__body {
  flex: 1;
  min-height: 0;
  padding: 16px 22px 24px;
  overflow-y: auto;
}

.memory-drawer__footer {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 11px 16px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgba(var(--v-theme-on-surface), 0.015);
}

.memory-drawer__empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 12px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 13px 18px;
}

.detail-grid > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.detail-grid span {
  color: rgba(var(--v-theme-on-surface), 0.48);
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.detail-grid strong {
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-grid small {
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-weight: 400;
}

.detail-section {
  margin-top: 22px;
}

.detail-section h4,
.section-head h4 {
  margin: 0 0 9px;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.section-head h4 {
  margin: 0;
}

.evidence-card,
.relation-card {
  margin-bottom: 9px;
  padding: 11px 13px;
}

.evidence-card__head,
.relation-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 10px;
}

.evidence-card__quote {
  margin: 0 0 7px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.evidence-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  color: rgba(var(--v-theme-on-surface), 0.46);
  font-size: 9.5px;
}

.synthetic {
  color: rgb(var(--v-theme-warning));
}

.relation-card__content {
  margin: 0 0 4px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 12px;
  line-height: 1.5;
}

.relation-card small {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 9.5px;
}

.version-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 10px 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
}

.version-row__main {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.version-row__title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.version-row__title strong {
  font-size: 11.5px;
  text-transform: capitalize;
}

.version-row__title span {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 10px;
}

.version-row__time {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 10px;
}

.version-row__actions {
  display: flex;
  gap: 2px;
}

.version-snapshot {
  width: 100%;
  max-height: 240px;
  margin: 8px 0 0;
  padding: 10px;
  overflow: auto;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.035);
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-family: Consolas, monospace;
  font-size: 10px;
  line-height: 1.45;
  white-space: pre-wrap;
}

.empty-block {
  padding: 30px 0;
  color: rgba(var(--v-theme-on-surface), 0.44);
  font-size: 12px;
  text-align: center;
}

.edit-form {
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.edit-form__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 11px;
}

.edit-form__slider span {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 10.5px;
}

.edit-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}

@media (max-width: 600px) {
  .memory-drawer {
    width: 100% !important;
  }
}
</style>
