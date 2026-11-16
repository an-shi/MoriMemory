<script setup lang="ts">
import cytoscape, {
  type Core,
  type EdgeSingular,
  type ElementDefinition,
  type LayoutOptions,
  type StylesheetJson,
} from 'cytoscape'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useMessages } from '../i18n'
import type {
  GraphEdge,
  GraphNode,
  LocaleCode,
  MemoryGraphColors,
  MemoryType,
  SourceKind,
} from '../types'

const props = withDefaults(
  defineProps<{
    nodes: GraphNode[]
    edges: GraphEdge[]
    selectedId?: string | null
    pathNodeIds?: string[]
    pathEdgeIds?: string[]
    locale?: LocaleCode
    colors: MemoryGraphColors
    layout?: 'cose' | 'concentric' | 'grid' | 'breadthfirst' | 'circle'
    clusterBy?: 'none' | 'space' | 'type'
    loading?: boolean
    fullscreen?: boolean
    compact?: boolean
  }>(),
  {
    selectedId: null,
    pathNodeIds: () => [],
    pathEdgeIds: () => [],
    locale: 'zh-CN',
    layout: 'cose',
    clusterBy: 'none',
    loading: false,
    fullscreen: false,
    compact: false,
  },
)

const emit = defineEmits<{
  (event: 'node-click', id: string): void
  (event: 'node-expand', id: string): void
  (event: 'background-click'): void
  (event: 'rendered', count: number): void
}>()

const { t } = useMessages(() => props.locale)
const container = ref<HTMLDivElement | null>(null)
let cy: Core | null = null
let resizeObserver: ResizeObserver | null = null
let lastWidth = 0
let lastHeight = 0

const typeColors: Record<MemoryType, string> = {
  working: '#90a4ae',
  short_term: '#4dd0e1',
  long_term: '#6fb8ff',
  episodic: '#e6c27a',
  semantic: '#7be3b3',
  associative: '#b388ff',
}

const sourceBorder: Record<SourceKind, string> = {
  user_explicit: '#7be3b3',
  ai_inference: '#e6c27a',
  behavior_observation: '#6fb8ff',
  model_summary: '#57b997',
}

function paletteColor(type: MemoryType): string {
  const fallback = typeColors[type] ?? props.colors.primary
  const map: Partial<Record<MemoryType, string>> = {
    long_term: props.colors.info,
    episodic: props.colors.warning,
    semantic: props.colors.primary,
    short_term: props.colors.secondary,
    associative: props.colors.accent,
  }
  return map[type] ?? fallback
}

function clusterKey(node: GraphNode): string {
  if (props.clusterBy === 'space') {
    return `${node.space_kind ?? 'space'}:${node.space_id}`
  }
  return node.memory_type
}

function clusterLabel(key: string): string {
  if (props.clusterBy === 'space') {
    const node = props.nodes.find((item) => `${item.space_kind ?? 'space'}:${item.space_id}` === key)
    if (node) {
      const kind = t(`spaceKind.${node.space_kind ?? 'user'}`)
      return `${kind} · ${node.space_name ?? node.space_id.slice(0, 6)}`
    }
  }
  return t(`type.${key}`)
}

function buildElements(): ElementDefinition[] {
  const elements: ElementDefinition[] = []
  if (props.clusterBy !== 'none') {
    const keys = new Set(props.nodes.map((node) => clusterKey(node)))
    for (const key of keys) {
      elements.push({
        data: {
          id: `cluster:${key}`,
          label: clusterLabel(key),
          isCluster: true,
          color: props.colors.muted,
        },
      })
    }
  }
  for (const node of props.nodes) {
    const classes = ['memory-node']
    if (!props.compact && (node.pinned || node.importance >= 0.85)) classes.push('label-node')
    elements.push({
      classes: classes.join(' '),
      data: {
        id: node.id,
        label: node.title || node.content,
        displayLabel:
          node.title || (node.content.length > 42 ? `${node.content.slice(0, 42)}…` : node.content),
        color: paletteColor(node.memory_type),
        borderColor: sourceBorder[node.source_kind] ?? props.colors.line,
        importance: node.importance,
        confidence: node.confidence,
        retention: node.retention,
        memoryType: node.memory_type,
        spaceKind: node.space_kind,
        spaceId: node.space_id,
        status: node.status,
        pinned: node.pinned,
        parent: props.clusterBy === 'none' ? undefined : `cluster:${clusterKey(node)}`,
      },
    })
  }
  for (const edge of props.edges) {
    const dashed = ['contradicts', 'derived_from', 'merged_into', 'consolidated_from'].includes(
      edge.relation_type,
    )
    const color =
      edge.relation_type === 'contradicts'
        ? props.colors.error
        : edge.relation_type === 'supports'
          ? props.colors.success
          : edge.relation_type === 'consolidated_from' || edge.relation_type === 'derived_from'
            ? props.colors.secondary
            : props.colors.muted
    elements.push({
      classes: 'memory-edge',
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        relationType: edge.relation_type,
        strength: edge.strength,
        confidence: edge.confidence,
        color,
        dashed,
        directed: edge.directed,
      },
    })
  }
  return elements
}

