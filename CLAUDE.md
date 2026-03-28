# CLAUDE.md - NovaBot 项目开发指南

> 本文档为 AI 助手提供项目上下文，确保代码风格一致、遵循最佳实践。

---

## 1. 项目概述

**NovaBot** 是一个 AstrBot 插件，为 NOVA 社团提供智能助手服务，以语雀知识库为核心。

### 1.1 核心功能

- 语雀知识库同步（Markdown + YAML frontmatter）
- RAG 语义检索（LangChain + ChromaDB）
- 用户画像生成（基于关键词提取）
- 团队成员管理

### 1.2 项目结构

```
astrbot_plugin_yuque/
├── main.py              # 主入口，包含所有核心逻辑
├── novabot/
│   ├── __init__.py
│   └── rag.py           # RAG 检索引擎
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置项定义
├── requirements.txt     # Python 依赖
├── docs/                # 开发文档
│   ├── 功能设计细化.md
│   ├── AstrBot开发快速参考卡片.md
│   ├── AstrBot开发实战示例.md
│   └── AstrBot开发指导手册-Plugin-Skills-MCP.md
└── plans/               # 开发计划
```

### 1.3 数据目录

所有持久化数据存放在 `data/nova/` 目录：

```
data/nova/
├── bindings.json           # 平台-语雀绑定关系
├── user_profiles/          # 用户画像
│   └── {yuque_id}.json
├── yuque-members.json      # 团队成员缓存
├── yuque_docs/             # 同步的 Markdown 文档
├── yuque_repos/            # 知识库列表缓存
├── sync_state.json         # 同步状态
└── chroma_db/              # RAG 向量库
```

---

## 2. AstrBot 开发规范

### 2.1 扩展方式选型

```
需要处理消息事件？        → Plugin
需要访问 AstrBot API？    → Plugin
是任务说明书/知识？       → Skill
是外部工具服务？          → MCP
```

### 2.2 Plugin 核心结构

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger

@register("plugin_name", "Author", "描述", "version")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.command("cmd")
    async def cmd(self, event: AstrMessageEvent, arg: str = ""):
        """指令说明"""
        yield event.plain_result("响应")
```

### 2.3 常用装饰器

```python
# 指令
@filter.command("hello")
@filter.command("add")  # /add 1 2
@filter.command_group("math")
@filter.permission_type(filter.PermissionType.ADMIN)

# 事件监听
@filter.event_message_type(filter.EventMessageType.ALL)
@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
@filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)

# 事件钩子
@filter.on_llm_request()
async def on_llm_req(self, event, req):
    req.system_prompt += " 额外提示"

@filter.on_llm_response()
async def on_llm_resp(self, event, resp):
    logger.info(f"LLM 响应: {resp.completion_text}")
```

### 2.4 消息操作

```python
# 获取信息
event.message_str          # 纯文本消息
event.get_sender_name()    # 发送者名称
event.get_sender_id()      # 发送者 ID
event.get_group_id()       # 群 ID（私聊为空）
event.unified_msg_origin   # 会话标识

# 发送消息
yield event.plain_result("文本")
yield event.image_result("path/to/image.jpg")

# 消息链
import astrbot.api.message_components as Comp
chain = [
    Comp.At(qq=event.get_sender_id()),
    Comp.Plain("你好"),
    Comp.Image.fromURL("https://...")
]
yield event.chain_result(chain)

# 主动消息
from astrbot.api.event import MessageChain
umo = event.unified_msg_origin
chain = MessageChain().message("主动消息")
await self.context.send_message(umo, chain)
```

### 2.5 LLM 调用

```python
# 获取当前 Provider
prov = self.context.get_using_provider(umo=event.unified_msg_origin)

# 调用 LLM
resp = await prov.text_chat(
    prompt="你好",
    context=[],
    system_prompt="你是助手"
)
print(resp.completion_text)

