# AstrBot 开发指导手册：Plugin / Skill / MCP

> 版本：v1.0  
> 基于官方文档整理，适用于 AstrBot v4.12.0+  
> 整理时间：2026-03-25

---

## 1. 结论摘要

1. **Plugin 是核心扩展方式**：需要处理消息事件、调用 AstrBot API、写 Python 逻辑 → 首选 Plugin
2. **Skill 是任务说明书**：需要给 Agent 提供操作步骤、让模型按流程执行 → 用 Skill
3. **MCP 是外部工具接入**：已有独立服务、想复用现成工具、需要进程隔离 → 接 MCP
4. **先跑通最小样例**：不要一上来写复杂逻辑，先让 Hello World 跑起来
5. **热重载是调试核心**：修改代码后用 WebUI 重载，不需要重启 AstrBot
6. **日志在控制台看**：`from astrbot.api import logger` 是官方推荐日志接口
7. **配置用 _conf_schema.json**：让用户在 WebUI 可视化配置，不要让用户改代码
8. **依赖写 requirements.txt**：否则用户安装会报 Module Not Found
9. **数据存 data 目录**：不要存在插件目录，更新插件会丢失数据
10. **Skills 必须有 SKILL.md**：文件名必须大写，内容遵循 Anthropic 规范
11. **MCP 需要 uv 或 npm**：大多数 MCP 服务器用这两个启动
12. **沙盒环境保护安全**：Skills 含可执行代码时，用 Sandbox 模式隔离
13. **测试分层进行**：加载 → 配置 → 触发 → 执行 → 返回，逐层排查
14. **发布走插件市场**：plugins.astrbot.app 提交 Issue 即可
15. **ruff 格式化代码**：提交前必须格式化，这是社区规范

---

## 2. 官方资料与依据

### 2.1 核心文档

| 文档 | 地址 | 说明 |
|------|------|------|
| 插件开发指南（新） | https://docs.astrbot.app/dev/star/plugin-new.html | 入门必读 |
| 插件开发指南（旧） | https://docs.astrbot.app/dev/star/plugin.html | API 详细参考 |
| Skills 文档 | https://docs.astrbot.app/use/skills.html | Skill 使用说明 |
| MCP 文档 | https://docs.astrbot.app/use/mcp.html | MCP 接入配置 |
| 沙盒环境 | https://docs.astrbot.app/use/astrbot-agent-sandbox.html | Skills 执行环境 |
| 插件发布 | https://docs.astrbot.app/dev/star/plugin-publish.html | 发布到市场 |

### 2.2 关键仓库

| 仓库 | 地址 | 说明 |
|------|------|------|
| AstrBot 主仓库 | https://github.com/AstrBotDevs/AstrBot | 源码参考 |
| 插件模板 | https://github.com/Soulter/helloworld | 新插件从这里开始 |
| 插件市场 | https://plugins.astrbot.app | 发布和发现插件 |

### 2.3 Anthropic Skills 规范

- 官方规范：https://code.claude.com/docs/zh-CN/skills
- AstrBot 遵循此规范，SKILL.md 格式需兼容

---

## 3. AstrBot 扩展体系总览

### 3.1 三者定义

| 类型 | 定义 | 本质 |
|------|------|------|
| **Plugin** | Python 类，继承 `Star`，注册到 AstrBot 运行时 | **代码扩展** |
| **Skill** | 包含 SKILL.md 的文件夹，给 Agent 提供操作指南 | **任务说明书** |
| **MCP** | 独立进程，通过 MCP 协议暴露工具接口 | **外部服务** |

### 3.2 三者关系图