function stylesheet(): StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        'background-color': 'data(color)',
        'border-color': 'data(borderColor)',
        'border-width': 2,
        width: 'mapData(importance, 0, 1, 30, 62)',
        height: 'mapData(importance, 0, 1, 30, 62)',
        label: 'data(displayLabel)',
        color: props.colors.text,
        'font-size': 12,
        'font-weight': 500,
        'min-zoomed-font-size': 12,
        'text-wrap': 'wrap',
        'text-max-width': '112px',
        'text-valign': 'bottom',
        'text-margin-y': 5,
        'text-outline-color': props.colors.background,
        'text-outline-width': 2,
        'overlay-opacity': 0,
        'transition-property': 'border-width, opacity',
        'transition-duration': 140,
      },
    },
    {
      selector: 'node[isCluster]',
      style: {
        shape: 'round-rectangle',
        'background-color': props.colors.surface,
        'background-opacity': 0.25,
        'border-color': props.colors.line,
        'border-width': 1,
        'border-style': 'dashed',
        label: 'data(label)',
        'font-size': 10,
        'font-weight': 600,
        color: props.colors.muted,
        'text-valign': 'top',
        'text-margin-y': -6,
        padding: '18px',
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-color': props.colors.primary,
        'border-width': 4,
        'background-color': 'data(color)',
        'font-size': 14,
        'min-zoomed-font-size': 0,
        'text-opacity': 1,
        'text-background-color': props.colors.surface,
        'text-background-opacity': 0.86,
        'text-background-padding': '3px',
        'text-background-shape': 'roundrectangle',
        'overlay-color': props.colors.primary,
        'overlay-opacity': 0.12,
        'overlay-padding': 6,
      },
    },
    {
      selector: 'node.memory-node.dimmed',
      style: {
        opacity: 0.16,
      },
    },
    {
      selector: 'node.memory-node.path-node',
      style: {
        'border-color': props.colors.accent,
        'border-width': 5,
        'font-size': 12,
        'min-zoomed-font-size': 0,
        'text-opacity': 1,
        'text-background-color': props.colors.surface,
        'text-background-opacity': 0.86,
        'text-background-padding': '3px',
        'text-background-shape': 'roundrectangle',
        'overlay-color': props.colors.accent,
        'overlay-opacity': 0.18,
        'overlay-padding': 8,
      },
    },
    {
      selector: 'node.memory-node.hovered, node.memory-node.label-node',
      style: {
        'font-size': 12,
        'min-zoomed-font-size': 0,
        'text-opacity': 1,
        'text-background-color': props.colors.surface,
        'text-background-opacity': 0.88,
        'text-background-padding': '3px',
        'text-background-shape': 'roundrectangle',
      },
    },
    {
      selector: 'edge',
      style: {
        width: 'mapData(strength, 0, 1, 1, 5)',
        'line-color': 'data(color)',
        'target-arrow-color': 'data(color)',
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.75,
        'curve-style': 'bezier',
        opacity: 0.58,
        'line-style': (element: EdgeSingular) => (element.data('dashed') ? 'dashed' : 'solid'),
      },
    },
    {
      selector: 'edge:selected',
      style: {
        opacity: 0.95,
        width: 4,
      },
    },
    {
      selector: 'edge.path-edge',
      style: {
        'line-color': props.colors.accent,
        'target-arrow-color': props.colors.accent,
        opacity: 1,
        width: 4,
      },
    },
    {
      selector: 'edge.dimmed',
      style: {
        opacity: 0.08,
      },
    },
  ]
}