# 获取指定 Provider
prov = self.context.get_provider_by_id("provider_id")
```

### 2.6 函数工具（FunctionTool）

```python
from astrbot.api import FunctionTool
from dataclasses import dataclass, field

@dataclass
class MyTool(FunctionTool):
    name: str = "tool_name"
    description: str = "工具描述"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "param": {"type": "string", "description": "参数描述"}
        },
        "required": ["param"]
    })

    async def run(self, event, param: str):
        return f"结果: {param}"

# 注册
self.context.add_llm_tools(MyTool())
```

### 2.7 会话控制（多轮对话）

```python
from astrbot.core.utils.session_waiter import session_waiter, SessionController

@filter.command("game")
async def game(self, event: AstrMessageEvent):
    yield event.plain_result("开始游戏！")

    @session_waiter(timeout=60)
    async def waiter(controller: SessionController, event: AstrMessageEvent):
        msg = event.message_str

        if msg == "退出":
            await event.send(event.plain_result("已退出"))
            controller.stop()
            return

        await event.send(event.plain_result("继续..."))
        controller.keep(timeout=60, reset_timeout=True)

    try:
        await waiter(event)
    except TimeoutError:
        yield event.plain_result("超时！")
```

### 2.8 配置管理

**_conf_schema.json**:

```json
{
  "api_key": {
    "description": "API 密钥",
    "type": "string",
    "hint": "输入你的 Key"
  },
  "count": {
    "description": "数量",
    "type": "int",
    "default": 10
  },
  "enabled": {
    "description": "启用",
    "type": "bool",
    "default": false
  }
}
```

**读取配置**:

```python
def __init__(self, context: Context, config: AstrBotConfig):
    super().__init__(context)
    self.config = config
    api_key = self.config.get("api_key", "")
```

### 2.9 平台适配矩阵

| 平台 | At | Plain | Image | Record | 主动消息 |
|------|:--:|:-----:|:-----:|:------:|:--------:|
| QQ (aiocqhttp) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Telegram | ✅ | ✅ | ✅ | ✅ | ✅ |
| 飞书 | ✅ | ✅ | ✅ | ❌ | ✅ |
| 企业微信 | ❌ | ✅ | ✅ | ✅ | ❌ |
| 钉钉 | ❌ | ✅ | ✅ | ❌ | ❌ |
| Discord | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 3. 语雀同步参考仓库

> **重要**：当项目中的语雀功能出现 bug 时，参考以下两个成熟仓库的实现。

### 3.1 yuque2git

- **仓库**: https://github.com/Gu-Heping/yuque2git
- **功能**: 语雀 → 本地 Markdown + Git，Webhook 驱动
- **核心文件**: `scripts/sync_to_files.py`, `scripts/webhook_server.py`

**关键实现要点**:

```python
# 1. 限流与重试
YUQUE_SYNC_CONCURRENCY = 3          # 并发数
YUQUE_SYNC_REQUEST_DELAY = 0.25     # 请求间隔
YUQUE_SYNC_MAX_RETRIES = 4          # 最大重试

SEMAPHORE = asyncio.Semaphore(YUQUE_SYNC_CONCURRENCY)

# 2. 带重试的请求
async def _request_with_retry(client, method, url):
    for attempt in range(MAX_RETRIES):
        try:
            r = await client.request(method, url)
            if r.status_code == 429:  # Rate limit
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(wait)
                continue
            if 500 <= r.status_code < 600:  # Server error
                await asyncio.sleep(2 ** attempt)
                continue
            return r
        except (httpx.RequestError, httpx.ConnectTimeout):
            await asyncio.sleep(2 ** attempt)
    raise last_exc

# 3. 文档文件名：标题优先，无标题用 slug
def _doc_basename(title: Optional[str], slug: str) -> str:
    return _slug_safe(title or slug) or "untitled"

