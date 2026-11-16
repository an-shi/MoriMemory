<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import { useMessages } from '../i18n'
import type { LocaleCode, MemoryCategory, MemorySpace, MemoryType } from '../types'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    spaces: MemorySpace[]
    locale?: LocaleCode
    saving?: boolean
  }>(),
  { locale: 'zh-CN', saving: false },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'save', payload: Record<string, unknown>): void
}>()

const { t } = useMessages(() => props.locale)

const form = reactive({
  content: '',
  title: '',
  memory_type: 'long_term' as MemoryType,
  category: 'fact' as MemoryCategory,
  source_kind: 'user_explicit',
  importance: 0.6,
  confidence: 0.88,
  tags: [] as string[],
  space_id: '',
  deduplicate: true,
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
    'other',
  ].map((value) => ({ value, title: t(`category.${value}`) })),
)
const sourceItems = computed(() =>
  ['user_explicit', 'ai_inference', 'behavior_observation', 'model_summary'].map((value) => ({
    value,
    title: t(`sourceKind.${value}`),
  })),
)
const spaceItems = computed(() => [
  { value: '', title: t('create.autoSpace') },
  ...props.spaces.map((space) => ({
    value: space.id,
    title: `${t(`spaceKind.${space.kind}`)} · ${space.name || space.key}`,
  })),
])

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    form.content = ''
    form.title = ''
    form.memory_type = 'long_term'
    form.category = 'fact'
    form.source_kind = 'user_explicit'
    form.importance = 0.6
    form.confidence = 0.88
    form.tags = []
    form.space_id = ''
    form.deduplicate = true
  },
)

function close(): void {
  emit('update:modelValue', false)
}

function submit(): void {
  if (!form.content.trim()) return
  emit('save', {
    content: form.content.trim(),
    title: form.title.trim(),
    memory_type: form.memory_type,
    category: form.category,
    source_kind: form.source_kind,
    importance: form.importance,
    confidence: form.confidence,
    tags: form.tags,
    space_id: form.space_id || undefined,
    deduplicate: form.deduplicate,
    scope: 'auto',
  })
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="640"
    persistent
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="create-dialog">
      <v-card-title class="create-dialog__title">
        <v-icon color="primary" class="mr-2">mdi-plus-circle-outline</v-icon>
        {{ t('create.title') }}
      </v-card-title>
      <v-card-text>
        <v-textarea
          v-model="form.content"
          :label="t('create.content')"
          rows="4"
          auto-grow
          autofocus
          counter="20000"
          maxlength="20000"
          class="mb-2"
        />
        <v-text-field v-model="form.title" :label="t('create.titleField')" class="mb-2" />
        <div class="create-grid">
          <v-select
            v-model="form.memory_type"
            :items="typeItems"
            item-title="title"
            item-value="value"
            :label="t('create.type')"
            density="compact"
          />
          <v-select
            v-model="form.category"
            :items="categoryItems"
            item-title="title"
            item-value="value"
            :label="t('create.category')"
            density="compact"
          />
          <v-select
            v-model="form.source_kind"
            :items="sourceItems"
            item-title="title"
            item-value="value"
            :label="t('create.source')"
            density="compact"
          />
          <v-select
            v-model="form.space_id"
            :items="spaceItems"
            item-title="title"
            item-value="value"
            :label="t('create.space')"
            density="compact"
          />
        </div>
        <div class="create-slider">
          <span>{{ t('create.importance') }} · {{ Math.round(form.importance * 100) }}%</span>
          <v-slider v-model="form.importance" :min="0" :max="1" :step="0.01" color="warning" hide-details />
        </div>
        <div class="create-slider">
          <span>{{ t('create.confidence') }} · {{ Math.round(form.confidence * 100) }}%</span>
          <v-slider v-model="form.confidence" :min="0" :max="1" :step="0.01" color="success" hide-details />
        </div>
        <v-combobox
          v-model="form.tags"
          :label="t('create.tags')"
          multiple
          chips
          closable-chips
          density="compact"
        />
        <v-checkbox v-model="form.deduplicate" :label="t('create.deduplicate')" density="compact" hide-details />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="close">{{ t('common.cancel') }}</v-btn>
        <v-btn
          color="primary"
          variant="flat"
          :loading="saving"
          :disabled="!form.content.trim()"
          @click="submit"
        >
          {{ saving ? t('create.creating') : t('create.submit') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.create-dialog {
  border-radius: 16px !important;
}

.create-dialog__title {
  display: flex;
  align-items: center;
  font-size: 16px;
  font-weight: 650;
}

.create-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 4px;
}

.create-slider {
  margin-top: 2px;
}

.create-slider span {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 10.5px;
}

@media (max-width: 600px) {
  .create-grid {
    grid-template-columns: 1fr;
  }
}
</style>