```
┌─────────────────────────────────────────────────────────────┐
│                      AstrBot 运行时                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │   Plugin    │    │   Skills    │    │     MCP     │     │
│  │  (进程内)    │    │ (执行环境)   │    │  (进程外)    │     │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘     │
│         │                  │                  │            │
│         ▼                  ▼                  ▼            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ AstrBot API │    │ Agent 上下文 │    │ 外部工具服务 │     │
│  │ 平台事件    │    │ 任务说明书   │    │ OCR/检索等  │     │
│  │ LLM 调用    │    │ 领域知识     │    │             │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 三者分工图

```
用户消息 → 平台适配器 → AstrBot Core
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Plugin          Skills           MCP
     (消息事件处理)    (任务知识)      (外部工具)
              │               │               │
              └───────┬───────┴───────┬───────┘
                      ▼               ▼
                   LLM 调用      工具执行
                      │               │
                      └───────┬───────┘
                              ▼
                         响应用户
```

- **Plugin**：直接访问 AstrBot API，处理消息事件，注册指令
- **Skill**：被 Agent 加载后，指导模型如何执行任务，提供领域知识
- **MCP**：提供的工具会被注册到 LLM 工具列表，进程外隔离

### 3.3 选型原则

#### 什么时候优先写 Plugin

- ✅ 需要处理平台消息事件（收到消息、发送消息）
- ✅ 需要与 AstrBot 运行时直接交互（获取配置、调用 Provider）
- ✅ 需要写本地 Python 逻辑
- ✅ 需要快速做一个消息驱动扩展
- ✅ 需要注册指令（如 `/hello`）

**典型场景**：群聊管理、消息转发、自定义指令、AI 增强功能

#### 什么时候优先做 Skill

- ✅ 需要做任务手册（告诉 Agent 怎么做事）
- ✅ 需要多步骤执行说明
- ✅ 想让模型按流程操作（如：先读文件、再处理、再写回）
- ✅ 需要把指令、脚本和资源打包
- ✅ 需要按需加载能力（节省 Token）

**典型场景**：文档处理流程、数据分析步骤、代码生成规范

#### 什么时候优先接 MCP

- ✅ 已有独立服务（OCR、搜索、数据库）
- ✅ 想做进程外工具（隔离故障）
- ✅ 想复用已有 MCP Server（awesome-mcp-servers 上很多）
- ✅ 需要跨项目共享工具

**典型场景**：接入外部 API、数据库操作、文件系统访问

#### 什么时候可以组合

**可以组合，但要谨慎**：

- Plugin + MCP：Plugin 处理消息，MCP 提供工具
- Skill + Plugin：Skill 指导流程，Plugin 提供底层能力

**警告**：组合增加复杂度。先问自己：能不能只用一种方式解决？

### 3.5 三者对比表

| 维度 | Plugin | Skills | MCP |
|------|--------|--------|-----|
| **最小扩展单元** | Python 类（main.py） | 文件夹 + SKILL.md | 独立服务进程 |
| **调用方式** | 装饰器注册，自动触发 | Agent 按需加载 | 通过协议调用 |
| **与主系统耦合** | 高（共享进程） | 低（执行环境隔离） | 无（独立进程） |
| **运行边界** | AstrBot 进程内 | Local/Sandbox | 完全独立 |
| **安全边界** | ⚠️ 进程内，需信任代码 | ✅ Sandbox 隔离 | ✅ 进程隔离 |
| **开发复杂度** | 中（需 Python） | 低（Markdown 为主） | 中高（需服务开发） |
| **调试难度** | 低（热重载） | 中（需执行环境） | 高（跨进程调试） |
| **适合的能力类型** | 平台事件、LLM 调用、会话管理 | 任务手册、领域知识 | 外部工具、数据服务 |
| **API 访问** | 完整 AstrBot API | 受限（通过 Tool） | 无（独立服务） |
| **依赖管理** | requirements.txt | 无（或沙盒预装） | 自行管理 |

### 3.6 选型判断流程图

```
开始
  │
  ├─ 需要处理平台消息事件？ ──是──→ Plugin
  │
  ├─ 需要访问 AstrBot API？ ──是──→ Plugin
  │
  ├─ 是任务说明书/领域知识？ ──是──→ Skills
  │
  ├─ 是外部工具服务？ ──是──→ MCP
  │
  └─ 需要组合？ ──是──→ 分析组合点
