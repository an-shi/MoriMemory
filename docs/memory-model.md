# 记忆模型与生命周期

## 1. 空间与内容类型必须分开

### 1.1 记忆空间（Who can see it）

| 类型 | key 示例 | 所有者 | 用途 |
| --- | --- | --- | --- |
| User Memory | `user:local` | 用户 | 用户明确表达的长期事实、偏好、目标 |
| Character Memory | `character:mori` | 角色 | 角色私有设定、关系理解、模型推断 |
| Conversation Memory | `conversation:conv-123` | 会话 | 某次对话中的情景、临时上下文 |

空间在第一次写入时自动创建，不需要用户手工初始化。空间的 `user_id`、`character_id`、`conversation_id` 由后端从可信 principal 中获取，不能由前端随意覆盖。

### 1.2 记忆类型（How it behaves）

| 类型 | 典型来源 | 生命周期 |
| --- | --- | --- |
| `working` | 当前任务上下文 | 短、易被替换 |
| `short_term` | 本次会话的临时事实 | 跟随会话衰减 |
| `long_term` | 稳定偏好、身份、事实 | 长期保留，可被强化 |
| `episodic` | “今天发生了什么” | 可被归纳为语义记忆 |
| `semantic` | 归纳后的稳定知识 | 衰减慢、可被引用 |
| `associative` | 关系型知识 | 依赖关联图谱 |

### 1.3 内容分类

`identity`、`fact`、`preference`、`relationship`、`event`、`goal`、`plan`、`skill`、`opinion`、`habit`、`context`、`summary`、`other`。

分类影响默认重要性、提取规则和归纳分组，但不决定权限。

## 2. 来源证据与可信度

每条记忆都必须有至少一条 `memory_evidence`：

| 来源 | 含义 | 默认置信度 | 上限 |
| --- | --- | --- | --- |
| `user_explicit` | 用户直接说出 | 0.90 | 0.98 |
| `behavior_observation` | 从行为/使用模式观察 | 0.70 | 0.85 |
| `ai_inference` | 模型推断 | 0.55 | 0.70 |
| `model_summary` | 归纳生成的摘要 | 0.75 | 0.80 |

- 聊天消息提取时，证据保留 `conversation_id`、`message_id`、`role`、`quote`、`observed_at`。
- 手工创建或摘要记忆会生成 synthetic evidence，并标记 `attributes.synthetic=true`。
- LLM 提取器如果没有返回具体的 `evidence_message_id`，候选来源会被强制降级为 `ai_inference`，不能伪装成用户事实。
- `/context` 返回的提示词明确标注来源，生成模型不应把推断复述为用户事实。

## 3. 核心字段

| 字段 | 说明 |
| --- | --- |
| `importance` | 0..1，主观重要程度，可手工调整 |
| `confidence` | 0..1，来源可信度，受来源上限约束 |
| `retention` | 0..1，当前留存强度，由衰减任务计算 |
| `access_count` | 被检索并注入上下文的次数 |
| `reinforcement_count` | 被重复确认/强化的次数 |
| `contradiction_count` | 被判定为矛盾的次数 |
| `status` | `active` / `archived` / `superseded` / `disputed` / `deleted` |
| `version` | 乐观版本号，每次有效修改 +1 |
| `content_hash` | 标准化内容 SHA-256，用于精确去重 |
| `occurred_at` | 事件发生时间（区别于创建时间） |
| `last_reinforced_at` | 最近强化时间，衰减锚点 |
| `pinned` | 置顶记忆不参与自动遗忘 |

重要性、置信度、访问频率、重复证实次数分别存储、分别更新，不合并成单一“分数”。

## 4. 生命周期操作

### 4.1 提取（Extract）

- 双语启发式规则按句/分句拆分，抽取第一人称陈述、偏好、身份、关系、目标、事件等。
- 默认只提取用户消息，避免把角色台词当成用户事实。
- 可选 `extraction.mode=llm` 或 `hybrid`，调用 OpenAI-compatible chat endpoint，要求严格 JSON。
- LLM 失败自动回退启发式，不阻塞对话。

### 4.2 分类（Classify）

- 枚举值非法时回退到安全默认值。
- 根据分类设置默认重要性，根据来源设置置信度并封顶。
- 抽取槽位（姓名、居住地、工作、生日、宠物），写入 `attributes.slots`，用于矛盾检测。

### 4.3 去重（Deduplicate）

1. 同一空间内 `content_hash` 完全相同 → 强化已有记忆。
2. 有 embedding 时，余弦相似度 ≥ `dedup.duplicate_threshold`（默认 0.92）→ 强化。
3. 无 embedding 时，使用内容 hash + 高 Jaccard 词元重叠兜底。
4. 相似度 ≥ `similar_threshold`（默认 0.82）→ 新建记忆并建立 `related_to` 关系。
5. `force=true` 或 `deduplicate=false` 可跳过自动去重（人工导入、归纳摘要）。

