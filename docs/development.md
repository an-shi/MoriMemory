# 开发指南

## 1. 环境

- Python 3.12+（`uv` 会自动读取 `.python-version`）
- Node.js 20+
- pnpm 10/11

## 2. 后端

```bash
uv sync --extra dev
uv run morimemory init
uv run morimemory token
uv run morimemory serve
```

或者：

```bash
uv run main.py
```

默认数据目录 `data/`，服务地址 `http://127.0.0.1:6190`。

### 2.1 测试

```bash
uv run pytest -q
uv run ruff check morimemory tests scripts
uv run ruff format --check morimemory tests scripts
```

测试使用临时数据目录、`hashing` embedding、`tasks.workers=0`（除专门的 worker 测试）。
关键覆盖：

- 认证、Origin、配置脱敏；
- 记忆 CRUD、证据、版本、FTS 更新、级联硬删除；
- 去重、槽位矛盾、合并；
- 角色空间隔离、用户记忆授权/撤销；
- 混合检索、Token 预算、来源标签；
- 图谱投影、局部展开、路径追踪；
- 归纳不覆盖原始事件、衰减软遗忘、索引重建；
- JSON/CSV 导入导出、备份恢复、异步任务 worker。

### 2.2 演示数据

```bash
# 推荐：服务未运行时自动启动，生成完成后自动关闭
uv run python scripts/seed_demo.py --reset --start

# 服务已在另一个终端运行时
uv run morimemory serve
uv run python scripts/seed_demo.py --reset

# 保留脚本启动的服务，方便立刻打开前端
uv run python scripts/seed_demo.py --reset --start --keep-running
```

生成 36 条记忆、16 条关系、4 个空间，覆盖用户/角色/会话、来源类别、归纳摘要、
矛盾关系、证据链和不同重要性，便于前端调试。

脚本参数：

- `--base-url`：服务地址，默认 `http://127.0.0.1:6190/api/v1`
- `--token`：服务令牌；缺省读取 `data/config.json`
- `--wait`：等待服务就绪的秒数，默认 12
- `--start`：服务未运行时自动执行 `python -m morimemory serve`
- `--keep-running`：保留自动启动的服务进程

如果连接失败，脚本会给出明确提示；自动启动失败时查看
`data/logs/seed-service.log`。

> 部分代理软件会设置 `HTTP_PROXY` / `ALL_PROXY`，导致访问 `127.0.0.1`
> 也被转发到代理。演示脚本和 MoriBot 的嵌入式客户端已对本地服务禁用环境代理
> （`trust_env=False`），因此无需关闭全局代理。

## 3. 前端组件包

```bash
cd frontend
pnpm install
pnpm typecheck
pnpm test
pnpm build
```

产物：

```text
frontend/dist/index.js
frontend/dist/index.d.ts
frontend/dist/memory-ui.css
```

库模式仅外部化 `vue`、`vuetify`、`cytoscape`；Vuetify 组件通过 `vite-plugin-vuetify`
自动导入，宿主必须提供同版本 Vuetify 实例和全局组件。

### 3.1 独立开发壳

```bash
cd frontend
pnpm dev
```

打开 `http://127.0.0.1:5174`，Vite 把 `/api` 代理到 `127.0.0.1:6190`。
第一次需要粘贴 `morimemory token` 输出的令牌。开发壳故意保持和 MoriBot 一致的主题、
侧栏、顶栏和深色/浅色模式。

### 3.2 在 MoriBot 中调试

参见 `integrations/moribot/README.md`。MoriBot 集成时组件通过宿主的 Vuetify 实例渲染，
不创建第二个 `createApp()`，不加载第二套 Vuetify CSS，不使用 iframe。

## 4. 代码约定

- 时间统一使用 UTC naive datetime 存储，API 输出 ISO 字符串。
- 所有数据库访问通过 `Database.session()`，失败自动回滚。
- 生成版本时使用 `memory_snapshot()`，不要手写字段列表。
- 修改记忆内容后必须：
  - 更新 `content_hash`；
  - 更新 FTS；
  - 重新生成 embedding，失败则删除旧向量并入队 `embed_memory`。
- 删除记忆必须走 `MemoryRepository.delete_memory()`（FTS + 级联）。
- 新增空间类型时同步更新：
  - `domain.MemorySpaceKind`；
  - `SpaceAccessService`；
  - 前端 i18n `spaceKind`；
  - 导入/导出与测试。
- 新增关系类型时同步更新：
  - `domain.RelationType`；
  - 图谱颜色/虚线规则；
  - 前端 i18n `relation`。
- 新增 API 必须使用 `/api/v1` 前缀、统一信封、Pydantic 请求模型和后端权限校验。

## 5. 调试技巧

- `/api/docs` 交互式接口。
- `GET /api/v1/stats` 查看任务数、FTS 状态、向量后端、schema 版本。
- `GET /api/v1/tasks?status=failed` 查看失败任务与错误。
- 浏览器开发壳控制台无错误时，检查 `network.list` 的 `/api/v1/memory` 代理请求。
- 使用 `sqlite3 data/morimemory.db` 直接查看表，但生产环境不要直接修改。
- 修改 embedding 配置后，用 `POST /config/embedding/test` 验证连通性，再用
  `POST /maintenance/reembed` 重建向量。

## 6. 发布检查清单

- [ ] `uv run pytest -q` 全绿
- [ ] `uv run ruff check` 无错误
- [ ] `pnpm typecheck` 通过
- [ ] `pnpm build` 生成 `index.js`、`index.d.ts`、`memory-ui.css`
- [ ] `uv run morimemory init` 在新目录可创建 schema
- [ ] `/api/v1/health` 返回 `fts5=true`
- [ ] 备份创建/恢复演练成功
- [ ] MoriBot 集成文档中的代理路径与版本一致