```

---

## 4. 从零开始的开发路线图

### 4.1 环境准备

```bash
# 1. 安装 uv（Python 包管理器）
pip install uv

# 2. 安装 AstrBot
uv tool install astrbot

# 3. 初始化
astrbot init

# 4. 启动
astrbot run
```

**Docker 方式（推荐生产环境）**：

```bash
git clone https://github.com/AstrBotDevs/AstrBot
cd AstrBot
docker compose up -d
```

### 4.2 稳步推进路线图

```
第 1 步：跑通最小插件
    ↓
第 2 步：学会看日志
    ↓
第 3 步：加配置项
    ↓
第 4 步：加外部依赖
    ↓
第 5 步：处理消息事件
    ↓
第 6 步：调用 LLM
    ↓
第 7 步：打包/发布
```

### 4.3 每个阶段的交付物

| 阶段 | 交付物 | 验证方式 |
|------|--------|----------|
| 1 | 能响应 `/hello` 指令 | 发消息收到回复 |
| 2 | 日志输出到控制台 | 能看到 `logger.info()` |
| 3 | WebUI 可配置参数 | 管理面板显示配置项 |
| 4 | 能 import 第三方库 | 不报 Module Not Found |
| 5 | 能处理特定消息 | 群聊/私聊触发不同逻辑 |
| 6 | 能调用 LLM 返回结果 | AI 回复正常 |
| 7 | 发布到插件市场 | 用户可一键安装 |

---

## 5. Plugin 开发详细指南

### 5.1 最小插件结构

```
astrbot_plugin_xxx/
├── main.py           # 必需：插件主程序
├── metadata.yaml     # 必需：插件元数据
└── requirements.txt  # 可选：依赖列表
```

### 5.2 文件说明

#### main.py

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("my_plugin", "Author", "描述", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("hello")
    async def hello(self, event: AstrMessageEvent):
        '''这是一个 hello 指令'''
        logger.info("触发 hello 指令!")
        yield event.plain_result("Hello!")
```

#### metadata.yaml

```yaml
name: my_plugin
display_name: My Plugin
desc: 插件描述
version: v1.0.0
author: YourName
repo: https://github.com/xxx/xxx
```

#### requirements.txt

```
aiohttp>=3.8.0
requests>=2.28.0
```

### 5.3 开发流程

#### Step 1：从模板创建

```bash
# 1. 打开 https://github.com/Soulter/helloworld
# 2. 点击 Use this template → Create new repository
# 3. 命名：astrbot_plugin_xxx（小写、无空格）
```

#### Step 2：Clone 到本地

```bash
git clone https://github.com/AstrBotDevs/AstrBot
mkdir -p AstrBot/data/plugins
cd AstrBot/data/plugins
git clone https://github.com/你的用户名/astrbot_plugin_xxx
```

#### Step 3：修改 metadata.yaml

```yaml
name: astrbot_plugin_xxx
display_name: XXX Plugin
desc: 这是一个示例插件
version: v1.0.0
author: 你的名字
repo: https://github.com/你的用户名/astrbot_plugin_xxx
```

#### Step 4：启动 AstrBot

```bash
cd AstrBot
astrbot run
# 或 Docker 方式
docker compose up -d
```

#### Step 5：测试插件

1. 打开 WebUI：http://localhost:6185
2. 进入 插件 页面
3. 确认插件已加载
4. 发送 `/hello` 测试

#### Step 6：热重载

修改代码后：
1. WebUI → 插件 → 找到你的插件
2. 点击 ... → 重载插件
3. 无需重启 AstrBot

### 5.4 常用装饰器

#### 指令相关

