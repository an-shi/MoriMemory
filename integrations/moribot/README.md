# MoriBot 嵌入集成指南

MoriMemory 独立开发、独立版本管理，但正式运行时由 MoriBot 自动启动和托管。浏览器只访问 MoriBot 的 `/api/v1/memory/*` 代理，不直接访问 Python 服务，也不直接读取 SQLite。

```text
MoriBot Vue (MemoryExplorer)
  └─ JWT /api/v1/memory/*
       └─ MoriBot FastAPI proxy（校验 JWT，注入服务令牌和用户身份）
            └─ MoriMemory FastAPI（127.0.0.1:6190 + Bearer token）
                 └─ SQLite + FTS5 + 向量索引
```

## 1. 后端：复制两个模块

将以下文件复制到 MoriBot：

```text
MoriMemory/integrations/moribot/service_manager.py -> moribot/core/memory/service_manager.py
MoriMemory/integrations/moribot/proxy.py            -> moribot/core/memory/proxy.py
MoriMemory/integrations/moribot/__init__.py         -> moribot/core/memory/__init__.py
```

它们只依赖 MoriBot 已有的 `fastapi`、`httpx`、`loguru`。

### 1.1 增加配置默认值

在 `moribot/core/config/default.py` 的 `DEFAULT_CONFIG` 中加入：

```python
"memory": {
    "enabled": True,
    "auto_start": True,
    "auto_restart": True,
    # 留空时依次查找：MORIBOT_MEMORY_HOME、MoriBot 同级目录 ../MoriMemory
    "home": "",
    "host": "127.0.0.1",
    "port": 6190,
    "service_token": "",
    "startup_timeout_seconds": 30,
    "request_timeout_seconds": 120,
},
```

如果 `service_token` 留空，管理器会在 `data/memory/service-token` 生成高熵令牌并以环境变量 `MORIMEMORY_TOKEN` 注入子进程，不写入日志。

### 1.2 应用工厂中装配

在 `moribot/dashboard/app.py` 的 `create_app()` 中：

```python
from moribot.core.memory import MemoryServiceManager, create_memory_proxy_router

memory_manager = MemoryServiceManager.from_host(
    project_root=resolved_paths.project_root,
    data_dir=resolved_paths.data_dir,
    config=resolved_config.get("memory", {}),
)
```

在 `lifespan()` 中启动和关闭：

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await lifecycle.initialize()
    await lifecycle.start()
    try:
        await memory_manager.ensure_started()
    except Exception:
        logger.exception("MoriMemory startup failed; dashboard will continue.")
    try:
        yield
    finally:
        await memory_manager.stop()
        await lifecycle.stop()
```

把代理路由注册到 MoriBot 已认证的 API 树中（在 `create_app()` 里、SPA 兜底路由之前）：

```python
from moribot.dashboard.deps import get_current_user
from moribot.core.memory import create_memory_proxy_router

