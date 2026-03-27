# NovaBot

> NOVA 社团智能助手 · 以语雀知识库为核心的 AstrBot Plugin

完全自包含，内置语雀同步 + RAG 检索 + 元数据索引，不依赖外部服务。

---

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_yuque.git
```

## 配置

| 配置项 | 说明 |
|--------|------|
| `yuque_token` | 语雀团队 Token |
| `yuque_base_url` | 语雀 API 地址，默认 `https://nova.yuque.com/api/v2` |
| `embedding_api_key` | Embedding API Key |
| `embedding_base_url` | Embedding API 地址（可选） |
| `embedding_model` | Embedding 模型，默认 `text-embedding-3-small` |
| `webhook_enabled` | 启用 Webhook 服务（实时同步语雀文档变更） |
| `webhook_port` | Webhook 服务端口，默认 `8766` |
| `git_enabled` | 启用 Git 版本控制（保留文档变更历史） |
| `git_auto_push` | 自动推送到远程仓库（需先配置 git remote） |
| `push_enabled` | 启用智能推送（文档变更时推送通知给订阅者） |
| `push_min_diff_chars` | 最小变更字符数（低于此值跳过推送） |
| `push_max_content_len` | 推送内容最大长度 |

## 指令

| 指令 | 说明 |
|------|------|
| `/bind <用户名>` | 绑定语雀账号 |
| `/unbind` | 解除绑定 |
| `/profile` | 查看用户画像 |
| `/profile refresh` | 刷新用户画像 |
| `/profile assess <领域>` | 评估某领域掌握程度 |
| `/sync` | 同步知识库 |
| `/sync status` | 查看同步状态 |
| `/sync members` | 同步团队成员 |
| `/sync clean` | 清理孤儿目录 |
| `/rag search <关键词>` | RAG 搜索 |
| `/rag rebuild` | 重建索引 |
| `/partner [主题]` | 伙伴推荐 |
| `/path <领域>` | 学习路径推荐 |
| `/subscribe` | 查看订阅 |
| `/subscribe repo <知识库名>` | 订阅知识库更新 |
| `/subscribe author <作者名>` | 订阅作者更新 |
| `/subscribe all` | 订阅全部更新 |
| `/unsubscribe <ID>` | 取消订阅 |
| `/webhook` | Webhook 服务状态 |
| `/novabot` | 帮助信息 |

## Webhook 配置

启用 Webhook 后，语雀文档变更将实时同步到本地，无需手动 `/sync`。

### 配置步骤

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

### Git 版本控制

启用 `git_enabled` 后，每次文档变更自动提交到本地 Git 仓库，保留完整历史。

如需推送到远程仓库：
1. 进入 `data/nova/yuque_docs/` 目录
2. 执行 `git remote add origin <repo-url>`
3. 在配置中启用 `git_auto_push`

## 使用流程

```
1. 管理员配置 yuque_token
2. 管理员: /sync members
3. 用户: /bind <用户名>
4. 用户: /sync
```

---

## 数据存储

```
data/nova/
├── bindings.json           # 用户绑定关系
├── user_profiles/          # 用户画像
├── yuque-members.json      # 团队成员缓存
├── yuque_repos.json        # 知识库列表
├── subscriptions.json      # 订阅关系
├── last_push.json          # 推送记录（文档ID→commit）
├── yuque_docs/             # 同步的 Markdown 文档
│   ├── .yuque-id-to-path.json  # 文档 ID→路径 索引
│   └── <知识库名>/
│       ├── .toc.json       # 目录结构
│       └── *.md            # 文档文件
├── doc_index.db            # SQLite 元数据索引
└── chroma_db/              # RAG 向量数据库
```

**注意**：删除 `data/nova/` 目录可完全卸载数据。

---

## AI 工具

NovaBot 为 AI 提供以下工具（AI 可自动调用）：

### 搜索类

| 工具 | 功能 | 适用场景 |
|------|------|----------|
| `grep_local_docs` | 关键词精确搜索 | 查找特定代码、配置、名称 |
| `search_knowledge_base` | 语义搜索 | 概念性查询、模糊匹配 |
| `read_doc` | 读取完整文档 | grep 后深入了解 |

### 元数据类

| 工具 | 功能 | 适用场景 |
|------|------|----------|
| `search_docs` | 按作者/知识库/标题搜索 | 查看某人的所有文档 |
| `list_authors` | 列出所有作者 | 谁写的最多 |
| `doc_stats` | 文档统计 | 总文档数、总字数 |
| `list_knowledge_bases` | 列出知识库 | 了解有哪些知识库 |
| `list_repo_docs` | 列出知识库结构 | 了解知识库目录 |

### 推荐搜索流程

```
1. list_knowledge_bases → 看有哪些知识库
2. search_docs(author="谷和平") → 按元数据筛选
3. grep_local_docs(keyword="madoka", repo_filter="madoka") → 精确搜索
4. read_doc(path) → 读取完整内容
```

---

## 数据存储说明

### 双重存储

NovaBot 维护两套数据：

| 数据 | Markdown 文件 | SQLite 索引 |
|------|--------------|-------------|
| 内容 | ✅ 完整正文 | ❌ 不存储 |
| 元数据 | ✅ YAML frontmatter | ✅ 结构化索引 |
| 用途 | 人类阅读、版本控制 | 高效元数据查询 |

**同步时自动同步**：写入 Markdown → 提取 frontmatter → 更新 SQLite

### 时区

- 所有时间存储为 `Asia/Shanghai` 时区
- 格式：`YYYY-MM-DD HH:MM:SS`
- 来源：语雀 API 返回的 UTC 时间自动转换

---

## 项目结构

```
astrbot_plugin_yuque/
├── main.py              # 主入口（插件类 + 指令处理）
├── novabot/
│   ├── __init__.py
│   ├── rag.py           # RAG 检索引擎
│   ├── yuque_client.py  # 语雀 API 客户端
│   ├── sync.py          # 文档同步
│   ├── storage.py       # 数据存储
│   ├── doc_index.py     # SQLite 元数据索引
│   ├── profile.py       # 用户画像生成
│   ├── partner.py       # 伙伴推荐
│   ├── knowledge_card.py # 知识卡片生成
│   ├── learning_path.py # 学习路径推荐
│   ├── subscribe.py     # 订阅管理
│   ├── push_notifier.py # 智能推送
│   ├── webhook.py       # Webhook 处理器
│   ├── git_ops.py       # Git 操作封装
│   └── tools/           # LLM 工具
│       ├── search.py    # 搜索工具
│       └── metadata.py  # 元数据工具
├── metadata.yaml        # 插件元数据
└── requirements.txt     # Python 依赖
```

---

## 版本历史

| 版本 | 变更 |
|------|------|
| v0.13.0 | 伙伴推荐、知识卡片、学习路径、智能推送订阅 |
| v0.12.x | 安全修复、依赖兼容、架构改进 |
| v0.11.0 | Webhook 实时同步、Git 版本控制 |
| v0.10.0 | 元数据索引（SQLite）、按作者/知识库查询 |
| v0.9.x | grep 优化、read_doc 工具、孤儿文件清理 |
| v0.8.0 | LLM 工具调用、Agentic RAG |
| v0.7.0 | LLM 用户画像、主动触发 |
| v0.5.0 | 代码重构、后台同步 |
| v0.4.0 | Markdown + frontmatter 同步 |
| v0.2.0 | 内置同步 + RAG |
| v0.1.0 | 初始版本 |

详见 [CHANGELOG.md](./CHANGELOG.md)。