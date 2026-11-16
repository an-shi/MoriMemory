# API 参考

基础路径：`/api/v1`。交互式文档：`/api/docs`，OpenAPI：`/api/openapi.json`。

## 1. 认证与身份

除健康检查外，所有接口要求：

```http
Authorization: Bearer <MORIMEMORY_TOKEN>
```

MoriBot 代理还会注入：

```http
X-Mori-User-Id: admin
X-Mori-Character-Id: 1
X-Mori-Conversation-Id: conv-123
X-Mori-Role: admin        # admin | service
```

- 直接连接服务时，`X-Mori-User-Id` 缺省为 `security.default_user_id`。
- `X-Mori-Role=service` 时不能使用 `manage` 模式绕过角色隔离。
- 浏览器直接访问时如果带 `Origin`，必须命中 `server.cors_origins`（`security.enforce_origin=true`）。
- 管理类接口（创建/修改/删除/配置/备份/维护）要求 `admin` 角色。

响应信封：

```json
{
  "status": "ok",
  "message": "",
  "data": {}
}
```

错误：

```json
{
  "status": "error",
  "message": "memory not found",
  "data": { "code": "not_found" },
  "code": "not_found"
}
```

## 2. 健康检查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 公开；数据库、FTS5、embedding、向量后端、版本 |
| GET | `/health/live` | 进程存活 |
| GET | `/health/ready` | 就绪状态（数据库可用） |

```json
{
  "status": "ok",
  "service": "morimemory",
  "version": "0.1.0",
  "database": { "ok": true, "schema_version": 1, "fts5": true },
  "embedding": { "enabled": true, "provider": "hashing", "model": "local-hash-512" },
  "vector": { "backend": "sqlite" },
  "auth_required": true
}
```

## 3. 记忆空间

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/spaces?mode=manage&kind=user` | 当前 principal 可读空间 + 记忆数 |
| POST | `/spaces` | 手工创建空间 |
| PATCH | `/spaces/{id}` | 修改名称/描述/状态 |
| DELETE | `/spaces/{id}` | 删除空间及其中记忆（管理员） |

空间对象：

```json
{
  "id": "uuid",
  "kind": "user",
  "key": "user:local",
  "name": "用户 · local",
  "user_id": "local",
  "character_id": null,
  "conversation_id": null,
  "memory_count": 36,
  "writable": true
}
```

## 4. 记忆 CRUD

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/memories` | 服务端分页、筛选、排序、`focus_id` 定位 |
| POST | `/memories` | 手动创建（自动去重） |
| GET | `/memories/{id}` | 详情 + 证据 + 版本 + 关系 + 实体 |
| PATCH | `/memories/{id}` | 修改内容/分类/评分/标签/状态 |
| DELETE | `/memories/{id}` | 永久删除（级联清理索引） |
| POST | `/memories/{id}/archive` | 软归档 |
| POST | `/memories/{id}/restore` | 恢复 |
| POST | `/memories/{id}/reinforce` | 强化，可附带证据 |
| POST | `/memories/{id}/merge` | 合并（body: `source_id`） |
| POST | `/memories/{id}/rollback` | 回滚到版本（body: `version`） |
| GET | `/memories/{id}/versions` | 版本列表 |
| GET/POST | `/memories/{id}/evidence` | 证据列表 / 添加证据 |
| GET | `/memories/{id}/relations` | 关联记忆 |
| POST | `/memories/bulk` | 批量 archive/delete/restore/pin/unpin |

### 4.1 列表参数

`q`、`page`、`per_page`（≤200）、`sort_by`、`sort_order`、`mode`、`focus_id`、`space_id`、`space_ids`、`space_kind`、`memory_type`（可重复）、`category`、`status`、`source_kind`、`tags`、`min_importance`、`max_importance`、`min_confidence`、`max_confidence`、`created_after/before`、`occurred_after/before`、`pinned`、`include_deleted`。

`focus_id` 用于图谱→表格联动：后端计算该记忆在当前筛选/排序下所在页，返回对应页并回填 `focus_id`。

### 4.2 创建

```http
POST /api/v1/memories
Content-Type: application/json

{
  "content": "用户喜欢手冲咖啡，不加糖。",
  "title": "咖啡偏好",
  "category": "preference",
  "memory_type": "long_term",
  "source_kind": "user_explicit",
  "importance": 0.78,
  "confidence": 0.92,
  "tags": ["咖啡", "偏好"],
  "scope": "user",
  "space_id": null,
  "deduplicate": true,
  "force": false
}
```

返回 `action=created|reinforced`、`memory`、`duplicate_of`、`relations`、`embedding_pending`。

## 5. 对话接入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/conversations/ingest` | 标准接入 |
| POST | `/ingest` | 别名 |

```json
{
  "conversation_id": "conv-123",
  "character_id": "mori",
  "character_name": "Mori",
  "async_mode": true,
  "messages": [
    { "id": "m1", "role": "user", "content": "我喜欢喝绿茶。" },
    { "id": "m2", "role": "assistant", "content": "记住啦。" }
  ]
}
```

- `async_mode=true`：写入 `extract_conversation` 任务并立即返回 `task_id`。
- `async_mode=false`：同步提取，返回候选数、创建数、强化数、记忆 ID。
- 用户/角色/会话空间会自动创建；`character_name` 仅用于给角色空间命名。
- 带 `character_id` 时，用户持久事实会写入该角色空间，不会进入共享用户空间。
- 默认只提取 user 消息；`extraction.include_assistant=true` 才提取助手消息（来源标记为 `ai_inference`）。