# 4. 时间处理：UTC 转本地可读时间
def _normalize_ts_local(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    local = dt.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.strftime("%Y-%m-%d %H:%M:%S")

# 5. 作者名获取优先级
def _author_name_from_detail(detail: dict) -> str:
    for key in ("last_editor", "creator", "user"):
        obj = detail.get(key)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("login")
            if name:
                return name
    return ""

# 6. 团队成员同步（分页）
async def get_group_members_page(group_id: int, page: int):
    path = f"/groups/{group_id}/statistics/members?page={page}"
    r = await self._get(path)
    if r.status_code == 404:
        return []  # 个人账号非团队
    return r.json().get("data", {}).get("members", [])
```

**Markdown 输出格式**:

```markdown
---
id: 123456
title: 文档标题
slug: doc-slug
created_at: 2026-03-26 10:30:00
updated_at: 2026-03-26 15:45:00
author: 作者名
book_name: 知识库名
description: 文档描述
---

| 作者 | 创建时间 | 更新时间 |
|------|----------|----------|
| 作者名 | 2026-03-26 10:30:00 | 2026-03-26 15:45:00 |

文档正文...
```

### 3.2 YuqueSyncPlatform

- **仓库**: https://github.com/Gu-Heping/YuqueSyncPlatform
- **功能**: 语雀知识库同步与 RAG 平台
- **核心文件**: `app/services/yuque_client.py`, `app/services/sync_service.py`

**YuqueClient 完整实现**:

```python
class YuqueClient:
    def __init__(self):
        self.base_url = settings.YUQUE_BASE_URL
        self.headers = {
            "X-Auth-Token": settings.YUQUE_TOKEN,
            "User-Agent": "YuqueSyncPlatform/1.0",
            "Content-Type": "application/json"
        }
        self.client = httpx.AsyncClient(headers=self.headers, timeout=30.0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.ConnectTimeout))
    )
    async def _get(self, endpoint: str, params: dict = None):
        url = f"{self.base_url}{endpoint}"
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_user_info(self) -> dict:
        data = await self._get("/user")
        return data.get("data", {})

    async def get_user_repos(self, user_id: int) -> list:
        data = await self._get(f"/users/{user_id}/repos")
        return data.get("data", [])

    async def get_group_repos(self, group_id: int) -> list:
        data = await self._get(f"/groups/{group_id}/repos")
        return data.get("data", [])

    async def get_repo_toc(self, repo_id: int) -> list:
        data = await self._get(f"/repos/{repo_id}/toc")
        return data.get("data", [])

    async def get_doc_detail(self, repo_id: int, slug: str) -> dict:
        data = await self._get(f"/repos/{repo_id}/docs/{slug}")
        return data.get("data", {})

    async def get_repo_detail(self, repo_id: int) -> dict:
        data = await self._get(f"/repos/{repo_id}")
        return data.get("data", {})
```

**同步服务关键逻辑**:

```python
class SyncService:
    def __init__(self):
        self.client = YuqueClient()
        self.semaphore = asyncio.Semaphore(5)  # 限流

    async def sync_all(self):
        # 1. 获取用户信息
        user_data = await self.client.get_user_info()
        current_user = await self._upsert_user(user_data)

        # 2. 同步团队成员
        await self.sync_team_members(current_user.yuque_id)

        # 3. 遍历知识库
        repos_data = await self.client.get_user_repos(current_user.yuque_id)
        for repo_data in repos_data:
            await self.sync_repo(repo_data)

    async def sync_team_members(self, group_id: int):
        page = 1
        while True:
            members = await self.client.get_group_members_page(group_id, page)
            if not members:
                break
            # 处理成员...
            page += 1