function layoutOptions(): LayoutOptions {
  const padding = 44
  if (props.layout === 'concentric') {
    return {
      name: 'concentric',
      fit: true,
      padding,
      animate: false,
      concentric: (node) => (node.data('isCluster') ? 0 : Math.round(Number(node.data('importance')) * 10)),
      levelWidth: () => 3,
    }
  }
  if (props.layout === 'grid') {
    return { name: 'grid', fit: true, padding, animate: false, avoidOverlap: true }
  }
  if (props.layout === 'breadthfirst') {
    return {
      name: 'breadthfirst',
      fit: true,
      padding,
      animate: false,
      directed: true,
      spacingFactor: 1.25,
      grid: true,
    }
  }
  if (props.layout === 'circle') {
    return { name: 'circle', fit: true, padding, animate: false, spacingFactor: 1.1 }
  }
  return {
    name: 'cose',
    fit: true,
    padding,
    animate: false,
    randomize: true,
    nodeRepulsion: () => 14000,
    idealEdgeLength: () => 110,
    gravity: 0.22,
    numIter: 900,
    nodeOverlap: 8,
  }
}

function render(shouldFit = true): void {
  if (!cy) return
  const hadSelection = props.selectedId
  cy.elements().remove()
  cy.add(buildElements())
  applySelection(hadSelection)
  applyPath()
  if (props.nodes.length) {
    cy.resize()
    cy.layout(layoutOptions()).run()
    if (shouldFit) {
      setTimeout(() => {
        cy?.resize()
        fitGraph()
      }, 80)
    }
  }
  emit('rendered', props.nodes.length)
}

function applySelection(id?: string | null): void {
  if (!cy) return
  cy.nodes('.memory-node').unselect()
  if (!id) return
  const node = cy.getElementById(id)
  if (node.nonempty()) {
    node.select()
  }
}

function applyPath(): void {
  if (!cy) return
  cy.elements().removeClass('path-node path-edge dimmed')
  if (!props.pathNodeIds.length) return
  const pathNodes = new Set(props.pathNodeIds)
  const pathEdges = new Set(props.pathEdgeIds)
  cy.nodes('.memory-node').forEach((node) => {
    if (pathNodes.has(node.id())) node.addClass('path-node')
    else node.addClass('dimmed')
  })
  cy.edges().forEach((edge) => {
    if (pathEdges.has(edge.id())) edge.addClass('path-edge')
    else edge.addClass('dimmed')
  })
}

function focusNode(id: string, zoom = true): boolean {
  if (!cy) return false
  const node = cy.getElementById(id)
  if (node.empty()) return false
  applySelection(id)
  const targetZoom = zoom ? Math.max(cy.zoom(), 1.05) : cy.zoom()
  cy.animate(
    { center: { eles: node }, zoom: targetZoom },
    { duration: 280 },
  )
  return true
}

function fitGraph(padding = 42): void {
  if (!cy) return
  const nodes = cy.nodes('.memory-node')
  if (!nodes.length) return
  const box = nodes.boundingBox({ includeLabels: false, includeOverlays: false })
  const width = cy.container()?.clientWidth ?? cy.width()
  const height = cy.container()?.clientHeight ?? cy.height()
  if (!box.w || !box.h || !width || !height) {
    cy.fit(undefined, padding)
    return
  }
  const zoom = Math.max(
    0.18,
    Math.min(1.15, Math.min((width - padding * 2) / box.w, (height - padding * 2) / box.h)),
  )
  const centerX = (box.x1 + box.x2) / 2
  const centerY = (box.y1 + box.y2) / 2
  cy.zoom(zoom)
  cy.pan({ x: width / 2 - centerX * zoom, y: height / 2 - centerY * zoom })
}

function fit(): void {
  fitGraph()
}

function runLayout(): void {
  if (!cy || !props.nodes.length) return
  cy.resize()
  cy.layout(layoutOptions()).run()
  setTimeout(fitGraph, 80)
}