```python
# 基本指令
@filter.command("hello")

# 带参指令
@filter.command("add")
async def add(self, event: AstrMessageEvent, a: int, b: int):
    yield event.plain_result(f"结果是: {a + b}")

# 指令组
@filter.command_group("math")
def math(self): pass

@math.command("add")
async def math_add(self, event, a: int, b: int): ...

# 指令别名
@filter.command("help", alias={'帮助', 'helpme'})
```

#### 事件监听

```python
# 所有消息
@filter.event_message_type(filter.EventMessageType.ALL)

# 仅私聊
@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)

# 仅群聊
@filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)

# 仅管理员
@filter.permission_type(filter.PermissionType.ADMIN)
```

#### 事件钩子

```python
# LLM 请求前
@filter.on_llm_request()
async def on_llm_req(self, event, req):
    req.system_prompt += " 你是一个有用的助手"

# LLM 响应后
@filter.on_llm_response()
async def on_llm_resp(self, event, resp):
    logger.info(f"LLM 回复: {resp.completion_text}")

# 发送消息前
@filter.on_decorating_result()
async def on_decorating(self, event):
    result = event.get_result()
    result.chain.append(Comp.Plain("!"))
```

### 5.5 消息操作

#### 获取信息

```python
event.message_str          # 纯文本消息
event.get_sender_name()    # 发送者名称
event.get_sender_id()      # 发送者 ID
event.get_group_id()       # 群 ID（私聊为空）
event.unified_msg_origin   # 会话标识
```

#### 发送消息

```python
# 纯文本
yield event.plain_result("文本")

# 图片
yield event.image_result("path/to/image.jpg")
yield event.image_result("https://example.com/img.jpg")

# 消息链
import astrbot.api.message_components as Comp
chain = [
    Comp.At(qq=event.get_sender_id()),
    Comp.Plain("你好"),
    Comp.Image.fromURL("https://...")
]
yield event.chain_result(chain)
```

#### 主动消息

```python
from astrbot.api.event import MessageChain

umo = event.unified_msg_origin  # 保存会话标识
chain = MessageChain().message("主动消息")
await self.context.send_message(umo, chain)
```

### 5.6 插件配置

#### 创建 _conf_schema.json

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

#### 读取配置

```python
from astrbot.api import AstrBotConfig

class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        api_key = self.config.get("api_key", "")
```

### 5.7 调用 LLM

```python
@filter.command("ask")
async def ask(self, event: AstrMessageEvent):
    prov = self.context.get_using_provider(umo=event.unified_msg_origin)
    if prov:
        resp = await prov.text_chat(
            prompt="Hi!",
            context=[],
            system_prompt="You are a helpful assistant."
        )
        yield event.plain_result(resp.completion_text)
```

### 5.8 函数工具

#### 类形式（推荐）

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

#### 装饰器形式

```python
@filter.llm_tool(name="get_weather")
async def get_weather(self, event, location: str):
    '''获取天气信息
    
    Args:
        location(string): 地点
    '''
    return f"{location} 天气晴朗"
```

### 5.9 会话控制

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

### 5.10 发布插件

1. 推送代码到 GitHub
2. 访问 https://plugins.astrbot.app
3. 点击右下角 + 按钮
4. 填写信息，点击"提交到 GITHUB"
5. 确认 Issue 内容，提交

---

## 6. Skill 开发详细指南

### 6.1 Skill 最小结构

```
my-skill/
├── SKILL.md          # 必需：技能定义
├── scripts/          # 可选：脚本
├── templates/        # 可选：模板
└── resources/        # 可选：资源文件
```

### 6.2 SKILL.md 格式

```markdown
---
name: my-skill
description: 清晰描述做什么以及何时使用
---

# My Skill

## 何时使用
- 场景 1
- 场景 2
- 场景 3

## 前置假设
- 已安装 Python 3
- 某些 pip 包已安装

## 操作步骤

### 第 1 步：准备
1. 具体操作
2. 具体操作

### 第 2 步：执行
1. 具体操作
2. 具体操作

### 第 3 步：验证
1. 检查结果
2. 确认成功

## 示例
- 示例 1
- 示例 2
```

