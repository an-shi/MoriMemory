# @moriai/memory-ui

MoriMemory 的 Vue 3 + Vuetify 3 组件包。提供统一的 Memory Explorer，将 Cytoscape 关联图谱与 VDataTableServer 数据表格分成独立页面切换，详情使用侧边抽屉；支持深色/浅色主题、服务端筛选分页、导入导出和图谱/表格双向定位。

## 安装

```bash
pnpm add @moriai/memory-ui
# 同时确保宿主已有 vue@3.5+ 与 vuetify@3.13+
```

本地开发可使用 `file:` 依赖：

```json
{ "dependencies": { "@moriai/memory-ui": "file:../../MoriMemory/frontend" } }
```

## 使用

```ts
import { createMemoryUi } from '@moriai/memory-ui'
import '@moriai/memory-ui/style.css'

app.use(
  createMemoryUi({
    api: {
      baseUrl: '/api/v1/memory',
      token: () => getToken(),
      headers: () => ({ 'Accept-Language': getLocale() }),
    },
  }),
)
```

然后直接使用：

```vue
<MemoryExplorer title="记忆探索器" />
```

或显式传入 client（适合页面级依赖注入）：

```vue
<script setup lang="ts">
import { MemoryApiClient, MemoryExplorer } from '@moriai/memory-ui'
import '@moriai/memory-ui/style.css'

const client = new MemoryApiClient({
  baseUrl: '/api/v1/memory',
  token: () => getToken(),
})
</script>

<template>
  <MemoryExplorer :client="client" locale="zh-CN" :page-size="20" />
</template>
```

## 组件与导出

| 导出 | 说明 |
| --- | --- |
| `MemoryExplorer` | 完整页面（内容页 + 图谱页 + 抽屉 + 导入导出） |
| `MemoryGraph` | Cytoscape 图谱 |
| `MemoryTable` | VDataTableServer 表格 |
| `MemoryDetailsDrawer` | 详情抽屉 |
| `MemoryCreateDialog` | 新建记忆对话框 |
| `MemoryUiProvider` | 通过 provide 注入 client 的包装组件 |
| `createMemoryUi` | Vue 插件，注入 client 并注册全局 `MemoryExplorer` |
| `MemoryApiClient` | 带 token、超时、错误信封解析的 REST client |
| `MemoryApiError` | 状态码、code、details |
| `useMessages` / `translate` | 内置中英文文案 |
| `types` | 全部 TypeScript 类型 |

## Props

`MemoryExplorer`：

| Prop | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `client` | `MemoryApi` | 注入值 | API client |
| `locale` | `'zh-CN' \| 'en-US'` | `'zh-CN'` | 界面语言 |
| `pageSize` | `number` | `20` | 表格默认每页 |
| `title` | `string` | `记忆探索器` | 页面标题 |
| `initialView` | `'table' \| 'graph'` | `'table'` | 初始显示内容页或图谱页 |

## 宿主要求

- Vue 3.5+、Vuetify 3.13+，组件使用宿主的全局 Vuetify 组件实例。
- 不创建第二个 Vue App，不加载第二套 Vuetify。
- 主题通过 Vuetify CSS 变量（`--v-theme-*`）继承，图谱读取 `useTheme().current`。
- 宿主应只通过自己的受保护代理提供 API，不要让浏览器直接访问 Memory Service。
- 必须引入 `@moriai/memory-ui/style.css`。

## 开发

```bash
pnpm install
pnpm dev        # 独立开发壳 http://127.0.0.1:5174
pnpm typecheck
pnpm build
```

`pnpm dev` 需要后端运行在 `127.0.0.1:6190`，首次打开粘贴 `morimemory token` 的输出。