## 6. 检索与上下文

### 6.1 `POST /search`

```json
{
  "query": "咖啡",
  "mode": "recall",
  "limit": 10,
  "token_budget": 2000,
  "space_id": null,
  "memory_types": ["long_term", "preference"],
  "statuses": ["active"],
  "min_importance": 0.3,
  "record_access": false,
  "expand_relations": true
}
```

返回每条命中的 `scores`：

```json
{
  "semantic": 0.42,
  "keyword": 0.81,
  "importance": 0.78,
  "confidence": 0.92,
  "recency": 0.74,
  "strength": 0.88,
  "relation_boost": 0.0
}
```

以及 `reasons`、`token_estimate`、`scope`、`tokens_used`、`truncated`。

### 6.2 `POST /context`

MoriBot 生成回复前调用。默认 `record_access=true`，返回：

```json
{
  "context": "以下是从 MoriMemory 检索到的长期记忆……",
  "context_tokens": 286,
  "items": [],
  "tokens_used": 240,
  "token_budget": 1600,
  "truncated": false
}
```

上下文中的来源标签：`用户明确表达`、`AI 推断`、`行为观察`、`模型归纳`。

## 7. 图谱

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/graph` | 节点/边投影，按需限制数量 |
| GET | `/graph/path` | 两记忆之间的最短关系路径 |

`/graph` 参数：`center_id`、`depth`（1..3）、`limit`（≤500）、`mode`，以及列表接口的筛选参数。`center_id` 用于局部展开；`limit` 用于按需加载。

节点包含 `space_kind`、`memory_type`、`category`、`importance`、`confidence`、`retention`、`source_kind`、`status`、时间等。边包含 `relation_type`、`strength`、`confidence`、`directed`。

`/graph/path?source_id=&target_id=&max_depth=` 返回 `found`、`node_ids`、`edge_ids`、`depth`。

## 8. 统计与事件

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/stats?mode=manage` | 总量、状态/类型/分类/空间分布、关系数、待巩固、任务、存储、最近事件 |
| GET | `/events` | 审计事件分页，支持 `memory_id`、`event_type` |
| GET | `/entities` | 实体列表，支持 `space_id` 和 `limit` |

## 9. 授权

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/grants?space_id=&grantee_id=` | 列出可见授权 |
| POST | `/grants` | 用户空间/单条记忆授权给角色 |
| DELETE | `/grants/{id}` | 撤销 |

```json
{
  "space_id": "user-space-uuid",
  "memory_id": null,
  "grantee_kind": "character",
  "grantee_id": "mori",
  "permission": "read",
  "expires_at": null
}
```

## 10. 配置与元数据

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/config` | 脱敏配置；不返回 token/secret/API Key 明文 |
| PATCH | `/config` | 修改 embedding/extraction/retrieval/memory/vector/tasks |
| POST | `/config/embedding/test` | 测试当前或临时 Provider |
| GET | `/meta` | 枚举值（类型/分类/状态/关系/权限） |

API Key 通过 `api_key` 字段提交，服务端使用安装密钥加密后写入
`embedding.api_key_encrypted`，接口只返回 `api_key_set=true/false`。

## 11. 导入导出

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/transfer/export?format=json|csv` | 按当前筛选导出，JSON/CSV |
| POST | `/transfer/import` | 导入 JSON 文档或 CSV 文本 |

CSV 带 UTF-8 BOM，列：`id, space_id, space_kind, title, content, memory_type, category, source_kind, status, importance, confidence, retention, tags, occurred_at, created_at, updated_at, attributes`。

导入默认 `source_kind=ai_inference`（除非文件明确声明），并标记 `attributes.imported=true`，避免把未知来源伪装成用户陈述。

## 12. 备份

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/backups` | 备份列表 |
| POST | `/backups` | 创建 ZIP |
| GET | `/backups/{name}` | 下载 |
| DELETE | `/backups/{name}` | 删除 |
| POST | `/backups/restore` | multipart 上传 ZIP 恢复 |

恢复会校验 manifest、SHA-256、ZIP 路径安全和 SQLite `integrity_check`，并保留
`morimemory.pre-restore-*.db` 安全副本。恢复保留当前 `config.json` 和令牌，避免客户端被旧配置锁死。

## 13. 任务与维护

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/tasks` | 任务列表（状态/类型/分页） |
| POST | `/tasks/{id}/retry` | 重试 |
| POST | `/maintenance/decay` | 立即扫描衰减 |
| POST | `/maintenance/consolidate` | 立即归纳 |
| POST | `/maintenance/rebuild-index` | 重建 FTS/可选向量 |
| POST | `/maintenance/reembed` | 批量重算 embedding |
| POST | `/maintenance/embed/{memory_id}` | 单条重算 |

## 14. 错误码

| HTTP | code | 含义 |
| --- | --- | --- |
| 401 | `unauthorized` | 缺少/错误服务令牌 |
| 403 | `access_denied` | 空间/角色/来源不允许 |
| 404 | `not_found` | 记忆/空间/关系/任务不存在 |
| 409 | `conflict` | 重复空间、冲突操作 |
| 422 | `validation_error` | 参数校验失败 |
| 503 | `memory_unavailable` | MoriBot 代理无法连接 Memory Service |