### 6.3 打包上传

```bash
# 打包为 zip
zip -r my-skill.zip my-skill/

# 上传到 AstrBot
# WebUI → 插件 → Skills → 上传
```

### 6.4 上传要求

1. 必须是 `.zip` 压缩包
2. 解压后是**单个文件夹**
3. 文件夹名字作为 Skill 标识（用英文）
4. 文件夹内必须有 `SKILL.md`（大写）

### 6.5 执行环境

| 环境 | 说明 | 风险 |
|------|------|------|
| **Local** | 在 AstrBot 运行环境中执行 | ⚠️ 高风险，可执行任意代码 |
| **Sandbox** | 在隔离沙盒中执行 | ✅ 推荐，安全隔离 |

**配置方式**：配置 → 使用电脑能力 → 执行环境

### 6.6 测试 Skill

#### 测试识别

1. 上传后，检查 WebUI 是否显示 Skill 名称
2. 确认没有报错

#### 测试执行

1. 在对话中触发 Skill 描述的场景
2. 观察 Agent 是否按 Skill 指导操作
3. 检查控制台日志

### 6.7 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| Skill 未被加载 | 文件名不对 | 确保是 `SKILL.md`（大写） |
| Skill 未被使用 | 描述不清晰 | 优化 description 字段 |
| 执行失败 | 环境问题 | 检查 Sandbox 是否启动 |
| 权限被拒绝 | 非管理员用户 | Local 模式仅管理员可用 |

### 6.8 Skill vs Plugin 选择

| 场景 | 推荐 |
|------|------|
| 需要处理消息事件 | Plugin |
| 需要注册指令 | Plugin |
| 需要访问 AstrBot API | Plugin |
| 需要给 Agent 提供操作指南 | Skill |
| 需要节省 Token（按需加载） | Skill |
| 需要隔离执行 | Skill + Sandbox |

---

## 7. MCP 开发/接入详细指南

### 7.1 MCP 定位

MCP（Model Context Protocol）是开放标准协议，用于在 LLM 和数据源之间建立连接。

**本质**：将函数工具抽离为独立服务，AstrBot 通过 MCP 协议远程调用。

### 7.2 接入方式

#### 自己开发 MCP Server

需要遵循 MCP 协议规范，使用 Python/TypeScript 等语言实现。

#### 接入已有 MCP Server

从以下资源获取：
- https://github.com/modelcontextprotocol/servers
- https://github.com/punkpeye/awesome-mcp-servers
- https://mcp.so

### 7.3 安装依赖

```bash
# uv（Python MCP 服务器）
pip install uv

# npm（Node.js MCP 服务器）
# 参考 https://nodejs.org/en/download
```

**Docker 环境**：

```bash
docker exec -it astrbot /bin/bash
apt update && apt install curl -y
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash
. "$HOME/.nvm/nvm.sh"
nvm install 22
```

### 7.4 配置 MCP 服务器

#### uv 方式

```json
{
  "command": "uv",
  "args": [
    "tool",
    "run",
    "arxiv-mcp-server",
    "--storage-path", "data/arxiv"
  ]
}
```

#### npm 方式

```json
{
  "command": "npx",
  "args": ["-y", "@scope/server-name", "/path/to/dir"]
}
```

#### 带环境变量

```json
{
  "command": "env",
  "args": [
    "API_KEY=xxx",
    "uv", "tool", "run", "xxx-mcp-server"
  ]
}
```

### 7.5 在 WebUI 配置

1. 打开 WebUI
2. 进入 AI 配置 → MCP
3. 点击"新增服务器"
4. 填写名称和配置 JSON
5. 点击"测试"
6. 测试成功后点击"保存"

### 7.6 验证 MCP 接入