function onResize(): void {
  if (!cy || !container.value) return
  const width = container.value.clientWidth
  const height = container.value.clientHeight
  const wasHidden = lastWidth === 0 || lastHeight === 0
  lastWidth = width
  lastHeight = height
  cy.resize()
  if (wasHidden && width > 0 && height > 0 && props.nodes.length) {
    // The graph page is kept mounted with v-show. Re-run the layout when the
    // container first becomes visible so nodes are not collapsed at (0, 0).
    cy.layout(layoutOptions()).run()
    setTimeout(fitGraph, 80)
  }
}

onMounted(() => {
  if (!container.value) return
  cy = cytoscape({
    container: container.value,
    elements: [],
    style: stylesheet(),
    layout: { name: 'preset' },
    minZoom: 0.15,
    maxZoom: 4,
    wheelSensitivity: 0.18,
    boxSelectionEnabled: false,
    selectionType: 'single',
    motionBlur: false,
  })
  if (import.meta.env.DEV) {
    ;(window as unknown as { __moriCy?: Core }).__moriCy = cy
  }
  cy.on('tap', 'node.memory-node', (event) => {
    emit('node-click', event.target.id())
  })
  cy.on('tap', (event) => {
    if (event.target === cy) emit('background-click')
  })
  cy.on('dbltap', 'node.memory-node', (event) => {
    emit('node-expand', event.target.id())
  })
  cy.on('mouseover', 'node.memory-node', (event) => {
    const node = event.target
    node.addClass('hovered')
    node.style('border-width', 3)
    container.value?.style.setProperty('cursor', 'pointer')
  })
  cy.on('mouseout', 'node.memory-node', (event) => {
    event.target.removeClass('hovered')
    event.target.style('border-width', event.target.selected() ? 4 : 2)
    container.value?.style.removeProperty('cursor')
  })
  resizeObserver = new ResizeObserver(onResize)
  resizeObserver.observe(container.value)
  render()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  cy?.destroy()
  cy = null
})

watch(
  () => [props.nodes, props.edges, props.clusterBy, props.layout],
  () => render(),
  { deep: true },
)

watch(
  () => props.colors,
  () => {
    if (!cy) return
    cy.style(stylesheet())
  },
  { deep: true },
)

watch(
  () => props.selectedId,
  (id) => applySelection(id),
)

watch(
  () => [props.pathNodeIds, props.pathEdgeIds],
  () => applyPath(),
  { deep: true },
)

watch(
  () => props.fullscreen,
  async () => {
    await nextTick()
    cy?.resize()
    setTimeout(fitGraph, 60)
  },
)

defineExpose({ focusNode, fit, runLayout })
</script>

<template>
  <div class="memory-graph" :class="{ 'memory-graph--loading': loading }">
    <div ref="container" class="memory-graph__canvas" />
    <div v-if="!loading && !nodes.length" class="memory-graph__empty">
      <v-icon size="34" color="primary" class="mb-2">mdi-graph-outline</v-icon>
      <span>{{ t('graph.empty') }}</span>
    </div>
    <v-progress-linear v-if="loading" indeterminate color="primary" class="memory-graph__progress" />
    <div class="memory-graph__hint">{{ t('graph.expand') }} · {{ t('graph.zoomHint') }}</div>
  </div>
</template>

<style scoped>
.memory-graph {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 12%, color-mix(in srgb, var(--mori-mint, #7be3b3) 9%, transparent), transparent 46%),
    radial-gradient(circle at 82% 92%, color-mix(in srgb, var(--mori-mint, #7be3b3) 6%, transparent), transparent 48%),
    var(--memory-graph-bg, transparent);
}

.memory-graph__canvas {
  position: absolute;
  inset: 0;
}

.memory-graph__empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: rgba(var(--v-theme-on-surface), 0.56);
  font-size: 12.5px;
  pointer-events: none;
}

.memory-graph__progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
}

.memory-graph__hint {
  position: absolute;
  right: 12px;
  bottom: 10px;
  max-width: calc(100% - 24px);
  color: rgba(var(--v-theme-on-surface), 0.42);
  font-size: 10px;
  letter-spacing: 0.02em;
  pointer-events: none;
  text-align: right;
  white-space: normal;
}

.memory-graph--loading .memory-graph__canvas {
  opacity: 0.68;
}
</style>
