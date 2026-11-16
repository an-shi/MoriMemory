import { inject, provide, defineComponent, type PropType } from 'vue'

import MemoryExplorer from './components/MemoryExplorer.vue'
import {
  memoryUiKey,
  resolveContext,
  type MemoryUiContext,
  type MemoryUiOptions,
} from './plugin'

export default defineComponent({
  name: 'MemoryUiProvider',
  props: {
    options: {
      type: Object as PropType<MemoryUiOptions>,
      default: () => ({}),
    },
  },
  setup(props, { slots }) {
    const existing = inject<MemoryUiContext | null>(memoryUiKey, null)
    if (!existing) {
      provide(memoryUiKey, resolveContext(props.options))
    }
    return () => slots.default?.()
  },
})

export { MemoryExplorer, memoryUiKey }
export type { MemoryUiContext, MemoryUiOptions }
