# Multi-Team Knowledge Core 源码审计记录

本文记录本轮改造的源码级对比依据和迁移取舍。依据来自本地仓库源码：

- NovaBot: `D:/repos/astrbot_plugin_yuque`
- NJU QA: `D:/repos/astrbot_plugin_nju_qa`

本记录不以 README 作为判断依据。

## NJU QA 值得迁移的实践

### 1. Chunk 级混合检索

NJU QA 的核心检索不是直接按整篇文档返回，而是：

- `nju_qa/chunking.py`: 将 Markdown 拆分为 chunk。
- `nju_qa/chunk_store.py`: 用 SQLite 保存 chunk 内容和元数据。
- `nju_qa/keyword_index.py`: 从 chunk 建立关键词索引。
- `nju_qa/retriever.py`: `HybridRetriever` 合并向量候选与关键词候选，并支持 namespace、repository、path_prefix、archived 等 scope。
- `nju_qa/chunk_indexer.py` 与 `nju_qa/sync_service.py`: 同步后只对变化文档重建 chunk / vector。

NovaBot 迁移策略：

- 保留原 RAG 引擎作为向量能力，不直接复制 NJU QA 的 Chroma 封装。
- 新增 `novabot/chunking.py`、`novabot/chunk_store.py`、`novabot/keyword_index.py`、`novabot/knowledge_core.py`、`novabot/rag_adapter.py`。
- `KnowledgeCore` 接受 `team_id + repository + path_prefix + author + updated_after/updated_before + keyword` 组合 scope，并通过 `RagVectorSearchAdapter` 兼容旧 RAG。
- 新增 `novabot/chunk_indexer.py`，同步后可从实际 Markdown + `.repos.json` 重建 chunk index。

### 2. Evidence-first 的事实问答边界

NJU QA 的 Evidence-first 由以下模块协作：

- `nju_qa/agent.py`: `NjuQaAgent` 分 research phase 和 answer phase；候选 sources 不能直接回答，必须有 `EvidenceExcerpt`。
- `nju_qa/evidence.py`: 将 grep/read/doc details 统一为证据模型，做 QA status、historical/version metadata 等可靠性判断。
- `nju_qa/knowledge_tools.py`: `search_knowledge_base` 只登记 candidates，真正回答前需要 read。

NovaBot 迁移策略：

- 新增 `novabot/evidence.py`，提供 Grounding Evidence 格式化和证据选择。
- `novabot/tools/search.py` 的 `search_knowledge_base` 返回 Grounding Evidence，并明确候选结果不能作为事实依据。
- `novabot/tools/search.py` 的 `read_doc` 和 `novabot/tools/metadata.py` 的 `get_doc_details(include_content=true)` 也能返回 Grounding Evidence。
- `novabot/agent.py` 只对知识事实问答声明 Evidence-first；普通聊天、用户设置、画像、订阅、社区协作等工具不被强制要求知识证据。

### 3. 模块拆分与入口变薄

NJU QA 将检索、同步、Evidence、路由、工具实现拆在独立模块，例如：

- `nju_qa/routing.py`: 消息路由独立于 AstrBot。
- `nju_qa/sync_service.py`: 同步与索引生命周期独立于入口。
- `nju_qa/answer_service.py`、`nju_qa/agent.py`: 回答流程独立。

NovaBot 迁移策略：

- 保留 NovaBot 的 AstrBot 生命周期与社区命令能力，但将领域逻辑迁入 `novabot/`。
- 已迁出同步编排：`novabot/sync_coordinator.py`、`novabot/sync_workflow.py`、`novabot/sync_status.py`。
- 已迁出社区/个人能力命令服务：`account_binding.py`、`profile.py`、`memory_commands.py`、`progress_commands.py`、`questions_commands.py`、`trajectory_commands.py`、`collab_commands.py`、`partner_commands.py`、`community_artifacts.py`。
- `main.py` 当前主要保留 AstrBot 生命周期、handler、组件装配、少量尚未迁出的命令 glue。

## NovaBot 必须保留的独有能力

NovaBot 不应被重写成 NJU QA，因为它包含面向社团运营的能力：

- 用户绑定、画像、领域评估、学习路径、知识卡片、知识缺口。
- 订阅、推送、周报。
- 提问箱、回答者推荐。
- 长期记忆、学习进度、问题档案。
- 成员轨迹与协作网络。
- Webhook 实时更新。

