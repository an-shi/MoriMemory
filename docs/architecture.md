# 架构设计

MoriMemory 是一个本地优先的长期记忆引擎，作为 MoriBot 的嵌入式子系统运行。它独立开发、独立发版，但运行时由 MoriBot 自动启动、代理和关闭。

## 1. 设计原则

1. **记忆与生成分离**：MoriMemory 不生成回复、不扮演角色，只负责提取、存储、检索、巩固和遗忘。
2. **空间与内容分离**：记忆空间（User / Character / Conversation）决定可见性；记忆类型（工作 / 短期 / 长期 / 情景 / 语义 / 关联）决定生命周期。
3. **权限在后端执行**：所有读取都经过 `SpaceAccessService`，前端过滤只是体验层，不能作为安全边界。
4. **证据先于结论**：每条记忆至少保留一条证据，来源类别不可被推断结果伪装成用户事实。
5. **可插拔基础设施**：Embedding Provider、向量索引、LLM 提取器都可以替换，不绑定单一厂商。
6. **零外部服务即可启动**：默认 `hashing` embedding + SQLite 精确向量检索，离线可用。
7. **原始数据不可被归纳覆盖**：归纳生成新记忆，原情景记忆保留并建立 `consolidated_from` 关系。
8. **所有删除可追溯**：硬删除同步清理证据、关系、版本、FTS 和向量，并保留一条删除审计事件。

## 2. 分层视图

```text
┌─────────────────────────────────────────────────────────────┐
│ MoriBot Vue 3 + Vuetify 3                                   │
│ @moriai/memory-ui · MemoryExplorer · Cytoscape.js            │
└──────────────────────────┬──────────────────────────────────┘
                           │ JWT /api/v1/memory/*
┌──────────────────────────▼──────────────────────────────────┐
│ MoriBot FastAPI proxy                                       │
│ 校验 JWT → 注入服务令牌 / 用户身份 → 转发                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ Bearer token, 127.0.0.1
┌──────────────────────────▼──────────────────────────────────┐
│ MoriMemory FastAPI                                          │
│ /api/v1/health · spaces · memories · search · graph · tasks  │
│ config · transfer · backups · maintenance                    │
└──────┬──────────────────────┬──────────────────────┬─────────┘
       │                      │                      │
┌──────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐
│  Engine     │        │ Repository  │        │  Services   │
│ extraction  │        │ SQLAlchemy  │        │ embedding   │
│ retrieval   │        │ FTS5        │        │ backup      │
│ consolidation│       │ vector      │        │ transfer    │
│ decay/tasks │        │ permissions │        │ task runner │
└──────┬──────┘        └──────┬──────┘        └─────────────┘
       │                      │
┌──────▼──────────────────────▼───────────────────────────────┐
│ SQLite WAL + FTS5 + packed float32 vectors + config.json     │
└─────────────────────────────────────────────────────────────┘
```

## 3. 目录职责

| 路径 | 职责 |
| --- | --- |
| `morimemory/config.py` | 默认配置、递归补全、原子写入、令牌/密钥生成、敏感字段脱敏 |
| `morimemory/paths.py` | 数据目录、数据库、日志、备份目录解析 |
| `morimemory/security.py` | Bearer token 常量时间比较、Fernet 密钥加密 |
| `morimemory/db/models.py` | SQLAlchemy 2.x 实体 |
| `morimemory/db/migrations.py` | schema 版本表、前向迁移、FTS 建表 |
| `morimemory/db/fts.py` | CJK unigram/bigram 分词、FTS 查询、重建 |
| `morimemory/db/repository.py` | 空间、记忆、证据、关系、授权、版本、实体、向量、任务的数据访问 |
| `morimemory/embeddings.py` | Hashing 与 OpenAI-compatible Embedding Provider |
| `morimemory/vector_store.py` | 向量索引接口、SQLite 精确余弦实现、后端注册表 |
| `morimemory/engine/access.py` | `manage` / `recall` 两种权限模式，User / Character / Conversation 隔离 |
| `morimemory/engine/extraction.py` | 双语启发式提取、可选 LLM 提取、分类与置信度上限 |
| `morimemory/engine/core.py` | 空间解析、创建、去重、矛盾、合并、版本、删除、统计 |
| `morimemory/engine/retrieval.py` | 关键词 + 向量 + 时间 + 关系混合检索，Token 预算与上下文组装 |
| `morimemory/engine/graph.py` | 图谱投影、局部展开、路径追踪 |
| `morimemory/engine/maintenance.py` | 归纳、衰减、embedding 回填、索引重建 |
| `morimemory/engine/tasks.py` | SQLite 持久任务队列、worker、维护调度器 |
| `morimemory/services/backup.py` | SQLite 在线快照、ZIP manifest、SHA-256 校验、恢复 |
| `morimemory/services/import_export.py` | JSON / CSV 导入导出 |
| `frontend/` | `@moriai/memory-ui` Vue 组件包与独立开发壳 |
| `integrations/moribot/` | 进程生命周期管理器、代理路由、集成说明 |

## 4. 运行生命周期

MoriBot 启动：

