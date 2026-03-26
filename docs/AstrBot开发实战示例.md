---
created: 2026-03-25T09:28:00
modified: 2026-03-25T09:28:00
tags:
  - AstrBot
  - 示例代码
  - 实战
---

# AstrBot 开发实战示例

> 配合 [[AstrBot开发指南-Plugin-Skills-MCP]] 和 [[AstrBot开发快速参考卡片]] 使用

---

## 一、Plugin 实战示例

### 示例 1：天气查询插件

**目录结构**：

```
astrbot_plugin_weather/
├── main.py
├── metadata.yaml
├── requirements.txt
└── _conf_schema.json
```

**metadata.yaml**：

```yaml
name: astrbot_plugin_weather
display_name: 天气查询
desc: 查询城市天气信息
version: v1.0.0
author: YourName
repo: https://github.com/xxx/astrbot_plugin_weather
support_platforms:
  - aiocqhttp
  - telegram
```

**requirements.txt**：

```
aiohttp>=3.8.0
```

**_conf_schema.json**：

```json
{
  "api_key": {
    "description": "天气 API Key",
    "type": "string",
    "hint": "请输入你的天气 API Key",
    "obvious_hint": true
  },
  "default_city": {
    "description": "默认城市",
    "type": "string",
    "default": "北京"
  }
}
```

**main.py**：

```python
import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger


@register("weather", "YourName", "天气查询插件", "1.0.0")
class WeatherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.api_key = config.get("api_key", "")
        self.default_city = config.get("default_city", "北京")

    @filter.command("weather")
    async def weather(self, event: AstrMessageEvent, city: str = None):
        """查询城市天气"""
        city = city or self.default_city
        
        if not self.api_key:
            yield event.plain_result("请先配置天气 API Key")
            return

        try:
            weather_info = await self._fetch_weather(city)
            yield event.plain_result(weather_info)
        except Exception as e:
            logger.error(f"天气查询失败: {e}")
            yield event.plain_result(f"查询失败: {str(e)}")

    @filter.command("weather:set")
    async def set_default_city(self, event: AstrMessageEvent, city: str):
        """设置默认城市"""
        self.default_city = city
        self.config["default_city"] = city
        self.config.save_config()
        yield event.plain_result(f"默认城市已设置为: {city}")

    async def _fetch_weather(self, city: str) -> str:
        """调用天气 API"""
        url = f"https://api.example.com/weather?city={city}&key={self.api_key}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        
        # 解析返回数据
        temp = data.get("temp", "未知")
        desc = data.get("description", "未知")
        humidity = data.get("humidity", "未知")
        
        return f"📍 {city}\n🌡️ 温度: {temp}°C\n☁️ 天气: {desc}\n💧 湿度: {humidity}%"
```

---

### 示例 2：群消息转发插件

**main.py**：

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.event import MessageChain


class ForwardPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 转发规则: {源群ID: 目标群ID列表}
        self.forward_rules = {}

    @filter.command("forward:add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_forward(self, event: AstrMessageEvent, src_group: str, dst_group: str):
        """添加转发规则（管理员）"""
        if src_group not in self.forward_rules:
            self.forward_rules[src_group] = []
        self.forward_rules[src_group].append(dst_group)
        yield event.plain_result(f"已添加转发规则: {src_group} → {dst_group}")

    @filter.command("forward:list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def list_forward(self, event: AstrMessageEvent):
        """列出转发规则"""
        if not self.forward_rules:
            yield event.plain_result("暂无转发规则")
            return
        
        lines = ["转发规则:"]
        for src, dsts in self.forward_rules.items():
            lines.append(f"  {src} → {', '.join(dsts)}")
        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息并转发"""
        group_id = event.get_group_id()
        
        if group_id not in self.forward_rules:
            return
        
        dst_groups = self.forward_rules[group_id]
        message_str = event.message_str
        sender_name = event.get_sender_name()
        
        forward_msg = f"[{sender_name}]: {message_str}"
        
        for dst_group in dst_groups:
            try:
                # 构建 unified_msg_origin
                umo = f"aiocqhttp:GROUP:{dst_group}"
                chain = MessageChain().message(forward_msg)
                await self.context.send_message(umo, chain)
                logger.info(f"转发消息到 {dst_group}")
            except Exception as e:
                logger.error(f"转发失败 {dst_group}: {e}")
```

---

### 示例 3：LLM 对话增强插件

**main.py**：

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger


class LLMEnhancePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.custom_prompts = {}  # 按用户存储自定义提示词

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入自定义提示"""
        user_id = event.get_sender_id()
        
        # 添加用户自定义提示词
        if user_id in self.custom_prompts:
            custom = self.custom_prompts[user_id]
            req.system_prompt += f"\n\n用户偏好: {custom}"
        
        # 添加上下文信息
        group_id = event.get_group_id()
        if group_id:
            req.system_prompt += f"\n\n当前环境: 群聊 {group_id}"
        else:
            req.system_prompt += "\n\n当前环境: 私聊"

    @filter.command("prompt:set")
    async def set_prompt(self, event: AstrMessageEvent, prompt: str):
        """设置个人偏好提示词"""
        user_id = event.get_sender_id()
        self.custom_prompts[user_id] = prompt
        yield event.plain_result(f"已设置你的偏好: {prompt}")

    @filter.command("prompt:clear")
    async def clear_prompt(self, event: AstrMessageEvent):
        """清除个人偏好"""
        user_id = event.get_sender_id()
        if user_id in self.custom_prompts:
            del self.custom_prompts[user_id]
            yield event.plain_result("已清除你的偏好设置")
        else:
            yield event.plain_result("你还没有设置偏好")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """LLM 响应后记录日志"""
        logger.info(f"LLM 响应: {resp.completion_text[:50]}...")
```

---

### 示例 4：多轮对话游戏（成语接龙）

**main.py**：

```python
import random
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from astrbot.api import logger


class IdiomGamePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 简单的成语库
        self.idioms = ["一马当先", "先见之明", "明知故犯", "犯规操作", ...]

    @filter.command("成语接龙")
    async def idiom_game(self, event: AstrMessageEvent):
        """开始成语接龙游戏"""
        yield event.plain_result("🎮 成语接龙开始！请发送一个成语，输入「退出」结束游戏。")

        # 随机选择一个成语开始
        current = random.choice(self.idioms)
        last_char = current[-1]
        
        await event.send(event.plain_result(f"我先来: {current}"))
        await event.send(event.plain_result(f"请以「{last_char}」开头的成语~"))

        @session_waiter(timeout=120, record_history_chains=False)
        async def waiter(controller: SessionController, event: AstrMessageEvent):
            nonlocal current, last_char
            
            msg = event.message_str.strip()
            
            if msg == "退出":
                await event.send(event.plain_result("游戏结束！"))
                controller.stop()
                return
            
            # 验证成语
            if len(msg) != 4:
                await event.send(event.plain_result("成语应该是四个字哦~"))
                return
            
            if msg[0] != last_char:
                await event.send(event.plain_result(f"要以「{last_char}」开头呀~"))
                return
            
            # 玩家正确，机器人接龙
            new_last = msg[-1]
            # 查找以这个字开头的成语
            matching = [i for i in self.idioms if i[0] == new_last]
            
            if not matching:
                await event.send(event.plain_result(f"厉害！我接不上「{new_last}」开头的成语，你赢了！"))
                controller.stop()
                return
            
            response = random.choice(matching)
            last_char = response[-1]
            
            await event.send(event.plain_result(f"{response}，该你接「{last_char}」了~"))
            controller.keep(timeout=120, reset_timeout=True)

        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("⏰ 超时了！游戏结束~")
        finally:
            event.stop_event()
```

---

### 示例 5：LLM 函数工具

**tools/calculator.py**：

```python
from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent
from dataclasses import dataclass, field


@dataclass
class CalculatorTool(FunctionTool):
    name: str = "calculator"
    description: str = "执行数学计算。用于需要计算数值的场景。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '2+3*4' 或 'sqrt(16)'"
            }
        },
        "required": ["expression"]
    })

    async def run(self, event: AstrMessageEvent, expression: str):
        """执行数学计算"""
        try:
            # 安全地评估数学表达式
            import math
            allowed_names = {
                "sqrt": math.sqrt,
                "sin": math.sin,
                "cos": math.cos,
                "tan": math.tan,
                "log": math.log,
                "pi": math.pi,
                "e": math.e
            }
            result = eval(expression, {"__builtins__": {}}, allowed_names)
            return f"计算结果: {expression} = {result}"
        except Exception as e:
            return f"计算错误: {str(e)}"


@dataclass
class TimeTool(FunctionTool):
    name: str = "get_current_time"
    description: str = "获取当前时间。用于需要知道当前日期时间的场景。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })

    async def run(self, event: AstrMessageEvent):
        """获取当前时间"""
        from datetime import datetime
        now = datetime.now()
        return f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"
```

**main.py**：

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from .tools.calculator import CalculatorTool, TimeTool


class ToolsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 注册工具
        self.context.add_llm_tools(
            CalculatorTool(),
            TimeTool()
        )
```

---

## 二、Skills 实战示例

### 示例 1：代码审查 Skill

**目录结构**：

```
code-review/
├── SKILL.md
├── references/
│   └── review-checklist.md
└── scripts/
    └── analyze.py
```

**SKILL.md**：

```yaml
---
name: code-review
description: 代码审查技能。当用户请求代码审查、Code Review、检查代码质量时使用。
---

# Code Review Skill

## 目标
帮助用户进行全面的代码审查，发现潜在问题并提供改进建议。

## 审查流程

### 1. 代码质量检查
- 命名规范（变量、函数、类）
- 代码结构（模块化、职责单一）
- 注释质量

### 2. 潜在问题识别
- 空值/边界检查
- 异常处理
- 资源泄漏（文件、连接）
- 并发安全

### 3. 性能考量
- 算法复杂度
- 不必要的循环/查询
- 内存使用

### 4. 安全审查
- 输入验证
- SQL 注入
- XSS 风险
- 敏感信息暴露

## 输出格式

```
## 审查摘要
[整体评价]

