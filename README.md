# astrbot_plugin_yuque (NovaBot)

> NOVA 社团智能助手 · 以语雀知识库为核心的 AstrBot Plugin

---

## 项目状态

**当前阶段**：`idle`（等待任务）

**状态机版本**：v1.0

**下次检查**：每 30 分钟自动唤醒

---

## ⚠️ 部署架构（重要）

**AstrBot 与 OpenClaw 部署在不同服务器，完全独立。**

```
OpenClaw 服务器 (当前机器)
├── yuque2git 服务
├── gno 检索
└── OpenClaw
    └── 本项目开发环境

AstrBot 服务器 (另一台机器)
└── AstrBot Docker 容器
    └── NovaBot 插件 (完全自包含)
        ├── 内置语雀同步
        ├── 内置 RAG 检索
        └── 不依赖外部文件/服务
```

**设计原则**：插件必须完全自包含，不能依赖：
- ❌ 宿主机文件路径
- ❌ 外部 MCP 服务
- ❌ 与 OpenClaw 服务器的网络连接

---

## 技术方案

### 内置模块

| 模块 | 方案 | 说明 |
|------|------|------|
| **语雀同步** | 插件内置 | 全量拉取 + Webhook 增量更新 |
| **文档存储** | 插件 data 目录 | `/AstrBot/data/plugins/astrbot_plugin_yuque/docs/` |
| **RAG 检索** | LangChain + ChromaDB | 纯 Python，无需 Node.js |

### 为什么不用 gno

1. **gno 需要 Node.js** — AstrBot 容器是 Python 环境
2. **gno 依赖外部文档** — 容器内无 yuque-docs
3. **gno MCP 是 stdio 协议** — 需要本地进程，不适合容器化

替代方案：**LangChain + ChromaDB**
- 纯 Python，pip 安装即可
- 向量存储轻量，适合插件规模
- 支持增量索引

---

## 设计原则

### 核心理念

1. **陪伴感 > 工具感**
   - 不是冷冰冰的搜索工具，是学习伙伴
   - 回答要有温度，有追问，有延伸

2. **成长可视化**
   - 让进步看得见
   - 时间线、里程碑、周报

3. **激发元认知**
   - 不只给答案，帮用户理解"为什么"
   - 来源追溯，让用户能追溯

4. **连接人与人**
   - 不只是人与 AI
   - 伙伴推荐、共同兴趣

5. **活人感**
   - 真实、有温度、有人味
   - 不是官方账号，是成员视角

---

## 技术约束

1. **纯 Plugin**：不用 Skill
2. **JSON 存储**：不用数据库
3. **自包含同步**：内置语雀 API 调用
4. **Python RAG**：LangChain + ChromaDB
5. **绑定用姓名**：模糊匹配，简单优先

---

## 设计检查清单

### 功能设计

- [x] 检索功能：来源追溯 + 延伸问题 → **内置 RAG**
- [x] 问答功能：对话式 + 追问 → RAG + LLM
- [x] 绑定功能：姓名模糊匹配 + 冲突检测 → 已实现
- [x] 用户画像：兴趣/水平/成长轨迹 → 已实现
- [x] 伙伴推荐：共同兴趣 + 水平相近 → 匹配算法已设计
- [x] 学习追踪：时间线 + 里程碑 → 阅读时长估算已设计
- [x] 周报生成：热门文档 + 活跃作者 → 数据来源已确定
- [x] 情绪感知：识别状态 + 调整语气 → 机制已设计

### 人性化设计

- [x] 学习陪伴：回答后追问"还想了解什么？"
- [x] 成长可视化：`/timeline` 查看历程
- [x] 社交温度：介绍伙伴时有人情味
- [x] 时机感知：深夜不推送
- [x] 游戏化：经验值 + 成就（P2）

### 技术设计

- [x] 语雀同步 → **内置同步模块**
- [x] RAG 检索 → **LangChain + ChromaDB**
- [x] 绑定数据结构 → 含冲突检测逻辑
- [x] 用户画像结构 → 含维度确定方式
- [x] 成员匹配算法 → 加权评分 + 场景调整
- [x] 错误处理 → 待开发时实现

---

## 当前进度

| 功能 | 状态 | 说明 |
|------|:----:|------|
| /bind 绑定 | ✅ | 支持用户名/login/模糊匹配 |
| 用户画像 | ✅ | 基于文档关键词生成 |
| 语雀同步 | ✅ | Markdown + YAML frontmatter |
| RAG 检索 | ✅ | LangChain + ChromaDB + 元数据 |
| /sync members | ✅ | 单独同步团队成员 |
| Webhook 增量 | 🔲 | 待开发 |

---

## 部署指南

### 1. 安装插件

```bash
# 在 AstrBot 的 data/plugins 目录下
cd AstrBot/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_yuque.git
```

### 2. 配置插件

在 AstrBot WebUI 的插件页面配置：

| 配置项 | 说明 |
|--------|------|
| `yuque_token` | 语雀团队 Token（用于同步知识库和用户绑定） |
| `yuque_base_url` | 语雀 API 地址，默认 `https://nova.yuque.com/api/v2` |
| `embedding_api_key` | Embedding API Key（OpenAI 或兼容服务） |
| `embedding_base_url` | Embedding API 地址（可选，默认 OpenAI） |
| `embedding_model` | Embedding 模型，默认 `text-embedding-3-small` |

### 3. 使用指令

| 指令 | 说明 |
|------|------|
| `/bind <用户名>` | 绑定语雀账号（支持模糊匹配） |
| `/unbind` | 解除绑定 |
| `/profile` | 查看用户画像 |
| `/sync` | 同步知识库 + 团队成员 |
| `/sync status` | 查看同步状态 |
| `/sync members` | 仅同步团队成员 |
| `/rag status` | 查看 RAG 状态 |
| `/rag search <关键词>` | 搜索文档 |
| `/novabot` | 帮助信息 |

### 4. 绑定流程

```
管理员: /sync members  → 同步团队成员到缓存

用户:   /bind 谷和平      → 精确匹配 name
     或 /bind heping-qcbue → 精确匹配 login  
     或 /bind 和平        → 模糊匹配
```

---

## 依赖 (requirements.txt)

```
httpx>=0.24.0
langchain>=0.1.0
langchain-community>=0.0.10
langchain-openai>=0.0.5
chromadb>=0.4.0
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.4.0 | 2026-03-26 | 同步输出 Markdown + frontmatter，支持元数据检索 |
| v0.3.0 | 2026-03-26 | 添加 `/sync members`，支持 login 和模糊匹配绑定 |
| v0.2.0 | 2026-03-26 | 自包含架构：内置同步 + RAG，废弃 gno MCP |
| v0.1.0 | 2026-03-26 | 初始版本：/bind、用户画像、gno MCP |

---

*本文档为项目锚点，每次唤醒后先读取此文件*