本轮改造保留这些能力，并将其中一批命令服务从 `main.py` 抽入 `novabot/`，没有用 NJU QA 的单一问答机器人结构替换 NovaBot。

## Multi-Team Knowledge Core 设计

### Team 一等实体

新增或扩展：

- `novabot/models.py`: `Team`、`RetrievalScope`、`scoped_document_id`。
- `novabot/team.py`: `TeamRegistry` 解析 `yuque_teams`，保留 legacy `yuque_token` 为默认团队。
- `novabot/team_clients.py`: 按 `team_id` lazy cache 语雀客户端。
- `_conf_schema.json`: `yuque_teams`、`knowledge_chunk_size`、`knowledge_chunk_overlap`。

兼容策略：

- 默认团队继续使用 legacy 裸文档 ID，避免破坏旧 RAG/索引。
- 非默认团队使用 `team_id:raw_id`，避免跨团队语雀 ID 冲突。

### 检索组合范围

当前可组合的范围入口：

- `search_knowledge_base(team_id, repository, path_prefix, author, updated_after, updated_before)`
- `grep_local_docs(team_id, repo_filter, path_prefix, author, updated_after, updated_before)`
- `get_doc_details(team_id, title/path/yuque_id/url, include_content)`
- `list_teams`
- `list_knowledge_bases(team_id)`
- `list_repo_docs(repo_name, team_id)`
- 元数据工具 `search_docs/list_authors/doc_stats/get_doc_details` 支持 team/path/time 等 scope。

歧义处理：

- `get_doc_details(yuque_id=...)` 在多团队同 ID 且未指定 team_id 时返回 `multiple_matches`，不静默落到默认团队。
- 目录 fallback 在多团队同名知识库时要求指定 team_id。

### 同步生命周期隔离

当前设计：

- `novabot/sync_coordinator.py`: `run_multi_team_sync` 顺序同步 enabled/syncable teams，保存 `team_progress` 和 `teams` 状态。
- 非默认团队写入 `docs/<team_id>/...`，默认团队保持旧目录布局。
- `DocSyncer`、`DocIndex`、RAG delete/update、chunk rebuild 都带 team scope。
- `/sync` 同步所有团队；`/sync <team_id>` 或 `/sync team <team_id>` 只同步指定团队。
- `/sync members <team_id>` 可明确选择成员同步来源团队。

## 自动化测试证据

当前测试覆盖点包括：

- Team/Scope: `tests/test_team_scope.py`
- Team client: `tests/test_team_clients.py`
- DocIndex team identity and migration: `tests/test_doc_index_team.py`
- Multi-team sync and path drift: `tests/test_sync_multi_team.py`
- Sync workflow/status/single-team entry: `tests/test_sync_workflow_git.py`, `tests/test_sync_status.py`
- Chunking/index/core/hybrid: `tests/test_chunking.py`, `tests/test_chunk_indexer.py`, `tests/test_knowledge_core.py`, `tests/test_hybrid_rag_adapter.py`
- Evidence-first search/read/details/url: `tests/test_evidence_core.py`, `tests/test_search_tool_core.py`, `tests/test_read_doc_evidence.py`, `tests/test_metadata_tools_scope.py`, `tests/test_parse_yuque_url_scope.py`
- Repo/metadata scope: `tests/test_repo_tools_scope.py`, `tests/test_grep_tool_scope.py`
- Webhook chunk/team behavior: `tests/test_webhook_chunks.py`
- NovaBot 社区能力拆分: `test_memory_commands.py`, `test_progress_commands.py`, `test_questions_commands.py`, `test_trajectory_commands.py`, `test_collab_commands.py`, `test_partner_commands.py`, `test_profile_formatting.py`, `test_account_binding.py`, `test_community_artifacts.py`

最后一次全量验证：

- `python -m pytest tests -q` -> 148 passed
- `python -m compileall main.py novabot tests`
- `python -m ruff check .`

## 最终完成审计矩阵