#### 连接测试

1. 点击 WebUI 的"测试"按钮
2. 查看控制台是否有错误

#### 工具发现测试

1. 配置 LLM Provider
2. 开始对话
3. 检查是否能看到 MCP 工具被调用

### 7.7 调试 MCP

#### 问题定位层次

```
1. AstrBot 能否启动 MCP 进程？
   └── 检查控制台日志
   
2. MCP Server 是否返回工具列表？
   └── 检查 MCP Server 日志
   
3. 工具参数是否正确？
   └── 检查 LLM 调用日志
   
4. 工具是否返回正确结果？
   └── 检查工具返回值
```

#### 常见错误

| 错误 | 原因 | 解决 |
|------|------|------|
| uv/npm not found | 未安装依赖 | 安装 uv 或 npm |
| Connection refused | MCP 未启动 | 检查 command/args 配置 |
| Tool not found | MCP 未返回工具 | 检查 MCP Server 日志 |
| Permission denied | 权限问题 | 检查文件/目录权限 |

---

## 8. 分层排查手册

### 8.1 安装/加载层

**症状**：插件未出现在列表中

**排查步骤**：

1. 检查目录结构
   ```bash
   ls AstrBot/data/plugins/astrbot_plugin_xxx/
   # 必须有 main.py 和 metadata.yaml
   ```

2. 检查文件名
   - main.py（不是 Main.py 或其他）
   - metadata.yaml（不是 metadata.yml）

3. 查看控制台错误
   - 启动时是否有报错
   - 是否有语法错误

4. 检查 metadata.yaml 格式
   ```bash
   cat metadata.yaml
   # 确保字段完整
   ```

### 8.2 配置层

**症状**：配置未生效

**排查步骤**：

1. 检查 _conf_schema.json 位置
   ```bash
   ls AstrBot/data/plugins/astrbot_plugin_xxx/_conf_schema.json
   ```

2. 检查 JSON 格式
   ```bash
   python -c "import json; json.load(open('_conf_schema.json'))"
   ```

3. 检查配置文件
   ```bash
   cat AstrBot/data/config/astrbot_plugin_xxx_config.json
   ```

4. 检查代码读取
   ```python
   def __init__(self, context: Context, config: AstrBotConfig):
       super().__init__(context)
       self.config = config
       logger.info(f"配置: {self.config}")
   ```

### 8.3 触发层

**症状**：指令无响应

**排查步骤**：

1. 确认指令注册
   ```python
   @filter.command("hello")
   async def hello(self, event: AstrMessageEvent):
       logger.info("触发!")  # 加日志确认
       yield event.plain_result("Hello!")
   ```

2. 检查消息平台
   - 是否正确连接
   - 是否有权限

3. 检查指令格式
   - 发送的是 `/hello` 不是 `hello`
   - 指令名是否正确

4. 检查过滤器组合
   ```python
   # 多个过滤器是 AND 关系
   @filter.command("hello")
   @filter.permission_type(filter.PermissionType.ADMIN)
   async def hello(self, event):
       # 只有管理员才能触发
   ```

### 8.4 执行层

**症状**：执行出错

**排查步骤**：

1. 查看完整错误栈
   - 控制台输出
   - WebUI 错误提示

2. 加调试日志
   ```python
   try:
       result = await some_operation()
       logger.info(f"结果: {result}")
   except Exception as e:
       logger.error(f"错误: {e}", exc_info=True)
   ```

3. 检查依赖
   ```bash
   # 是否安装了 requirements.txt 中的依赖
   pip list | grep aiohttp
   ```

4. 检查异步操作
   ```python
   # 错误：同步调用异步函数
   result = some_async_function()
   
   # 正确：使用 await
   result = await some_async_function()
   ```

### 8.5 返回层

**症状**：无返回或返回异常

**排查步骤**：

