# NovaBot Webhook 集成计划

## Context

**目标**：集成语雀 Webhook，实现文档变更的实时同步，避免定时全量同步。

**当前数据流**：
```
语雀 API → /sync 全量同步 → 本地 Markdown → SQLite (DocIndex) → ChromaDB (RAG)
```

**期望数据流**：
```
语雀 Webhook → 增量更新 → 本地 Markdown + SQLite + ChromaDB（三层数据同步）
```

**参考实现**：yuque2git 的 `scripts/webhook_server.py`

---

## 语雀 Webhook 事件类型

| 事件 | 触发时机 | 处理方式 |
|------|----------|----------|
| `publish` | 文档发布 | 获取详情 → 写入 MD → 更新索引 |
| `update` | 文档更新 | 获取详情 → 覆盖 MD → 更新索引 |
| `delete` | 文档删除 | 删除 MD → 删除索引记录 |

**Webhook Payload 结构**（简化）：
```json
{
  "data": {
    "action_type": "publish|update|delete",
    "id": 123456,
    "slug": "doc-slug",
    "title": "文档标题",
    "book": {"id": 789, "slug": "repo-slug", "name": "知识库名"}
  }
}
```

---

## 需要新增的功能

### 1. RAG 引擎增量更新方法

**文件**: `novabot/rag.py`

```python
def upsert_doc(self, doc: dict) -> bool:
    """更新或插入单个文档到向量库"""
    # 1. 先删除旧向量（按 metadata.id 过滤）
    # 2. 添加新向量
    pass

def delete_doc(self, yuque_id: int) -> bool:
    """删除指定文档的向量"""
    # 使用 collection.delete(where={"id": str(yuque_id)})
    pass
```

### 2. DocIndex 增量更新方法

**文件**: `novabot/doc_index.py`

```python
def delete_doc(self, yuque_id: int) -> bool:
    """删除指定文档的索引记录"""
    conn = self._get_conn()
    conn.execute("DELETE FROM docs WHERE yuque_id = ?", (yuque_id,))
    conn.commit()
```

### 3. Webhook 处理器

**新文件**: `novabot/webhook.py`

```python
class WebhookHandler:
    """语雀 Webhook 处理器"""

    def __init__(self, plugin: "NovaBotPlugin"):
        self.plugin = plugin
        self.client = plugin._get_client()

    async def handle(self, payload: dict) -> dict:
        """处理 Webhook 事件"""
        action = payload["data"]["action_type"]

        if action in ("publish", "update"):
            return await self._handle_doc_change(payload)
        elif action == "delete":
            return await self._handle_doc_delete(payload)

        return {"status": "ignored"}

    async def _handle_doc_change(self, payload: dict) -> dict:
        """处理文档发布/更新"""
        # 1. 获取文档详情
        # 2. 写入 Markdown
        # 3. 更新 SQLite
        # 4. 更新 ChromaDB
        pass

    async def _handle_doc_delete(self, payload: dict) -> dict:
        """处理文档删除"""
        yuque_id = payload["data"]["id"]
        # 1. 删除 Markdown
        # 2. 删除 SQLite 记录
        # 3. 删除 ChromaDB 向量
        pass
```

### 4. HTTP 服务端点

**方案**：使用 aiohttp 内嵌异步 HTTP 服务器（参考 astrbot_plugin_github_webhook）

**核心原理**：
- AstrBot 插件继承自 `Star` 类
- 在 `initialize()` 中启动 aiohttp 服务器（不是 `__init__`，确保 AstrBot 完全初始化）
- 在 `terminate()` 中优雅关闭服务器
- 使用 `web.AppRunner` + `web.TCPSite` 管理服务器生命周期

**实现模式**：

```python
from aiohttp import web
from astrbot.api.star import Star

class NovaBotPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 1. 创建 aiohttp Application
        self.app = web.Application()
        # 2. 注册 Webhook 路由
        self.app.router.add_post("/yuque/webhook", self.handle_webhook)
        # 3. 保存服务器引用
        self.runner = None
        self.site = None

    async def initialize(self):
        """✅ 在 initialize() 中启动服务器（确保 AstrBot 已完全初始化）"""
        if not self.config.get("webhook_enabled", False):
            return

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        port = self.config.get("webhook_port", 8766)
        self.site = web.TCPSite(self.runner, "0.0.0.0", port)
        await self.site.start()
        logger.info(f"NovaBot Webhook 服务已启动: http://0.0.0.0:{port}/yuque/webhook")

    async def handle_webhook(self, request: web.Request):
        """处理语雀 Webhook 请求"""
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        # 调用 WebhookHandler 处理
        result = await self.webhook_handler.handle(payload)
        return web.Response(status=200, text="OK")

    async def terminate(self):
        """✅ 在 terminate() 中关闭服务器"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("NovaBot Webhook 服务已关闭")
```