```

### 3.3 语雀 API 端点汇总

```
GET /user                              # 当前用户信息
GET /users/{id}/repos                  # 用户知识库列表
GET /groups/{id}/repos                 # 团队知识库列表
GET /repos/{id}                        # 知识库详情
GET /repos/{id}/toc                    # 知识库目录
GET /repos/{id}/docs                   # 知识库文档列表
GET /repos/{namespace}/docs            # 按命名空间获取文档列表
GET /repos/{namespace}/docs/{slug}     # 文档详情（含正文）
GET /groups/{id}/statistics/members    # 团队成员（分页）
```

### 3.4 语雀 API 关键字段说明

**团队成员 API 返回结构** (`GET /groups/{id}/users`):

```json
{
  "data": [{
    "user_id": 22463641,           // 用户 ID（用于匹配文档创建者）
    "user": {
      "id": 22463641,              // 同 user_id
      "login": "heping-qcbue",     // 登录名
      "name": "谷和平"              // 显示名
    }
  }]
}
```

**文档详情 API 返回结构** (`GET /repos/{namespace}/docs/{slug}`):

```json
{
  "data": {
    "id": 261881563,
    "title": "文档标题",
    "slug": "doc-slug",
    "user_id": 22463641,           // 创建者 ID（重要！用于匹配作者）
    "creator_id": null,            // 通常为 null，不可用！
    "last_editor_id": 22463641,    // 最后编辑者 ID
    "creator": {                   // 创建者信息（嵌套对象）
      "id": 22463641,
      "name": "谷和平",
      "login": "heping-qcbue"
    },
    "user": {                      // 同 creator
      "id": 22463641,
      "name": "谷和平"
    }
  }
}
```

**关键发现**：
- `creator_id` 字段通常为 `null`，不能用于匹配作者
- `user_id` 才是创建者 ID，用于文档作者匹配
- 团队成员的 `user_id` 与文档的 `user_id` 对应

**本地存储格式** (Markdown frontmatter):

```yaml
---
id: 261881563
title: 文档标题
slug: doc-slug
creator_id: 22463641    # 存储的是 user_id
author: 谷和平           # 从团队成员解析的真实姓名
book_name: 知识库名
---
```

---

## 4. 人性化设计原则

> 来源: `docs/功能设计细化.md`

### 4.1 核心原则

| 原则 | 说明 |
|------|------|
| **陪伴感** | 不是工具，是伙伴 |
| **成长可视化** | 让进步看得见 |
| **情绪感知** | 识别用户状态，调整语气 |
| **社交连接** | 连接人与人，不只是人与AI |
| **游戏化** | 学习像游戏一样有趣 |

### 4.2 对话风格对比

```
❌ 传统：
"搜索到 3 篇文档：..."

✅ 人性化：
"这个我帮你找到了！我看了几篇相关的文章..."
```

### 4.3 功能优先级

**Phase 1 (P0-P1) - 当前阶段**:
- 文档检索 + 来源追溯
- 智能问答 + 延伸问题
- `/bind`, `/profile` 指令
- 伙伴推荐（基础）

**Phase 2 (P1-P2)**:
- 知识卡片、学习路径推荐
- 成长时间线、社交连接
- 情绪识别、游戏化元素

**Phase 3 (P2-P3)**:
- 情绪感知 + 语气调整
- 压力检测、关系图谱、成就系统

### 4.4 情绪识别实现（关键词匹配 MVP）

```python
EMOTION_KEYWORDS = {
    "negative": {
        "累": 0.8, "烦": 0.7, "难": 0.6, "不想": 0.7, "放弃": 0.9,
        "压力": 0.8, "焦虑": 0.9, "崩溃": 1.0, "抑郁": 1.0,
    },
    "positive": {
        "开心": 0.8, "兴奋": 0.9, "终于": 0.7, "搞定": 0.8,
        "成功": 0.7, "进步": 0.6, "理解了": 0.7, "学会了": 0.8
    }
}

def detect_emotion(text: str) -> dict:
    score = {"positive": 0, "negative": 0}
    for emotion, keywords in EMOTION_KEYWORDS.items():
        for kw, weight in keywords.items():
            if kw in text:
                score[emotion] += weight

    total = score["positive"] + score["negative"]
    if total == 0:
        return {"emotion": "neutral", "confidence": 0.5}

    return {
        "emotion": "negative" if score["negative"] > score["positive"] else "positive",
        "confidence": max(score.values()) / total
    }
