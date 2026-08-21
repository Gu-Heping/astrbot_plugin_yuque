# NovaBot

> NOVA 社团智能助手 · 以语雀知识库为核心的 AstrBot Plugin

完全自包含，内置语雀同步 + RAG 检索 + 元数据索引，不依赖外部服务。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **语雀同步** | 全量同步 + Webhook 实时同步，输出 Markdown + YAML frontmatter，支持多语雀团队隔离同步 |
| **RAG 检索** | Evidence-first 混合检索：Chunk 关键词召回 + 向量语义搜索 + 元数据范围过滤 |
| **自然语言交互** | 直接对话即可，AI 自动识别意图调用工具 |
| **用户画像** | LLM 分析用户文档，生成技术画像和兴趣领域 |
| **人格定制** | 每个用户可设置称呼、语气、回复风格 |
| **学习辅助** | 知识卡片、学习路径、伙伴推荐、学习缺口分析 |
| **智能推送** | LLM 判断变更价值，订阅知识库/作者更新 |
| **知识问答** | 实名提问、多人回答、点赞机制，促进知识共享 |
| **知识库层** | 按知识库查看概览、贡献者、范围检索 |

---

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_yuque.git
```

---

## 配置

### 核心配置

| 配置项 | 说明 |
|--------|------|
| `yuque_token` | 默认语雀团队 Token（单团队部署必需；多团队部署可作为默认团队） |
| `yuque_base_url` | 默认语雀 API 地址，默认 `https://www.yuque.com/api/v2`；也可填写 `https://nova.yuque.com/` 这类 Web 地址，系统会规范化为 `/api/v2` |
| `yuque_teams` | 可选，多语雀团队 JSON 数组；留空时沿用 `yuque_token` 单团队模式；支持 token-first 自动发现 |
| `embedding_api_key` | Embedding API Key（必需） |
| `embedding_base_url` | Embedding API 地址（可选） |
| `embedding_model` | Embedding 模型，默认 `text-embedding-3-small` |

#### 多语雀团队

`yuque_teams` 将 Team 作为一等实体。每个 Team 拥有独立 Token、API 地址、同步生命周期、文档路径、元数据索引和检索范围；Agent 会根据 Team 名称、描述、知识库、路径、作者、更新时间与关键词自行组合检索范围。

示例：

最小配置只需要提供各团队 token，NovaBot 会在启动或同步前调用语雀 API 自动补齐 Team：

```json
[
  { "yuque_token": "token-a" },
  { "yuque_token": "token-b" }
]
```

也可以保留人工描述，方便 Agent 判断检索范围：

```json
[
  {
    "yuque_token": "token-a",
    "description": "2025 新手团队知识库"
  },
  {
    "yuque_token": "token-b",
    "description": "2026 寒假项目知识库"
  }
]
```

完整显式配置仍然受支持，且手动 `team_id` 优先：

```json
[
  {
    "team_id": "nova",
    "name": "NOVA",
    "description": "社团公共知识库、活动记录与项目沉淀",
    "yuque_token": "xxx",
    "yuque_base_url": "https://www.yuque.com/api/v2",
    "enabled": true
  },
  {
    "team_id": "research",
    "name": "Research",
    "description": "科研资料、论文笔记与课题协作",
    "yuque_token": "yyy",
    "enabled": true
  }
]
```

兼容说明：

- 未配置 `yuque_teams` 时，NovaBot 继续使用原来的 `yuque_token` 和原始文档目录。
- `yuque_teams` 支持完整对象、token-only 对象和字符串 token 简写；缺少 `team_id`/`name` 时，系统用 `get_user()` 自动发现并生成 `group_<id>` 或 `user_<id>`。
- 自动发现结果只在内存中使用，不会写回 AstrBot 配置；如两个 token 发现出同一个团队，只保留第一项并记录重复配置错误。
- 默认团队保持旧文档 ID 与目录布局；非默认团队写入 `yuque_docs/<team_id>/<知识库名>/...`。
- 文档身份按 `team_id + yuque_id` 区分，同一个语雀文档 ID 可在不同团队中共存。
- 多团队检索可组合 `team_id`、知识库、路径前缀、作者、更新时间、关键词和语义查询；带时间范围的检索只匹配有更新时间元数据的文档。

### Webhook 实时同步

| 配置项 | 说明 |
|--------|------|
| `webhook_enabled` | 启用 Webhook 服务 |
| `webhook_port` | 服务端口，默认 `8766` |
| `webhook_ip_whitelist` | IP 白名单（语雀服务器：`47.96.64.251`） |

### Git 版本控制

| 配置项 | 说明 |
|--------|------|
| `git_enabled` | 启用 Git 版本控制（默认 true） |
| `git_auto_push` | 自动推送到远程仓库 |

### 消息路由

| 配置项 | 说明 |
|--------|------|
| `wake_words` | 唤醒词（默认：novabot,nova,诺瓦） |
| `enable_private_chat` | 私聊直接响应（默认 true） |
| `enable_group_at` | 群聊 @ 触发（默认 true） |
| `group_reply_mode` | 群聊回复模式：`trigger`（默认，仅 @/唤醒词）或 `smart`（智能旁听，Bot 自行决定是否回复） |