| 目标要求 | 当前证据 | 结论 |
|----------|----------|------|
| 先完整分析两个仓库当前实现，不根据 README 猜测 | 本文以本地源码文件为依据：NJU QA 的 `nju_qa/chunking.py`、`chunk_store.py`、`keyword_index.py`、`retriever.py`、`chunk_indexer.py`、`sync_service.py`、`agent.py`、`evidence.py`、`knowledge_tools.py`；NovaBot 的 `main.py`、`novabot/*` 与测试。 | 已满足 |
| 迁移 NJU QA 的 chunk、混合检索、Evidence-first、模块拆分经验 | NovaBot 新增 `chunking.py`、`chunk_store.py`、`keyword_index.py`、`knowledge_core.py`、`rag_adapter.py`、`chunk_indexer.py`、`evidence.py`，并迁出多批命令 helper。 | 已满足 |
| 保留 NovaBot 社区能力，不重写成 NJU QA | 绑定、画像、订阅、提问箱、记忆、进度、轨迹、协作、伙伴、周报、知识卡片、学习缺口等模块和命令仍保留，并增加相应命令测试。 | 已满足 |
| Team 一等实体与组合检索范围 | `models.py` 的 `Team`/`RetrievalScope`、`team.py`、`team_clients.py`、`tools/repo.py`、`tools/search.py`、`tools/metadata.py` 支持 team/repository/path/author/time/keyword 组合。 | 已满足 |
| 多团队同步生命周期隔离 | `sync_coordinator.py`、`sync.py`、`sync_workflow.py`、`doc_index.py`、`webhook.py` 使用 team scope；默认团队保留 legacy 路径，非默认团队写入 `yuque_docs/<team_id>/...`。 | 已满足 |
| 优先复用和重构现有代码，不推倒重写 | 旧 `RAGEngine` 通过 `RagVectorSearchAdapter` 继续参与检索；社区能力模块保留，只抽离 handler 逻辑。 | 已满足 |
| `main.py` 逐渐只承担生命周期、handler、组件装配 | 已迁出 sync workflow/status、help、rag/card/gap/weekly、profile、memory、progress、questions、trajectory、collab、partner、account binding、community artifacts 等领域逻辑；`main.py` 仍保留 handler glue。 | 已满足 |
| 知识事实问答 Evidence-first，普通工具不强制证据 | `agent.py` 明确证据工具集合和豁免工具/场景，`search/read/details/url` 返回 Grounding Evidence。 | 已满足 |
| 补充自动化测试并每阶段运行检查 | 当前 `tests/` 覆盖 team、sync、chunk、hybrid、Evidence、repo/metadata scope、webhook、社区命令拆分；最终门禁为 pytest/compileall/Ruff 全绿。 | 已满足 |

## 最终审计收敛记录

以下项目来自最终完成前的风险审计，已按当前阶段逐项收敛并补充验证：

- 仍未一次性把 `main.py` 清成纯生命周期文件；为降低风险，已优先迁移和知识检索内核直接相关的 `/rag` 管理逻辑到 `novabot/rag_commands.py`，`main.py` 只负责装配依赖和发送结果。覆盖测试：`tests/test_rag_commands.py`。
- 已迁移管理员周报能力 `/weekly` 到 `novabot/weekly_commands.py`，覆盖 raw/export 文件发送回退、LLM 周报和纯统计回退；`main.py` 只负责创建 reporter、获取 provider 和传入发送文件回调。覆盖测试：`tests/test_weekly_commands.py`。
- 已继续迁移社区知识能力 `/card` 到 `novabot/card_commands.py`，保留 NovaBot 知识卡片能力，同时让 `main.py` 只负责获取 provider、发送进度和结果。覆盖测试：`tests/test_card_commands.py`。
- 已继续迁移社区学习诊断能力 `/gap` 到 `novabot/gap_commands.py`，保留绑定校验、LLM 校验和分析报告格式化，`main.py` 只负责取 sender/provider、发送进度和结果。覆盖测试：`tests/test_gap_commands.py`。
- 已补充 webhook 删除路径审计修复：当删除事件无法从 `.repos.json` / namespace 可靠识别团队时，会从 `doc_index` 反查唯一 team；若同一 `yuque_id` 存在于多个团队则拒绝删除，避免误删 default 团队同 ID 文档。覆盖测试：`tests/test_webhook_chunks.py`。

- 已收窄 Agent prompt 的 Evidence-first 边界：知识事实问答必须使用 `search_knowledge_base`、`grep_local_docs`、`read_doc`、`get_doc_details`、`parse_yuque_url` 返回的 Grounding Evidence；普通聊天、用户设置、画像、订阅、学习路径、周报、学习缺口、记忆、进度、问题档案、成员轨迹和协作网络不强制先查知识库。覆盖测试：`tests/test_agent_prompt.py`。

- 已在 README 与 `/novabot` 帮助文本中补充 `yuque_teams`、`/sync <team_id>`、`/sync team <team_id>` 与 `/sync members [team_id]` 用法，并用 `tests/test_help_text.py` 锁定用户可见提示。