**关键要点**：
- ❌ 不要在 `__init__` 中启动服务器
- ❌ 不要使用 `threading.Thread` + uvicorn
- ✅ 使用 `async def initialize()` 启动
- ✅ 使用 `async def terminate()` 关闭

---

## 文件修改清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `novabot/rag.py` | 修改 | 新增 `upsert_doc()`, `delete_doc()` |
| `novabot/doc_index.py` | 修改 | 新增 `delete_doc()` |
| `novabot/webhook.py` | 新建 | WebhookHandler 类 |
| `novabot/git_ops.py` | 新建 | Git 操作封装 |
| `novabot/__init__.py` | 修改 | 导出 WebhookHandler, GitOps |
| `main.py` | 修改 | 内嵌 aiohttp Webhook 服务 |
| `_conf_schema.json` | 修改 | 新增 webhook、git 配置项 |
| `requirements.txt` | 修改 | 新增 `aiohttp>=3.9.0` |

---

## 配置项

```json
{
  "webhook_enabled": {
    "description": "启用 Webhook 服务",
    "type": "bool",
    "default": false
  },
  "webhook_port": {
    "description": "Webhook 服务端口",
    "type": "int",
    "default": 8766
  },
  "git_enabled": {
    "description": "启用 Git 版本控制（保留变更历史）",
    "type": "bool",
    "default": true
  },
  "git_auto_push": {
    "description": "自动推送到远程仓库（需配置 remote）",
    "type": "bool",
    "default": false
  }
}
```

---

## 实现步骤

### Phase 1: 数据层增量更新（P0）

**任务 1.1**: `novabot/doc_index.py` 新增 `delete_doc()`
```python
def delete_doc(self, yuque_id: int) -> bool:
    """删除指定文档的索引记录"""
    conn = self._get_conn()
    cursor = conn.execute("DELETE FROM docs WHERE yuque_id = ?", (yuque_id,))
    conn.commit()
    return cursor.rowcount > 0
```

**任务 1.2**: `novabot/rag.py` 新增增量更新方法
```python
def upsert_doc(self, doc: dict) -> bool:
    """更新或插入单个文档"""
    yuque_id = str(doc.get("id", ""))

    # 先删除旧向量
    if yuque_id:
        self.delete_doc(int(yuque_id))

    # 添加新向量
    return self.index_docs([doc]) > 0

def delete_doc(self, yuque_id: int) -> bool:
    """删除指定文档的向量"""
    try:
        collection = self.vectorstore._collection
        collection.delete(where={"id": str(yuque_id)})
        logger.info(f"[RAG] 删除向量: yuque_id={yuque_id}")
        return True
    except Exception as e:
        logger.error(f"[RAG] 删除向量失败: {e}")
        return False
```

**验证**: 单元测试
```bash
pytest tests/test_rag.py::test_upsert_delete -v
pytest tests/test_doc_index.py::test_delete_doc -v
```

---

### Phase 2: Git 操作封装（P1）

**任务 2.1**: 创建 `novabot/git_ops.py`

**关键实现**:
- `ensure_git()`: 初始化仓库（如不存在）
- `add_commit(files, message)`: 添加并提交，返回 commit hash
- `get_diff(commit, file)`: 获取 diff（可选，用于未来智能推送）

**依赖**: 需要系统安装 Git

**验证**:
```bash
pytest tests/test_git_ops.py -v
```

---

### Phase 3: Webhook 处理逻辑（P0）

**任务 3.1**: 创建 `novabot/webhook.py`

**WebhookHandler 核心方法**:
- `handle(payload)`: 路由到具体处理方法
- `_handle_doc_change(payload)`: publish/update 事件
- `_handle_doc_delete(payload)`: delete 事件
- `_get_doc_detail(repo_id, slug)`: 调用 API 获取详情
- `_write_markdown(doc, path)`: 写入 MD 文件
- `_update_all_indexes(doc)`: 同步更新 SQLite + ChromaDB

**数据一致性保证**:
1. 先写 MD 文件
2. 再更新 SQLite
3. 最后更新 ChromaDB
4. 最后 Git commit（失败不影响数据）

**验证**: 模拟 webhook 请求测试

---

### Phase 4: HTTP 服务内嵌（P1）

**任务 4.1**: 在 `main.py` 中内嵌 aiohttp 服务器

**技术要点**：
- 使用 `aiohttp.web.Application` + `AppRunner` + `TCPSite`
- 在 `initialize()` 异步启动，在 `terminate()` 优雅关闭
- 不使用 threading，完美融入 AstrBot 事件循环

**验证**:
```bash
curl -X POST http://localhost:8766/yuque/webhook \
  -H "Content-Type: application/json" \
  -d '{"data":{"action_type":"publish","id":123456,...}}'
```

---