1. 确认 yield 使用
   ```python
   # 错误：直接 return
   return event.plain_result("Hello")
   
   # 正确：使用 yield
   yield event.plain_result("Hello")
   ```

2. 检查消息链
   ```python
   chain = [
       Comp.Plain("文本"),
       Comp.Image.fromURL("...")
   ]
   logger.info(f"消息链: {chain}")
   yield event.chain_result(chain)
   ```

3. 检查平台支持
   - 不是所有平台都支持所有消息类型
   - 参考"平台适配矩阵"

### 8.6 Agent 选择层（Skills/MCP）

**症状**：工具未被调用

**排查步骤**：

1. 确认工具已注册
   - Skills：WebUI 是否显示
   - MCP：测试是否通过

2. 检查工具描述
   - description 是否清晰
   - 参数定义是否正确

3. 检查模型能力
   - 是否支持工具调用
   - 是否启用工具调用

4. 检查提示词
   - 是否引导模型使用工具

---

## 9. 工程规范建议

### 9.1 目录规范

```
astrbot_plugin_xxx/
├── main.py              # 主程序
├── metadata.yaml        # 元数据
├── requirements.txt     # 依赖
├── _conf_schema.json    # 配置 Schema
├── logo.png             # Logo（可选，256x256）
├── README.md            # 说明文档
├── tools/               # 工具类（可选）
│   ├── __init__.py
│   └── helper.py
└── tests/               # 测试（可选）
    └── test_main.py
```

### 9.2 配置规范

```json
{
  "字段名": {
    "description": "中文描述",
    "type": "string|int|float|bool|object|list",
    "default": "默认值",
    "hint": "提示信息",
    "obvious_hint": false
  }
}
```

### 9.3 日志规范

```python
from astrbot.api import logger

# 使用官方 logger，不用 logging 模块
logger.info("信息")
logger.warning("警告")
logger.error("错误", exc_info=True)  # 打印堆栈
logger.debug("调试信息")
```

### 9.4 依赖规范

```txt
# requirements.txt
# 指定版本范围
aiohttp>=3.8.0,<4.0.0
httpx>=0.24.0

# 不要用 requests（同步库）
# 用 aiohttp 或 httpx（异步库）
```

### 9.5 版本规范

```yaml
# metadata.yaml
version: v1.0.0  # 语义化版本

# 声明 AstrBot 版本要求
astrbot_version: ">=4.16,<5"
```

### 9.6 发布规范

1. 代码格式化：`ruff format .`
2. 代码检查：`ruff check .`
3. 更新 README.md
4. 更新 CHANGELOG.md（可选）
5. 打 tag：`git tag v1.0.0`
6. 推送：`git push --tags`

---

## 10. 稳步推进建议

### 10.1 第一阶段：最小可运行

- ✅ 从模板创建插件
- ✅ 修改 metadata.yaml
- ✅ 跑通 `/hello` 指令
- ✅ 学会热重载

**不要做**：
- ❌ 加复杂配置
- ❌ 接外部服务
- ❌ 写大量代码

### 10.2 第二阶段：核心功能

- ✅ 加配置项
- ✅ 加依赖
- ✅ 处理消息事件
- ✅ 调用 LLM

**不要做**：
- ❌ 多个指令组
- ❌ 复杂状态管理
- ❌ 主动消息推送

### 10.3 第三阶段：完善功能

- ✅ 错误处理
- ✅ 日志完善
- ✅ 测试覆盖
- ✅ 文档完善

**不要做**：
- ❌ 过度工程
- ❌ 过早优化

### 10.4 发布准备

- ✅ ruff 格式化
- ✅ README 完善
- ✅ 测试安装流程
- ✅ 提交到插件市场

---

## 11. 常见误区与反模式

### 误区 1：把 Plugin / Skill / MCP 当成同一种东西

**后果**：选型错误，增加复杂度

**正确做法**：理解三者区别，按场景选择

### 误区 2：没有最小样例就开始写复杂逻辑