app.include_router(
    create_memory_proxy_router(
        manager=memory_manager,
        get_current_user=get_current_user,
    ),
    prefix="/api/v1",
)
```

`create_memory_proxy_router` 默认前缀为 `/memory`，因此最终地址为
`/api/v1/memory/*`；代理会把请求转发到 Memory Service 的 `/api/v1/*`。

`app.state.memory_manager = memory_manager` 后可让系统信息接口返回：

```python
status = await memory_manager.status()
# running / owned / version / database_ok / fts5 / error
```

### 1.3 生命周期行为

- 启动前先请求 `GET /api/v1/health`；健康则复用，不重复拉起进程。
- `data/memory/memory-service.lock` 防止多个 MoriBot 进程启动多个服务实例。
- 服务异常退出后，`auto_restart=True` 时每 5 秒检测并重新拉起。
- MoriBot 正常退出时仅终止自己启动的进程，不误杀外部复用的服务。
- 服务日志位于 `data/memory/logs/morimemory.log`；启动失败时额外输出到
  `data/memory/memory-service.out.log`。
- 数据库、配置和备份都位于 MoriBot 的 `data/` 下，因此 MoriBot 现有 ZIP 备份会自动包含记忆数据。

### 1.4 回复前检索、回复后提取

生成回复前调用 `/context`：

```python
import json

response = await memory_manager.request(
    "POST",
    "context",
    headers={
        "X-Mori-User-Id": user.username,
        "X-Mori-Character-Id": str(character.id),
        "X-Mori-Conversation-Id": conversation_id,
    },
    content=json.dumps(
        {
            "query": user_message,
            "mode": "recall",
            "limit": 8,
            "token_budget": 1600,
            "record_access": True,
        }
    ).encode(),
)
context_text = response.json()["data"]["context"]
```

回复完成后异步提交提取（不阻塞回复）：

```python
await memory_manager.request(
    "POST",
    "conversations/ingest",
    headers={...same principal...},
    content=json.dumps(
        {
            "conversation_id": conversation_id,
            "character_id": str(character.id),
            "character_name": character.name,
            "async_mode": True,
            "messages": [
                {"id": msg.id, "role": "user", "content": msg.content},
                {"id": reply.id, "role": "assistant", "content": reply.content},
            ],
        }
    ).encode(),
)
```

`/context` 返回的文本已经带来源标签（用户明确表达 / AI 推断 / 行为观察 / 模型归纳），提示词应明确要求模型不要把推断当成用户事实。

## 2. 前端：安装组件包

组件包位于本仓库 `frontend/`，包名 `@moriai/memory-ui`。先构建：

```bash
cd MoriMemory/frontend
pnpm install
pnpm build
```

在 MoriBot `dashboard/package.json` 中添加本地依赖（发布到私有 registry 后改用版本号）：

```json
{
  "dependencies": {
    "@moriai/memory-ui": "file:../../MoriMemory/frontend"
  }
}
```

```bash
cd MoriBot/dashboard
pnpm install
```

组件包不会创建第二个 Vue App 或第二套 Vuetify：`vue`、`vuetify` 都是 peer dependency，组件直接使用宿主的 Vuetify 实例、主题和全局组件。

### 2.1 页面组件

`dashboard/src/views/MemoryPage.vue`：

```vue
<script setup lang="ts">
import { MemoryApiClient, MemoryExplorer } from '@moriai/memory-ui'
import '@moriai/memory-ui/style.css'

import { getLocale } from '@/i18n'
import { getToken } from '@/utils/token'

const client = new MemoryApiClient({
  baseUrl: '/api/v1/memory',
  token: () => getToken(),
  headers: () => ({ 'Accept-Language': getLocale() }),
})
</script>

<template>
  <MemoryExplorer :client="client" title="记忆探索器" />
</template>
```

如果 MoriBot 需要把当前用户或角色传给记忆引擎，可以在 `headers()` 中追加
`X-Mori-Character-Id`，代理会透传该请求头。

### 2.2 路由与导航

`dashboard/src/router/index.ts`：

```ts
{
  path: 'memory',
  name: 'memory',
  component: () => import('@/views/MemoryPage.vue'),
  meta: { titleKey: 'nav.memory', requiresAuth: true },
}
```

`DefaultLayout.vue` 的 `navItems` 增加：

```ts
{ labelKey: 'nav.memory', icon: 'mdi-memory', to: '/memory', available: true },
```

在 `zh-CN.ts` / `en-US.ts` 增加 `nav.memory`（记忆 / Memory）即可。页面在 MoriBot 的
`v-main` 内直接渲染，不跳转新标签页、不使用 iframe、不创建独立应用外壳。

### 2.3 主题

Memory Explorer 的 CSS 全部使用 Vuetify 的 CSS 变量（`--v-theme-*`），因此会直接继承 MoriBot 的 `moribot-dark` / `moribot-light` 主题；图表配色也读取 `useTheme().current`。切换主题时无需重新挂载页面。

## 3. 独立开发模式

```bash
# 终端 1
cd MoriMemory
uv run morimemory init
uv run morimemory token
uv run morimemory serve

# 终端 2
cd MoriMemory/frontend
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5174`，粘贴服务令牌即可。开发服务器把 `/api` 代理到
`127.0.0.1:6190`，页面结构与 MoriBot 内嵌入时一致。

## 4. 安全清单

- MoriMemory 默认只监听 `127.0.0.1`。
- 所有业务接口要求高熵 Bearer token；健康检查公开。
- 代理层必须保留 MoriBot 的 `get_current_user` 依赖，不能匿名转发。
- `security.enforce_origin` 默认只允许 `server.cors_origins` 中的浏览器来源。
- 生产环境不要将 6190 端口暴露到局域网；如必须暴露，请放在带 TLS 和认证的反向代理之后。
- 角色私有记忆由后端 `SpaceAccessService` 强制隔离；`recall` 模式下另一个角色无法读取该空间。
- 用户级空间不会隐式共享给角色；如需跨角色共享，必须通过 `/grants` 显式授权。
- 对话记忆只对同一角色开放；不同角色即使属于同一用户也无法互相读取。

## 5. 故障排查

| 现象 | 检查 |
| --- | --- |
| MoriBot 启动后 `/api/v1/memory/health` 503 | 查看 `data/memory/memory-service.out.log`；确认 `uv` 可用、`memory.home` 正确 |
| 页面 401 | 浏览器 JWT 是否有效；代理是否正确注入服务令牌 |
| 图谱或表格为空 | 确认用户 ID 与写入记忆时的 `X-Mori-User-Id` 一致；检查筛选条件 |
| 角色看不到用户记忆 | 这是严格隔离的预期行为；需要共享时调用 `/api/v1/grants` 授权 |
| 端口被占用 | 修改 `memory.port`，或先停止旧的 Memory Service |