### 智能推送

| 配置项 | 说明 |
|--------|------|
| `push_enabled` | 启用智能推送 |
| `push_min_diff_chars` | 最小变更字符数 |
| `push_max_content_len` | 推送内容最大长度 |

---

## 指令

### 用户指令

| 指令 | 说明 |
|------|------|
| `/bind <用户名>` | 绑定语雀账号 |
| `/unbind` | 解除绑定 |
| `/profile` | 查看用户画像 |
| `/profile refresh` | 刷新用户画像 |
| `/profile assess <领域>` | 评估某领域掌握程度 |
| `/persona` | 查看人格设置 |
| `/persona name/tone/style/formality <值>` | 设置偏好 |
| `/persona reset` | 重置为默认 |
| `/partner [主题]` | 伙伴推荐 |
| `/path <领域>` | 学习路径推荐 |
| `/card <主题>` | 生成知识卡片 |
| `/gap [领域]` | 学习缺口分析 |
| `/subscribe` | 查看订阅 |
| `/subscribe repo/author/all` | 订阅更新 |
| `/unsubscribe <ID>` | 取消订阅 |
| `/kb` | 列出知识库 |
| `/kb <知识库>` | 查看知识库概览 |
| `/kb <知识库> <问题>` | 在知识库范围内问答 |
| `/ask <问题>` | 提问（需绑定） |
| `/ask list/view/answer/like/mine` | 问答操作 |
| `/novabot` | 帮助信息 |

### 管理员指令

| 指令 | 说明 |
|------|------|
| `/sync` | 同步全部已启用团队知识库 |
| `/sync <team_id>` 或 `/sync team <team_id>` | 只同步指定团队 |
| `/sync status` | 查看同步状态 |
| `/sync members [team_id]` | 同步团队成员；传入 `team_id` 时使用指定团队 Token |
| `/rag search <关键词>` | RAG 搜索 |
| `/rag rebuild` | 重建索引 |
| `/webhook` | Webhook 服务状态 |
| `/weekly` | 本周知识周报（管理员） |
| `/weekly raw` | 导出“全部文档按周原始统计”CSV（管理员） |
| `/tokens` | Token 消耗统计 |
| `/askreset` | 重置问答数据 |

---

## 自然语言交互

NovaBot 支持直接对话，无需记忆指令：

```
用户: 帮我找爬虫教程
NovaBot: 我帮你找到了几篇爬虫相关的文档...

用户: 张三写过哪些文档
NovaBot: 张三共写了 12 篇文档，主要涉及...

用户: 叫我小明
NovaBot: 好的，小明！有什么需要帮忙的？

用户: 说话活泼一点
NovaBot: 好嘞～以后会更活泼地和你聊天！
```

### 触发方式

- **私聊**：直接发消息即可（可配置关闭）
- **群聊（`group_reply_mode=trigger`，默认）**：需要 @NovaBot 或使用唤醒词（如 `nova 帮我找文档`）
- **群聊（`group_reply_mode=smart`）**：@ 与唤醒词仍必回；其余群消息由 Bot 判断是否介入（水群短句、闲聊通常静默；学习/知识类问题可能自动回复）。活跃群慎用。

---

## Webhook 配置

启用 Webhook 后，语雀文档变更实时同步到本地：

1. 在 AstrBot 管理面板启用 `webhook_enabled`
2. 设置 `webhook_port`（默认 8766）
3. 在语雀知识库设置中添加 Webhook URL：
   ```
   http://your-server:8766/yuque/webhook
   ```
4. 选择触发事件：`publish`、`update`、`delete`

### 数据同步流程

```
语雀 Webhook → WebhookHandler
                   ↓
        ┌─────────┼─────────┐
        ↓         ↓         ↓
   本地 MD    SQLite    ChromaDB
   写入/删除  upsert   upsert
        ↓
   Git commit（可选）
```

---

## 使用流程

```
1. 管理员配置 yuque_token + embedding_api_key
   - 多团队部署时，额外配置 yuque_teams
2. 管理员: /sync members [team_id]
3. 管理员: /sync 或 /sync <team_id>
4. 用户: /bind <用户名>
5. 用户: /profile refresh（生成画像）
6. 用户: 直接对话或使用指令
```

---

## 数据存储

```
data/plugin_data/astrbot_plugin_yuque/
├── bindings.json           # 用户绑定关系
├── user_profiles/          # 用户画像（含偏好）
├── yuque-members.json      # 团队成员缓存
├── yuque_repos.json        # 知识库列表
├── subscriptions.json      # 订阅关系
├── ask_box.json            # 提问箱
├── token_logs.json         # Token 消耗日志
├── search_logs.json        # 搜索日志
├── yuque_docs/             # Markdown 文档
│   ├── .yuque-id-to-path.json
│   ├── <默认团队知识库名>/*.md
│   └── <team_id>/<知识库名>/*.md
├── doc_index.db            # SQLite 元数据索引
└── chroma_db/              # RAG 向量数据库
```