强化会新增证据、增加 `reinforcement_count`、提升置信度、重置留存强度、记录版本和事件，但不会覆盖原内容。

### 4.4 矛盾（Contradict）

- 槽位冲突：同一 slot 不同值（如“住在上海” vs “住在北京”）。
- 否定冲突：高相似度且一方含否定词、另一方不含。
- 调用方显式指定 `contradicts_memory_id`。
- 新建记忆与旧记忆建立 `contradicts` 关系；旧记忆 `contradiction_count` +1，状态变为 `disputed`，置信度下降。
- 矛盾不自动删除任何一方；由用户或后续巩固决定。

### 4.5 合并（Merge）

`POST /api/v1/memories/{target}/merge`：

- 源记忆内容作为证据追加到目标；
- 证据行移动到目标；
- 关系重连到目标，避免图断裂；
- 目标重要性/置信度取较大值，强化/访问次数累加，标签合并；
- 源记忆标记 `superseded`，建立 `merged_into` 关系；
- 源和目标都保留版本快照。

### 4.6 归纳（Consolidate）

- 扫描同一空间、同一分类、最近 N 天（默认 30）且未被归纳的 `episodic` 记忆。
- 分组数量达到 `min_group_size`（默认 3）时，生成一条 `semantic` + `model_summary` 的新记忆。
- 新摘要与每条原始记忆建立 `consolidated_from` 关系。
- 原始记忆**不修改内容、不归档**，只在 `attributes` 中标记 `consolidated_at` / `consolidated_into`。
- 摘要的证据保留来源记忆 ID，可追溯。

### 4.7 衰减与软遗忘（Decay）

```text
half_life = base_half_life × (1 + importance×2 + min(access,20)×0.05 + min(reinforcement,20)×0.12)
retention = 2^(-age_days / half_life)
```

- 衰减锚点：`last_reinforced_at` → `last_accessed_at` → `created_at`。
- 置顶记忆默认免疫。
- `retention < forget_threshold` 且访问次数低于阈值 → 状态变为 `archived`，写入 `forgotten_soft` 事件。
- 软遗忘保留数据、证据、关系和索引，可随时恢复。
- 只有用户明确调用 `DELETE /api/v1/memories/{id}` 才会硬删除。

### 4.8 硬删除（Hard Delete）

同一事务内：

- 删除 `memories` 行 → 外键级联删除 evidence、relations、versions、embeddings、grants、memory_entities；
- 显式删除 FTS5 行；
- 写入 `deleted` 审计事件，payload 保留删除前快照；
- 目标空间保留，其他记忆不受影响。

## 5. 关系模型

| 关系 | 方向 | 含义 |
| --- | --- | --- |
| `related_to` | 有向 | 相似或上下文相关 |
| `supports` | 有向 | 一条记忆支持另一条 |
| `contradicts` | 有向 | 冲突 |
| `derived_from` | 有向 | 派生 |
| `part_of` | 有向 | 从属 |
| `causes` | 有向 | 因果 |
| `precedes` | 有向 | 时间先后 |
| `same_entity` | 有向 | 指向同一实体 |
| `merged_into` | 有向 | 合并去向 |
| `consolidated_from` | 有向 | 摘要来源 |

关系强度 `strength` 和置信度 `confidence` 分别存储，图谱边宽映射强度，颜色/虚线映射类型。

## 6. 实体

`entities` 保存规范名、类型、别名、描述；`memory_entities` 是多对多连接表。

启发式提取会从姓名、居住地、工作单位、宠物等槽位创建实体；LLM 提取器可返回实体列表。实体的作用：

- 图谱路径与聚类；
- 同一实体的记忆联想；
- 后续实体消歧和知识图谱扩展。

## 7. 版本与事件

- 创建时写入 v1 快照。
- 更新、强化、合并、归纳、遗忘、回滚都会增加版本或写入事件。
- 版本快照是不可变 JSON，包含内容、分类、评分、标签、属性、时间等。
- 回滚不会删除历史版本：回滚后 `version+1`，再写入新快照。
- `memory_events` 是审计流：`created` / `updated` / `reinforced` / `merged` / `archived` / `restored` / `deleted` / `forgotten_soft` / `consolidated` / `contradicted` / `shared` 等。

## 8. 异步任务

| 任务 | 触发 | 行为 |
| --- | --- | --- |
| `extract_conversation` | MoriBot 回复后 | 提取并保存对话记忆 |
| `embed_memory` | 写入时 embedding 失败 | 重试生成向量 |
| `reembed_all` | 更换 embedding 模型 | 批量重算 |
| `consolidate` | 每日维护 / 手动 | 归纳情景记忆 |
| `decay_scan` | 每日维护 / 手动 | 更新留存强度、软遗忘 |
| `rebuild_index` | 手动 | 重建 FTS，可选重建向量 |
| `backup` | 手动 / 调度 | 创建 ZIP 备份 |

任务持久化在 `async_tasks`，支持状态、优先级、尝试次数、错误、去重键和重试。