1. 解析 `data/`，读取 `data/memory` 下的服务配置。
2. `MemoryServiceManager.ensure_started()`：
   - 先请求 `GET /api/v1/health`；健康则复用现有服务。
   - 否则检查 `data/memory/memory-service.lock` 与 PID，防止重复启动。
   - 生成/读取服务令牌，通过 `MORIMEMORY_TOKEN` 注入子进程。
   - `uv run --project <MoriMemory> morimemory serve --data-dir <data/memory>`。
   - 轮询健康检查（默认 30 秒超时）。
3. MoriBot 注册 `/api/v1/memory/*` 代理；浏览器请求携带 JWT。
4. MoriMemory FastAPI lifespan：
   - `Database.initialize()`：建表、FTS5、迁移到最新 schema。
   - 注册任务处理器。
   - 启动 0..N 个任务 worker。
   - 启动维护调度器（默认启动时入队一次衰减与归纳）。
5. 关闭时：MoriBot 停止自己拥有的子进程；MoriMemory 停止 worker、关闭 embedding client、释放数据库连接。

独立运行时，用户直接执行：

```bash
uv run morimemory serve
```

两种模式使用完全相同的数据结构和 API。

## 5. 写入流程

```text
对话消息
  └─ ExtractionService
       ├─ LLM 提取（可选，失败自动回退）
       └─ 启发式提取
            └─ normalize_candidate
                 ├─ 枚举校验
                 ├─ 来源置信度上限
                 ├─ 槽位抽取（姓名/居住地/工作/生日/宠物）
                 └─ 实体抽取
  └─ MemoryEngine.create_memory
       ├─ 解析目标空间并校验写权限
       ├─ 精确 hash 去重
       ├─ 向量相似度去重 / 相似关系
       ├─ 槽位冲突 + 否定词检测矛盾
       ├─ 写入 memory + evidence + version
       ├─ FTS5 索引
       ├─ embedding（失败则入队重试）
       ├─ 实体 upsert + link
       └─ 审计事件
```

批量对话提取默认异步：`POST /conversations/ingest` 写入 `async_tasks` 后立即返回，任务 worker 在后台完成提取和保存。

## 6. 读取流程

```text
POST /context
  └─ RetrievalService.search
       ├─ 解析 AccessScope（recall）
       ├─ 关键词候选（FTS5 BM25）
       ├─ 语义候选（query embedding + vector index）
       ├─ 单条授权记忆（memory-level grants）
       ├─ 关系扩展（related_to/supports/part_of/... 一跳）
       ├─ 过滤：空间、类型、分类、状态、时间、标签、可信度
       ├─ 打分并排序
       ├─ Token 预算截断
       └─ 组装带来源标签的上下文文本
```

混合打分默认权重：

```text
0.35 × semantic + 0.20 × keyword + 0.15 × importance
+ 0.10 × confidence + 0.10 × recency + 0.10 × strength
+ relation_boost（最大约 0.12）+ pinned_boost（0.05）
```

权重、候选池、Token 预算、时间半衰期都可以在 `config.json` 的 `retrieval` 中调整。

## 7. 权限模型

两种访问模式：

| 模式 | 使用方 | 可见范围 |
| --- | --- | --- |
| `manage` | Memory Explorer 管理界面 | 管理员可浏览自己的用户空间、自己的会话空间、所有角色空间（可通过配置关闭） |
| `recall` | 回复前检索 | 当前角色空间、当前角色过去/当前的会话空间、显式授权的空间/单条记忆 |

- 角色对话中的长期事实、偏好、身份等全部写入该角色空间，不写入共享用户空间。
- 角色 A 的角色空间和会话空间不会被角色 B 读取。
- 用户空间和跨角色会话空间默认不可见，必须通过 `/api/v1/grants` 显式授权；授权可过期、可撤销。
- `GET /api/v1/health` 公开；其余接口必须带服务令牌。
- MoriBot 代理层必须保留 JWT 校验，浏览器永远不接触服务令牌。

## 8. 存储与可迁移性

所有可变数据位于 `MORIMEMORY_DATA_DIR`（默认 `data/`）：

```text
data/
  config.json           # 配置、token、加密后的 API Key
  morimemory.db         # SQLite（WAL）
  morimemory.db-wal
  morimemory.db-shm
  logs/morimemory.log
  backups/*.zip
  memory-service.lock    # 仅嵌入式模式
  service-token          # 仅嵌入式模式
```

MoriBot 把 `data/memory` 放在自己的 `data/` 下，因此现有 ZIP 备份会自动包含记忆数据库。MoriMemory 自己的备份额外包含 `manifest.json`、SHA-256 校验和 SQLite 完整性检查。

## 9. 为什么不用外部向量数据库

本地个人助手的记忆量通常在数千到数万条，SQLite 精确余弦扫描足够快，且：

- 零部署、零网络依赖；
- 备份/恢复只有一个数据库文件；
- 不会因为向量库进程未启动而丢失“记忆”这一核心能力；
- 通过 `VectorIndex` 接口和注册表，仍可替换为 Qdrant、Milvus 或其他后端，而不改变检索管线。

## 10. 与 MoriBot 的边界

MoriMemory 负责：

- 记忆数据模型、提取、去重、矛盾、归纳、衰减、检索、权限、审计、备份。

MoriBot 负责：

- 会话和消息、模型回复、角色卡、用户账户、启动/停止 Memory Service、JWT、代理、页面入口。

这样 MoriMemory 可以独立测试和发布，MoriBot 也不需要了解记忆库内部表结构。