---

## AI 工具

NovaBot 为 AI 提供以下工具（自动调用）：

| 工具 | 功能 |
|------|------|
| `list_teams` | 列出可用语雀团队及描述 |
| `search_knowledge_base` | Evidence-first 混合检索，支持 team/repository/path/author/time 范围 |
| `grep_local_docs` | 关键词精确匹配，支持 team/repository/path/author/time 范围 |
| `read_doc` | 读取文档正文（支持 offset/limit 分页） |
| `get_doc_details` | 获取单篇文档完整元数据与可选正文 |
| `parse_yuque_url` | 解析语雀链接并读取内容与元数据 |
| `search_docs` | 按作者/知识库/标题筛选（含链接、时间、字数） |
| `list_authors` | 列出所有作者 |
| `list_knowledge_bases` | 列出知识库，可按 team 过滤 |
| `list_repo_docs` | 知识库目录结构（含 slug、path），可按 team 过滤 |
| `doc_stats` | 文档统计 |
| `generate_knowledge_card` | 生成知识卡片 |
| `set_preference` | 设置用户偏好 |
| `partner_recommend` | 推荐学习伙伴 |
| `learning_path` | 生成学习路径 |
| `weekly_report` | 生成本周周报 |
| `knowledge_gap` | 分析学习缺口 |
| `subscribe` | 订阅知识库/作者 |
| `unsubscribe` | 取消订阅 |
| `profile_view` | 查看用户画像 |

知识事实问答默认采用 Evidence-first：Agent 应优先调用 `list_teams` / `list_knowledge_bases` 确定范围，再使用 `search_knowledge_base`、`grep_local_docs`、`read_doc` 或 `get_doc_details` 取得证据后回答。普通聊天、用户设置、画像刷新、订阅、学习路径、协作网络等社区能力不强制要求知识库证据。

---

## 项目结构

```
astrbot_plugin_yuque/
├── main.py              # 主入口
├── novabot/
│   ├── rag.py           # RAG 检索
│   ├── agent.py         # Agent 对话处理
│   ├── yuque_client.py  # 语雀 API
│   ├── sync.py          # 文档同步
│   ├── storage.py       # 数据存储
│   ├── doc_index.py     # SQLite 索引
│   ├── doc_utils.py     # 文档 URL/正文读取工具函数
│   ├── profile.py       # 用户画像
│   ├── partner.py       # 伙伴推荐
│   ├── knowledge_card.py# 知识卡片
│   ├── learning_path.py # 学习路径
│   ├── knowledge_gap.py # 学习缺口
│   ├── knowledge_base.py# 知识库层
│   ├── ask_box.py       # 知识问答
│   ├── subscribe.py     # 订阅管理
│   ├── push_notifier.py # 智能推送
│   ├── webhook.py       # Webhook 处理
│   ├── git_ops.py       # Git 操作
│   └── tools/           # LLM 工具
├── metadata.yaml
└── requirements.txt
```

---

## 版本历史

| 版本 | 变更 |
|------|------|
| v0.29.2 | 新增 `get_doc_details`；增强 `search_docs`/`read_doc`/`parse_yuque_url`/`list_repo_docs` 文档信息获取 |
| v0.29.1 | 修复计费/同步一致性，新增 `/weekly raw` 导出按周原始 CSV（管理员） |
| v0.29.0 | 群聊发送者识别、群聊幻觉修复、Token 限额可配置 |
| v0.28.0 | Agent 智能化升级：指令改用 Agent 处理、智能推荐权重优化 |
| v0.27.0 | 成员轨迹、协作网络、主动关心机制 |
| v0.26.0 | 长期记忆系统、学习进度追踪、问题档案 |
| v0.25.0 | 自然语言交互工具（伙伴推荐、学习路径、周报、学习缺口、订阅、画像） |
| v0.24.0 | 更新感知（/kb updates）、语雀链接解析 |
| v0.23.0 | 联网搜索配置、TOC 结构优化 |
| v0.22.0 | 知识库层（/kb 列表、概览、范围检索） |
| v0.21.0 | 知识问答重构（实名、多回答、点赞） |
| v0.20.0 | `/weekly` LLM 增强（主题洞察、热点话题、下周建议） |
| v0.19.0 | 人格管理系统（称呼、语气、风格定制） |
| v0.18.0 | 消息路由（唤醒词、@触发）、学习缺口分析 |
| v0.17.0 | 自然语言交互（Agent 对话） |
| v0.16.0 | 知识问答 |
| v0.14.0 | 周报生成、Token 监控 |
| v0.13.0 | 伙伴推荐、知识卡片、学习路径、智能推送 |
| v0.11.0 | Webhook 实时同步、Git 版本控制 |
| v0.10.0 | 元数据索引（SQLite） |
| v0.8.0 | LLM 工具调用、Agentic RAG |
| v0.2.0 | 内置同步 + RAG |

详见 [CHANGELOG.md](./CHANGELOG.md)。