### Phase 5: 配置与文档（P2）

1. 更新 `_conf_schema.json`
2. 更新 README 说明 Webhook 配置步骤
3. 更新 CHANGELOG

---

## 待确认的技术细节

### 1. ChromaDB 向量删除机制

**问题**: ChromaDB 的 `collection.delete(where={...})` 是否能正确删除？

**方案**: 测试验证
```python
# 测试代码
collection.add(ids=["1"], documents=["test"], metadatas=[{"id": "123"}])
collection.delete(where={"id": "123"})
assert collection.count() == 0
```

**备选方案**: 如果 where 删除不稳定，维护额外的 ID->ChromaID 映射表

---

### 2. aiohttp 与 AstrBot 事件循环共存

**问题**: aiohttp 服务器与 AstrBot 事件循环是否冲突？

**方案**: 使用 `AppRunner.setup()` + `TCPSite.start()` 方式

```python
self.runner = web.AppRunner(self.app)
await self.runner.setup()
self.site = web.TCPSite(self.runner, "0.0.0.0", port)
await self.site.start()
```

这是 AstrBot 官方推荐方式，与事件循环完美兼容。

**验证**: 已通过 astrbot_plugin_github_webhook 插件验证

---

### 3. Git 初始化时机

**问题**: 何时初始化 Git 仓库？

**方案**:
- 方案 A: 首次 webhook 时自动初始化
- 方案 B: `/sync` 时检查并初始化
- 方案 C: 添加 `/git init` 命令手动初始化

**建议**: 方案 B，全量同步时初始化更可靠

---

## 数据同步流程图

```
┌─────────────────────────────────────────────────────────────┐
│                      语雀 Webhook                            │
│                  (publish/update/delete)                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   WebhookHandler                             │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ publish      │  │ update       │  │ delete       │      │
│  │ 获取详情     │  │ 获取详情     │  │ 直接处理     │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼─────────────────┼─────────────────┼────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────┐
│                   三层数据同步                               │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ 本地 MD     │  │ SQLite      │  │ ChromaDB    │         │
│  │ 写入/删除   │  │ upsert/del  │  │ upsert/del  │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

---

## 验证方案

### 单元测试

```bash
# 测试增量更新
pytest tests/test_webhook.py -v

# 测试数据层
pytest tests/test_rag.py::test_upsert_doc -v
pytest tests/test_doc_index.py::test_delete_doc -v
```

### 集成测试

1. 配置语雀 Webhook URL: `http://your-server:8766/yuque/webhook`
2. 在语雀中创建/更新/删除文档
3. 检查日志确认事件处理
4. 验证 MD 文件、SQLite、ChromaDB 数据一致

---

## Git 集成

**目标**：每次文档变更自动 commit，保留完整历史记录（类似 yuque2git）。

### 实现要点

```python
# novabot/git_ops.py
import subprocess
from pathlib import Path

class GitOps:
    """Git 操作封装"""

    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir

    def ensure_git(self) -> bool:
        """确保 Git 仓库已初始化"""
        if not (self.repo_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=self.repo_dir, capture_output=True)
        return True

    def add_commit(self, files: list[str], message: str) -> Optional[str]:
        """添加文件并提交，返回 commit hash"""
        try:
            # git add
            subprocess.run(["git", "add", *files], cwd=self.repo_dir, capture_output=True)

            # git commit
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_dir,
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                # 获取 commit hash
                hash_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True
                )
                return hash_result.stdout.strip()[:7]
        except Exception as e:
            logger.warning(f"Git commit 失败: {e}")
        return None

    def get_diff(self, commit: str, file_path: str) -> str:
        """获取指定 commit 与当前文件的 diff"""
        # git diff <commit> -- <file>
        pass
```

### Webhook 处理集成

```python
async def _handle_doc_change(self, payload: dict) -> dict:
    # ... 获取文档详情、写入 MD ...

    # Git commit
    git = GitOps(self.plugin.storage.data_dir / "yuque_docs")
    git.ensure_git()
    commit_hash = git.add_commit([rel_path], f"yuque: {action} {title}")

    # ... 更新索引 ...
```

### 配置项

```json
{
  "git_enabled": {
    "description": "启用 Git 版本控制",
    "type": "bool",
    "default": true
  },
  "git_auto_push": {
    "description": "自动推送到远程仓库",
    "type": "bool",
    "default": false
  }
}
```

---

## 依赖

```txt
# requirements.txt
aiohttp>=3.9.0
```

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| ChromaDB 向量 ID 管理 | 使用 yuque_id 作为 metadata.id，支持按条件删除 |
| Webhook 丢失 | 保留 `/sync` 全量同步作为兜底 |
| 并发冲突 | 使用文件锁或 SQLite WAL 模式 |
| 服务崩溃 | 添加健康检查和自动重启 |