## 发现的问题

### 🔴 严重
- [问题描述]

### 🟡 中等
- [问题描述]

### 🟢 建议
- [改进建议]

## 优点
- [代码亮点]
```

## 参考资源
详细检查清单见 [references/review-checklist.md](references/review-checklist.md)
```

**references/review-checklist.md**：

```markdown
# 代码审查检查清单

## Python 专项

- [ ] 使用了 `with` 语句管理资源
- [ ] 避免了可变默认参数
- [ ] 正确使用了 `async/await`
- [ ] 类型注解完整

## JavaScript 专项

- [ ] 使用了 `const/let` 而非 `var`
- [ ] 异步操作正确处理
- [ ] 避免了 `==` 使用 `===`

## 通用

- [ ] 函数长度合理（< 50 行）
- [ ] 嵌套层级合理（< 4 层）
- [ ] 无重复代码
```

---

### 示例 2：报告生成 Skill

**目录结构**：

```
report-generator/
├── SKILL.md
├── assets/
│   └── report-template.md
└── scripts/
    └── generate_pdf.py
```

**SKILL.md**：

```yaml
---
name: report-generator
description: 报告生成技能。当用户需要生成各类报告（周报、月报、项目报告等）时使用。
---

# 报告生成技能

## 功能
根据用户提供的数据和信息，生成结构化的报告文档。

## 报告类型

### 周报
包含：本周完成、进行中、下周计划、问题与风险

### 项目报告
包含：项目概述、进度、风险、下一步计划

### 分析报告
包含：背景、数据、分析、结论、建议

## 步骤

1. **确认报告类型**
   询问用户需要什么类型的报告

2. **收集信息**
   引导用户提供必要的数据和要点

3. **生成报告**
   使用模板生成结构化报告

4. **格式化输出**
   使用 Markdown 格式，清晰美观

## 模板

报告模板见 [assets/report-template.md](assets/report-template.md)

## 注意事项

- 保持客观、数据驱动
- 突出重点，避免冗长
- 使用清晰的标题和列表
```

**assets/report-template.md**：

```markdown
# {{报告标题}}

**日期**: {{日期}}  
**作者**: {{作者}}

---

## 概述

{{概述内容}}

---

## 主要内容

### 一、{{章节标题}}

{{章节内容}}

### 二、{{章节标题}}

{{章节内容}}

---

## 结论

{{结论内容}}

---

## 下一步计划

1. {{计划项 1}}
2. {{计划项 2}}
3. {{计划项 3}}
```

---

## 三、MCP 实战配置示例

### 示例 1：文件系统 MCP

```json
{
  "command": "npx",
  "args": [
    "-y",
    "@modelcontextprotocol/server-filesystem",
    "/home/admin/documents",
    "/home/admin/projects"
  ]
}
```

### 示例 2：PostgreSQL MCP

```json
{
  "command": "uv",
  "args": [
    "tool",
    "run",
    "mcp-server-postgres",
    "--connection-string",
    "postgresql://user:pass@localhost:5432/mydb"
  ]
}
```

### 示例 3：自定义 MCP Server

```json
{
  "command": "env",
  "args": [
    "OPENAI_API_KEY=sk-xxx",
    "uv",
    "tool",
    "run",
    "my-custom-mcp-server",
    "--config",
    "/data/mcp-config.json"
  ]
}
```

---

## 四、组合使用示例

### Plugin + MCP 协作

**场景**：用户发送图片，Plugin 接收后调用 MCP OCR 服务

**main.py**：

```python
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
import astrbot.api.message_components as Comp


class ImageOCRPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # MCP 工具通过 Tool Manager 访问
        self.tool_mgr = context.get_llm_tool_manager()

    @filter.command("ocr")
    async def ocr(self, event: AstrMessageEvent):
        """识别图片中的文字"""
        # 获取消息中的图片
        message = event.message_obj.message
        images = [comp for comp in message if isinstance(comp, Comp.Image)]
        
        if not images:
            yield event.plain_result("请发送图片后再使用 /ocr 命令")
            return
        
        # 获取 MCP 的 OCR 工具
        ocr_tool = self.tool_mgr.get_func("mcp_ocr_extract_text")
        
        if not ocr_tool:
            yield event.plain_result("OCR 服务未配置")
            return
        
        # 调用 OCR
        for img in images:
            try:
                # 假设工具接受 image_url 参数
                result = await ocr_tool.run(event, image_url=img.url)
                yield event.plain_result(f"识别结果:\n{result}")
            except Exception as e:
                logger.error(f"OCR 失败: {e}")
                yield event.plain_result(f"识别失败: {str(e)}")
```

---

*实战示例 - 配合开发指南和快速参考使用*