# MoriBot 嵌入模式

MoriMemory 的正式运行方式不是独立后台，而是由 MoriBot 自动启停的嵌入式服务。完整可复制的代码与补丁点见：

**[integrations/moribot/README.md](../integrations/moribot/README.md)**

## 关键约束

- 独立仓库、独立版本、独立数据库。
- MoriBot 启动时检查 `/api/v1/health`，未运行则拉起子进程；退出时关闭自己拥有的进程。
- 进程锁 + PID 检测防止重复实例。
- 数据目录位于 MoriBot `data/memory`，自动进入 MoriBot 备份。
- 前端 `@moriai/memory-ui` 直接挂载在 MoriBot 的 Vue Router 中，不使用 iframe。
- 复用 MoriBot 的 Vue 3、Vuetify 3、主题、图标和鉴权。
- 浏览器只访问 MoriBot 的 `/api/v1/memory/*` 代理；代理校验 JWT 后注入服务令牌和用户身份。
- 服务只监听回环地址，业务接口需要 Bearer token，浏览器来源受 CORS 限制。

## 推荐组件关系

```text
MoriBot Vue
  └─ MemoryExplorer
       └─ MemoryApiClient(baseUrl="/api/v1/memory")
            └─ MoriBot FastAPI proxy
                 └─ MemoryServiceManager.request()
                      └─ MoriMemory FastAPI
                           └─ SQLite / FTS5 / vectors
```

## 接入步骤摘要

1. 把 `integrations/moribot/` 下的 `service_manager.py`、`proxy.py` 复制进 MoriBot。
2. 在 `DEFAULT_CONFIG` 增加 `memory` 配置节。
3. 在 `create_app()` 创建 `MemoryServiceManager`，在 lifespan 中 `ensure_started()` / `stop()`。
4. 把 `create_memory_proxy_router()` 注册到 `/api/v1/memory`。
5. 在 dashboard `package.json` 添加 `@moriai/memory-ui`。
6. 新增 `MemoryPage.vue`、路由、导航和 i18n 文案。
7. 在生成回复前调用 `/context`，回复后异步调用 `/conversations/ingest`。

## 验收清单

- 冷启动 MoriBot 后，`/api/v1/memory/health` 返回 200。
- MoriBot 重复启动不会出现两个 Memory Service。
- MoriBot 退出后子进程结束；异常退出后自动重启。
- 页面刷新、主题切换、深色/浅色模式正常。
- 图谱与表格双向联动，详情使用侧边抽屉。
- 角色 A 的私有记忆不会出现在角色 B 的召回结果中。
- 断开 Memory Service 时，MoriBot 其他页面仍可用，记忆页给出明确错误。
- MoriBot 备份恢复后记忆数据完整。
