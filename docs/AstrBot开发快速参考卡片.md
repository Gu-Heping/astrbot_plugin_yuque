---
created: 2026-03-25T09:28:00
modified: 2026-03-25T09:28:00
tags:
  - AstrBot
  - 快速参考
  - Cheatsheet
---

# AstrBot 开发快速参考卡片

> 配合 [[AstrBot开发指南-Plugin-Skills-MCP]] 使用

---

## 一、三秒选型

```
需要处理消息事件？        → Plugin
需要访问 AstrBot API？    → Plugin
是任务说明书/知识？       → Skills
是外部工具服务？          → MCP
```

---

## 二、Plugin 最小模板

### 目录结构

```
astrbot_plugin_xxx/
├── main.py           # 必需
├── metadata.yaml     # 必需
└── requirements.txt  # 可选
```

### main.py

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
        """这是一个 hello 指令"""
        yield event.plain_result("Hello!")
```

### metadata.yaml

```yaml
name: my_plugin
display_name: My Plugin
desc: 插件描述
version: v1.0.0
author: YourName
repo: https://github.com/xxx/xxx
```

---

## 三、常用装饰器速查

### 指令相关

```python
# 基本指令
@filter.command("cmd")

# 带参指令
@filter.command("add")  # /add 1 2
async def add(self, event, a: int, b: int): ...

# 指令组
@filter.command_group("math")
def math(self): pass

@math.command("add")
async def math_add(self, event, a: int, b: int): ...

# 指令别名
@filter.command("help", alias={'帮助', 'helpme'})
```

### 事件监听

```python
# 所有消息
@filter.event_message_type(filter.EventMessageType.ALL)

# 仅私聊
@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)

# 仅群聊
@filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)

# 特定平台
@filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)

# 仅管理员
@filter.permission_type(filter.PermissionType.ADMIN)
```

### 事件钩子

```python
# LLM 请求前
@filter.on_llm_request()
async def on_llm_req(self, event, req): ...

# LLM 响应后
@filter.on_llm_response()
async def on_llm_resp(self, event, resp): ...

# 发送消息前
@filter.on_decorating_result()
async def on_decorating(self, event): ...

# Bot 初始化完成
@filter.on_astrbot_loaded()
async def on_loaded(self): ...
```

---

## 四、消息操作速查

### 获取信息

```python
event.message_str          # 纯文本消息
event.get_sender_name()    # 发送者名称
event.get_sender_id()      # 发送者 ID
event.get_group_id()       # 群 ID（私聊为空）
event.unified_msg_origin   # 会话标识
event.message_obj.message  # 消息链
```

### 发送消息

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

### 主动消息

```python
from astrbot.api.event import MessageChain

umo = event.unified_msg_origin  # 保存会话标识
chain = MessageChain().message("主动消息")
await self.context.send_message(umo, chain)
```

---

## 五、LLM 调用速查

```python
# 获取当前提供商
prov = self.context.get_using_provider(umo=event.unified_msg_origin)

# 调用 LLM
resp = await prov.text_chat(
    prompt="你好",
    context=[],  # 对话历史
    system_prompt="你是助手"
)
print(resp.completion_text)

# 获取指定提供商
prov = self.context.get_provider_by_id("provider_id")

# 获取所有提供商
all_provs = self.context.get_all_providers()
```

---

## 六、配置管理速查

### _conf_schema.json

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

### 读取配置

```python
def __init__(self, context: Context, config: AstrBotConfig):
    super().__init__(context)
    self.config = config
    api_key = self.config.get("api_key", "")
```

---

## 七、函数工具速查

### 类形式（推荐）

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

### 装饰器形式

```python
@filter.llm_tool(name="tool_name")
async def my_tool(self, event, param: str):
    '''工具描述
    
    Args:
        param(string): 参数描述
    '''
    return "结果"
```

---

## 八、Skills 最小模板

### 目录结构

```
my-skill/
└── SKILL.md
```

### SKILL.md

```yaml
---
name: my-skill
description: 清晰描述做什么以及何时使用
---

# My Skill

## 何时使用
触发场景描述

## 步骤
1. 第一步
2. 第二步

## 示例
- 示例 1
```

### 打包上传

```bash
zip -r my-skill.zip my-skill/
# 上传到 AstrBot 管理面板 → 插件 → Skills
```

---

## 九、MCP 配置速查

### uv 方式

```json
{
  "command": "uv",
  "args": ["tool", "run", "server-name", "--option", "value"]
}
```

### npm 方式

```json
{
  "command": "npx",
  "args": ["-y", "@scope/server-name", "/path/to/dir"]
}
```

### 带环境变量

```json
{
  "command": "env",
  "args": [
    "API_KEY=xxx",
    "uv", "tool", "run", "server-name"
  ]
}
```

---

## 十、会话控制速查

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
        
        # 处理逻辑
        await event.send(event.plain_result("继续..."))
        controller.keep(timeout=60, reset_timeout=True)

    try:
        await waiter(event)
    except TimeoutError:
        yield event.plain_result("超时！")
```

---

## 十一、平台适配速查

| 平台 | 代号 | At | 图片 | 语音 | 主动消息 |
|------|------|:--:|:----:|:----:|:--------:|
| QQ 个人号 | `aiocqhttp` | ✅ | ✅ | ✅ | ✅ |
| Telegram | `telegram` | ✅ | ✅ | ✅ | ✅ |
| 飞书 | `lark` | ✅ | ✅ | ❌ | ✅ |
| 企业微信 | `wecom` | ❌ | ✅ | ✅ | ❌ |
| 钉钉 | `dingtalk` | ❌ | ✅ | ❌ | ❌ |

---

## 十二、常见问题速查

| 问题 | 解决方案 |
|------|---------|
| 插件不加载 | 检查 `main.py` 和 `metadata.yaml` 是否存在 |
| 指令不触发 | 检查装饰器语法、指令名是否重复 |
| 依赖找不到 | 确认 `requirements.txt` 已创建 |
| Skills 不执行 | 检查执行环境配置（Local/Sandbox） |
| MCP 连接失败 | 确认 uv/npm 已安装、command 路径正确 |
| 热重载失败 | 查看控制台错误日志修复代码问题 |

---

## 十三、常用命令

```bash
# 克隆主仓库 + 插件
git clone https://github.com/AstrBotDevs/AstrBot
mkdir -p AstrBot/data/plugins && cd AstrBot/data/plugins
git clone <你的插件仓库>

# 安装 uv
pip install uv

# Docker 安装 node
docker exec -it astrbot /bin/bash
apt update && apt install curl -y
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash
. "$HOME/.nvm/nvm.sh" && nvm install 22
```

---

## 十四、重要链接

| 资源 | 链接 |
|------|------|
| 官方文档 | https://docs.astrbot.app |
| 主仓库 | https://github.com/AstrBotDevs/AstrBot |
| 插件模板 | https://github.com/Soulter/helloworld |
| 插件市场 | https://plugins.astrbot.app |
| Skills 规范 | https://github.com/anthropics/skills |
| MCP 协议 | https://modelcontextprotocol.io |
| MCP Servers | https://github.com/modelcontextprotocol/servers |
| awesome-mcp | https://github.com/punkpeye/awesome-mcp-servers |

---

*快速参考卡片 - 配合完整开发指南使用*