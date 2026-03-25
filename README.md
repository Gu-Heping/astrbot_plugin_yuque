# astrbot_plugin_yuque (NovaBot)

> NOVA 社团智能助手 · 以语雀知识库为核心的 AstrBot Plugin

---

## 项目状态

**当前阶段**：`idle`（等待任务）

**状态机版本**：v1.0

**下次检查**：每 30 分钟自动唤醒

**状态流转**：`idle` → `planning` → `developing` → `testing` → `auditing` → `committing` → `pushing` → `refactoring` → `optimizing` → `idle`

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
3. **复用 yuque2git**：不重复造轮子
4. **gno 检索**：已验证可行
5. **绑定用姓名**：模糊匹配，简单优先

---

## 设计检查清单

### 功能设计

- [x] 检索功能：来源追溯 + 延伸问题 → **使用 gno MCP**
- [x] 问答功能：对话式 + 追问 → gno MCP + LLM
- [x] 绑定功能：姓名模糊匹配 + 冲突检测 → 已设计
- [x] 用户画像：兴趣/水平/成长轨迹 → 维度确定方式已设计
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

- [x] gno 调用方式确定 → **使用 gno MCP**
- [x] 绑定数据结构 → 含冲突检测逻辑
- [x] 用户画像结构 → 含维度确定方式
- [x] 成员匹配算法 → 加权评分 + 场景调整
- [x] 错误处理 → 待开发时实现

---

## 当前进度

| 任务 | 状态 | 备注 |
|------|:----:|------|
| 仓库创建 | ✅ | git@github.com:Gu-Heping/astrbot_plugin_yuque.git |
| 设计文档 | ✅ | NovaBot设计/ |
| 功能细化 | ✅ | docs/功能设计细化.md |
| metadata.yaml | 🔲 | 待更新 |
| _conf_schema.json | 🔲 | 待创建 |
| main.py | 🔲 | 不开发 |
| gno 索引 | 🔄 | 后台运行中 |

---

## 部署指南

### 1. 安装插件

```bash
# 在 AstrBot 的 data/plugins 目录下
cd AstrBot/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_yuque.git
```

### 2. 配置 gno MCP

NovaBot 使用 gno 作为检索引擎，需要在 AstrBot 中配置 gno MCP：

1. 打开 AstrBot WebUI（默认 http://localhost:6185）
2. 进入 **AI 配置** → **MCP**
3. 点击 **新增服务器**
4. 填写配置：

```json
{
  "command": "/home/admin/.bun/bin/gno",
  "args": ["mcp", "serve"],
  "env": {
    "NODE_LLAMA_CPP_GPU": "false"
  }
}
```

5. 点击 **测试**，确认成功
6. 点击 **保存**

### 3. 配置插件

在 WebUI 的插件页面，找到 NovaBot，配置：

| 配置项 | 说明 |
|--------|------|
| yuque_token | 语雀团队 Token（可选，用于访问团队知识库） |
| yuque_base_url | 语雀 API 地址，默认 `https://nova.yuque.com/api/v2` |
| docs_path | 语雀文档本地路径，默认 `/home/admin/yuque-docs` |

### 4. 使用指令

| 指令 | 说明 |
|------|------|
| `/bind <Token>` | 绑定语雀账号 |
| `/unbind` | 解除绑定 |
| `/profile` | 查看用户画像 |
| `/novabot` | 帮助信息 |

### 5. 直接提问

绑定后，直接在对话中提问即可，NovaBot 会自动从语雀知识库检索答案。

---

## 文档索引

| 文档 | 路径 | 用途 |
|------|------|------|
| 设计概览 | `NovaBot设计/00-概览.md` | 功能分层、核心理念 |
| 数据架构 | `NovaBot设计/01-数据架构.md` | 存储、复用 yuque2git |
| 知识引擎 | `NovaBot设计/02-知识引擎.md` | 检索、知识卡片 |
| 个人层 | `NovaBot设计/03-个人层.md` | 绑定、画像、推荐 |
| 社团层 | `NovaBot设计/04-社团层.md` | 周报、趋势 |
| 技术实现 | `NovaBot设计/05-技术实现.md` | Plugin 代码设计 |
| 开发路线 | `NovaBot设计/06-开发路线.md` | 阶段、里程碑 |
| 功能细化 | `docs/功能设计细化.md` | 人性化功能详解 |
| AstrBot 手册 | `../vault/项目/多模态Agent系统探索/AstrBot开发指导手册-Plugin-Skills-MCP.md` | 开发参考 |

---

## 每次唤醒检查项

**流程定义**：见 `DEVFLOW.md`

**状态文件**：`state.json`

### 执行步骤

```
1. 读取 state.json，获取当前阶段和任务
2. 根据阶段执行动作（参考 DEVFLOW.md）
3. 更新 state.json，记录进度
4. 如有阻塞或问题，主动提出
```

---

## 状态文件说明

| 字段 | 说明 |
|------|------|
| `task_queue` | 待做任务，按优先级排序 |
| `completed` | 已完成任务 |
| `blocked` | 卡住的任务 |
| `questions` | 待讨论的问题 |
| `ideas` | 新想法 |
| `next_focus` | 下次重点 |

---

## 参考案例

### 类似产品

- OpenClaw Peacebot：个人 AI 助手
- yuque-sync-platform：语雀同步 + RAG
- astrbot_plugin_self_learning：自主学习、社交关系

### 设计参考

- Dieter Rams：少即是多
- Rob Pike：清晰胜于 clever
- Bret Victor：工具应该让你看见自己在做什么

---

## 版本历史

| 日期 | 变更 |
|------|------|
| 2026-03-26 | 创建仓库、设计文档 |
| 2026-03-26 | 加入人性化设计层 |
| 2026-03-26 | 设置 cron 定时唤醒 |

---

*本文档为项目锚点，每次唤醒后先读取此文件*