```

---

## 5. 开发规范

### 5.1 代码规范

```python
# 1. 使用官方 logger，不用 logging 模块
from astrbot.api import logger
logger.info("信息")
logger.error("错误", exc_info=True)

# 2. 使用异步库 (httpx/aiohttp)，不用同步库
# ✅ 正确
async with httpx.AsyncClient() as client:
    resp = await client.get(url)

# ❌ 错误
import requests
resp = requests.get(url)

# 3. yield 返回结果，不是 return
# ✅ 正确
yield event.plain_result("Hello")

# ❌ 错误
return event.plain_result("Hello")

# 4. 错误处理要完善
try:
    result = await operation()
except Exception as e:
    logger.error(f"错误: {e}", exc_info=True)
    yield event.plain_result("出错了，请稍后重试")
```

### 5.2 文件命名规范

```
main.py              # 必需，入口文件
metadata.yaml        # 必需，元数据
_conf_schema.json    # 配置 Schema
requirements.txt     # Python 依赖
```

### 5.3 版本规范

```yaml
# metadata.yaml
version: v1.0.0      # 语义化版本
astrbot_version: ">=4.16,<5"  # AstrBot 版本要求
```

### 5.4 发布前检查清单

- [ ] 代码用 `ruff format .` 格式化
- [ ] 代码用 `ruff check .` 检查
- [ ] requirements.txt 有依赖
- [ ] 有错误处理
- [ ] 有日志输出
- [ ] README 更新

---

## 6. 常见问题排查

### 6.1 分层排查

```
1. 安装/加载层：插件未出现在列表
   └── 检查 main.py 和 metadata.yaml 是否存在

2. 配置层：配置未生效
   └── 检查 _conf_schema.json 格式

3. 触发层：指令无响应
   └── 加日志确认是否触发

4. 执行层：执行出错
   └── 查看控制台完整错误栈

5. 返回层：无返回
   └── 确认使用 yield 而非 return
```

### 6.2 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| 插件不加载 | 文件缺失 | 检查 main.py, metadata.yaml |
| 指令不触发 | 装饰器语法 | 检查 @filter.command |
| 依赖找不到 | 未安装 | 检查 requirements.txt |
| 热重载失败 | 代码错误 | 查看控制台错误 |

---

## 7. 重要链接

| 资源 | 链接 |
|------|------|
| AstrBot 官方文档 | https://docs.astrbot.app |
| AstrBot 主仓库 | https://github.com/AstrBotDevs/AstrBot |
| 插件模板 | https://github.com/Soulter/helloworld |
| 插件市场 | https://plugins.astrbot.app |
| yuque2git | https://github.com/Gu-Heping/yuque2git |
| YuqueSyncPlatform | https://github.com/Gu-Heping/YuqueSyncPlatform |

---

## 8. 开发经验教训

### 8.1 多存储层一致性

NovaBot 使用三层存储架构，必须保持数据同步：

```
Markdown 文件 (frontmatter) → SQLite 索引 → ChromaDB 向量
```

**关键原则**：

1. **新增字段时同步更新**：
   - 表结构（SQLite）
   - 写入逻辑（sync.py, webhook.py）
   - 索引逻辑（doc_index.py, rag.py）
   - 查询逻辑（learning_path.py 等）

2. **全量同步 vs 增量更新**：
   - 全量同步：`sync.py` 写入所有数据
   - 增量更新：`webhook.py` 必须与全量同步逻辑一致
   - **检查点**：修改 sync.py 后，检查 webhook.py 是否需要同步修改

3. **数据库迁移兼容**：
   ```python
   # 添加新字段时，考虑已有数据库
   try:
       conn.execute("ALTER TABLE docs ADD COLUMN new_field TEXT")
   except sqlite3.OperationalError:
       pass  # 字段已存在
   ```

### 8.2 None 值处理

**问题**：API 可能返回 None，ChromaDB 不接受 None 作为 metadata 值。

**解决方案**：

```python
# ✅ 正确：条件添加
creator_id = doc.get("creator_id")
if creator_id is not None:
    documents[-1].metadata["creator_id"] = creator_id