**后果**：难以定位问题，容易陷入困境

**正确做法**：先跑通 Hello World，再逐步扩展

### 误区 3：不看日志就改代码

**后果**：盲目修改，可能引入新问题

**正确做法**：先看日志定位问题，再针对性修改

### 误区 4：先追求功能全，后补测试

**后果**：代码不稳定，难以维护

**正确做法**：边开发边测试，每个功能都验证

### 误区 5：Skill 和 Plugin 乱用

**后果**：架构混乱，难以理解

**正确做法**：
- 处理消息事件 → Plugin
- 提供 Agent 指南 → Skill

### 误区 6：MCP 一上来就接复杂服务

**后果**：调试困难，不知哪层出问题

**正确做法**：先用简单 MCP 验证链路，再接入复杂服务

### 误区 7：不做错误处理和回退

**后果**：一个小错误导致整个插件崩溃

**正确做法**：
```python
try:
    result = await operation()
except Exception as e:
    logger.error(f"错误: {e}", exc_info=True)
    yield event.plain_result("出错了，请稍后重试")
```

### 误区 8：不做最小复现

**后果**：在完整系统中排查，干扰因素多

**正确做法**：抽离最小代码复现问题

### 误区 9：数据存在插件目录

**后果**：更新插件时数据丢失

**正确做法**：存到 `data/` 目录

### 误区 10：使用同步网络库

**后果**：阻塞事件循环，影响性能

**正确做法**：用 aiohttp、httpx 等异步库

---

## 12. 快速参考

### 12.1 三秒选型

```
需要处理消息事件？        → Plugin
需要访问 AstrBot API？    → Plugin
是任务说明书/知识？       → Skill
是外部工具服务？          → MCP
```

### 12.2 开发检查清单

- [ ] main.py 存在
- [ ] metadata.yaml 完整
- [ ] requirements.txt 有依赖
- [ ] 代码用 ruff 格式化
- [ ] 有错误处理
- [ ] 有日志输出
- [ ] 测试通过
- [ ] README 完善

### 12.3 重要链接

| 资源 | 链接 |
|------|------|
| 官方文档 | https://docs.astrbot.app |
| 主仓库 | https://github.com/AstrBotDevs/AstrBot |
| 插件模板 | https://github.com/Soulter/helloworld |
| 插件市场 | https://plugins.astrbot.app |
| Skills 规范 | https://code.claude.com/docs/zh-CN/skills |
| MCP 协议 | https://modelcontextprotocol.io |
| MCP Servers | https://github.com/modelcontextprotocol/servers |
| awesome-mcp | https://github.com/punkpeye/awesome-mcp-servers |

---

## 附录 A：平台适配矩阵

| 平台 | At | Plain | Image | Record | Video | Reply | 主动消息 |
|------|:--:|:-----:|:-----:|:------:|:-----:|:-----:|:--------:|
| QQ 个人号 (aiocqhttp) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Telegram | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 飞书 | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| 企业微信 | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 钉钉 | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Discord | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Slack | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 附录 B：常用 API 速查

```python
# 获取信息
event.message_str          # 纯文本
event.get_sender_name()    # 发送者名
event.get_sender_id()      # 发送者 ID
event.get_group_id()       # 群 ID
event.unified_msg_origin   # 会话标识

# 发送消息
yield event.plain_result("文本")
yield event.image_result("path/to/image.jpg")
yield event.chain_result([Comp.Plain("文本"), Comp.At(qq=123)])

# 调用 LLM
prov = self.context.get_using_provider(umo=event.unified_msg_origin)
resp = await prov.text_chat(prompt="Hi!", context=[], system_prompt="...")

# 日志
from astrbot.api import logger
logger.info("信息")

# 配置
def __init__(self, context: Context, config: AstrBotConfig):
    self.config = config
    value = self.config.get("key", "default")
```

---

*本手册基于 AstrBot 官方文档整理，持续更新中。*