# ✅ 正确：日志追踪
if creator_id is None:
    logger.warning(f"文档缺少 creator_id: {title}")

# ❌ 错误：直接添加可能导致 ChromaDB 报错
metadata["creator_id"] = doc.get("creator_id")  # 可能为 None
```

### 8.3 语雀 API 字段说明

| 字段 | 说明 | 备注 |
|------|------|------|
| `user_id` | 创建者 ID（整数） | **推荐使用** |
| `creator_id` | 通常为 None | **不可用** |
| `last_editor_id` | 最后编辑者 ID | 用于推送消息 |
| `creator`/`user` | 嵌套对象 | 包含 name, login |

**正确获取创建者 ID**：

```python
creator_id = detail.get("user_id") or (detail.get("creator") or {}).get("id")
```

### 8.4 代码审计检查清单

修改语雀相关功能后，检查以下模块：

| 模块 | 文件 | 检查点 |
|------|------|--------|
| 全量同步 | sync.py | frontmatter 写入、doc_metadata 收集 |
| 增量更新 | webhook.py | _write_markdown_file, _update_doc_index, _update_rag |
| SQLite | doc_index.py | 表结构、add_doc, add_docs |
| RAG | rag.py | index_docs, index_from_sync, upsert_doc |
| 查询 | storage.py, learning_path.py | 字段读取、过滤逻辑 |

### 8.5 SQLite 数据库迁移

**问题**：添加新列时，索引创建可能先于 ALTER TABLE 执行，导致 "no such column" 错误。

**正确做法**：使用 PRAGMA 检查列是否存在。

```python
def _init_db(self):
    conn.execute("CREATE TABLE IF NOT EXISTS docs (...)")

    # ✅ 正确：先检查列是否存在
    columns = conn.execute("PRAGMA table_info(docs)").fetchall()
    column_names = [col[1] for col in columns]
    if "new_column" not in column_names:
        conn.execute("ALTER TABLE docs ADD COLUMN new_column INTEGER")

    # 然后再创建索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_new_column ON docs(new_column)")

    # ❌ 错误：直接创建索引可能失败
    conn.execute("CREATE INDEX IF NOT EXISTS idx_new_column ON docs(new_column)")
    try:
        conn.execute("ALTER TABLE docs ADD COLUMN new_column INTEGER")
    except:
        pass  # 太晚了，索引已失败
```

### 8.6 流式输出限制

**问题**：AstrBot 流式输出模式下，`LLMResponse.usage` 为 None，无法统计 chat token。

**调查过程**：
```python
@filter.on_llm_response()
async def on_llm_response(self, event, resp):
    # 日志显示：
    # resp.usage: None
    # raw_completion.usage: None
    # is_chunk: False
```

**结论**：这是 AstrBot 框架的限制，不是插件 bug。

**解决方案选项**：
1. **接受限制**：流式模式下 chat token 无法统计
2. **禁用流式输出**：在 AstrBot 配置中关闭流式，可获取 token 但影响用户体验
3. **等待框架更新**：AstrBot 可能未来支持流式输出的 token 统计

**其他功能的 token 统计**（非流式，正常工作）：
- embedding：RAG 向量化
- learning_path：学习路径生成
- profile：用户画像生成
- push：智能推送判断
| RAG | rag.py | index_docs, index_from_sync, upsert_doc |
| 查询 | storage.py, learning_path.py | 字段读取、过滤逻辑 |

---

*本文档基于 AstrBot 官方文档和项目开发经验整理，持续更新中。*