# AstrBot Agent 机制与多能力系统设计

> 基于 AstrBot 官方文档的技术分析
> 目标：设计单主代理 + 子能力模拟系统

---

## 1. AstrBot Agent 核心机制

### 1.1 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│                      AstrBot 架构                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Agent Runner（执行器）                                     │
│  ├── 内置 Agent Runner（默认）                              │
│  ├── Dify Agent Runner                                     │
│  ├── Coze Agent Runner                                     │
│  ├── 百炼应用 Agent Runner                                  │
│  └── DeerFlow Agent Runner                                 │
│                                                             │
│  Chat Provider（模型层）                                    │
│  ├── OpenAI / Anthropic / Google                           │
│  ├── DeepSeek / Qwen / 其他兼容提供商                       │
│  └── 单次补全接口：prompt + history + tools → response      │
│                                                             │
│  Tools（能力层）                                            │
│  ├── FunctionTool（函数工具）                               │
│  ├── MCP Server（外部能力）                                 │
│  └── Skills（Anthropic Skills）                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Agent Runner 职责分离

| 层 | 职责 | 本质 |
|---|------|------|
| Chat Provider | 说话 | 单次补全接口 |
| Agent Runner | 思考 + 行动 | 多轮循环 |

Agent Runner 实现 **感知 → 规划 → 行动 → 观察 → 再规划** 的循环。

---

## 2. tool_loop_agent 工作流程

### 2.1 核心方法

```python
llm_resp = await self.context.tool_loop_agent(
    event=event,                          # 事件对象
    chat_provider_id=prov_id,             # 模型提供商 ID
    prompt="用户任务",                     # 任务描述
    system_prompt="系统提示词",            # 可选
    tools=ToolSet([Tool1(), Tool2()]),    # 工具集合
    max_steps=30,                         # 最大执行步骤
    tool_call_timeout=60,                 # 工具调用超时
)
```

### 2.2 循环机制

```
Step 1: LLM 接收 prompt + tools 定义
        ↓
Step 2: LLM 决定：返回文本 或 调用工具
        ↓
Step 3a: 若返回文本 → 循环结束，返回 completion_text
        ↓
Step 3b: 若调用工具 → 执行工具 → 结果加入上下文 → 回到 Step 1
        ↓
... 循环直到 LLM 不再调用工具 或 达到 max_steps ...
```

### 2.3 返回值

```python
llm_resp.completion_text  # 最终文本响应
```

---

## 3. FunctionTool 定义

### 3.1 基本结构

```python
from pydantic import Field
from pydantic.dataclasses import dataclass
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

@dataclass
class MyTool(FunctionTool[AstrAgentContext]):
    name: str = "tool_name"                    # 工具名称
    description: str = "工具描述"               # LLM 可见的描述
    parameters: dict = Field(                  # JSON Schema 参数定义
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "参数描述",
                },
            },
            "required": ["param1"],
        }
    )
    
    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        # 工具执行逻辑
        return "工具返回结果"
```

### 3.2 装饰器方式

```python
@filter.llm_tool(name="get_weather")
async def get_weather(self, event: AstrMessageEvent, location: str) -> MessageEventResult:
    '''获取天气信息。
    Args:
        location(string): 地点
    '''
    result = await fetch_weather(location)
    yield event.plain_result(f"天气信息: {result}")
```

参数格式：`参数名(类型): 描述`

支持类型：`string`, `number`, `object`, `boolean`, `array`, `array[string]`

---

## 4. Multi-Agent（agent-as-tool）实现

### 4.1 核心概念

将子代理封装为 FunctionTool，主代理通过调用工具来委托任务。

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Agent                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ System Prompt: 你是主代理，负责分配任务给子代理       │   │
│  │ Tools: transfer_to_weather, transfer_to_search      │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│              调用 transfer_to_weather(query)                │
│                           ↓                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │               SubAgent (Weather)                     │   │
│  │ Tools: get_weather_info, format_response            │   │
│  │ → 执行 → 返回结果给 Main Agent                       │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 子代理 Tool 定义

```python
@dataclass
class WeatherSubAgent(FunctionTool[AstrAgentContext]):
    name: str = "transfer_to_weather"
    description: str = "委托天气相关任务给天气子代理。适用于查询天气、气候信息。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "需要执行的天气查询任务",
                },
            },
            "required": ["query"],
        }
    )
    
    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        # 获取上下文
        ctx = context.context.context
        event = context.context.event
        
        # 子代理有自己的工具集
        llm_resp = await ctx.tool_loop_agent(
            event=event,
            chat_provider_id=await ctx.get_current_chat_provider_id(
                event.unified_msg_origin
            ),
            prompt=kwargs["query"],
            system_prompt="你是天气助手，负责查询和解释天气信息。",
            tools=ToolSet([
                GetWeatherTool(),
                FormatWeatherTool(),
            ]),
            max_steps=10,
        )
        
        return llm_resp.completion_text
```

### 4.3 主代理调用

```python
@filter.command("ask")
async def ask(self, event: AstrMessageEvent):
    umo = event.unified_msg_origin
    prov_id = await self.context.get_current_chat_provider_id(umo)
    
    llm_resp = await self.context.tool_loop_agent(
        event=event,
        chat_provider_id=prov_id,
        prompt="帮我查一下北京和上海的天气对比",
        system_prompt=(
            "你是主代理。根据用户请求，决定委托给哪个子代理。"
            "可用子代理：天气查询、文档搜索、代码生成。"
        ),
        tools=ToolSet([
            WeatherSubAgent(),      # 天气子代理
            SearchSubAgent(),       # 搜索子代理
            CodeSubAgent(),         # 代码子代理
        ]),
        max_steps=30,
    )
    
    yield event.plain_result(llm_resp.completion_text)
```

---

## 5. SubAgent 编排模式（WebUI 配置）

### 5.1 与代码方式的区别

| 方式 | 配置位置 | 灵活性 |
|------|----------|--------|
| agent-as-tool（代码） | 插件代码中定义 | 完全自定义 |
| SubAgent 编排（WebUI） | WebUI 配置页面 | 配置驱动 |

### 5.2 WebUI 配置要素

- **Agent Name**: 生成委托工具名 `transfer_to_<name>`
- **Persona**: 子代理的人设和行为
- **Description**: 主代理可见的描述
- **Tools**: 子代理可用的工具
- **Provider Override**: 可选，使用不同模型

### 5.3 工作流程

```
用户请求 → Main Agent
              │
              ├─ transfer_to_weather → Weather SubAgent
              │                              ├─ get_weather
              │                              └─ 返回结果
              │
              └─ transfer_to_search → Search SubAgent
                                             └─ 返回结果
```

---

## 6. 单主代理 + 子能力模拟系统设计

### 6.1 设计目标

**只有一个主 Agent**，但具备多种专业能力：

| 能力 | 职责 |
|------|------|
| 文档分析 | 理解、分析、总结文档 |
| 架构设计 | 系统设计、方案输出 |
| 代码生成 | 生成代码、调试修复 |

**关键约束**：这些能力不是物理子代理，而是通过 **prompt + tool** 模拟的"逻辑子代理"。

### 6.2 实现方案

#### 方案 A：单次调用 + 能力注入

```python
@dataclass
class CapabilityRouter(FunctionTool[AstrAgentContext]):
    """能力路由工具"""
    name: str = "use_capability"
    description: str = "使用指定能力执行任务。可选：doc_analysis, architecture, code_gen"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "enum": ["doc_analysis", "architecture", "code_gen"],
                    "description": "能力类型",
                },
                "task": {
                    "type": "string",
                    "description": "具体任务",
                },
            },
            "required": ["capability", "task"],
        }
    )
    
    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        capability = kwargs["capability"]
        task = kwargs["task"]
        
        # 能力对应的专业 prompt
        capability_prompts = {
            "doc_analysis": """你是文档分析专家。
任务：{task}
输出：
1. 核心观点
2. 结构分析
3. 关键结论""",
            
            "architecture": """你是系统架构师。
任务：{task}
输出：
1. 架构图（ASCII）
2. 组件说明
3. 技术选型理由
4. 风险点""",
            
            "code_gen": """你是代码工程师。
任务：{task}
输出：
1. 代码实现
2. 关键注释
3. 测试用例""",
        }
        
        # 调用 LLM（使用专业 prompt）
        ctx = context.context.context
        event = context.context.event
        
        llm_resp = await ctx.llm_generate(
            chat_provider_id=await ctx.get_current_chat_provider_id(
                event.unified_msg_origin
            ),
            prompt=capability_prompts[capability].format(task=task),
        )
        
        return llm_resp.completion_text
```

#### 方案 B：递归 tool_loop_agent

```python
@dataclass
class DocAnalysisAgent(FunctionTool[AstrAgentContext]):
    """文档分析子代理（嵌套 tool_loop）"""
    name: str = "analyze_document"
    description: str = "分析文档内容，提取关键信息。支持：摘要、对比、问题提取。"
    parameters: dict = Field(...)
    
    async def call(self, context, **kwargs) -> ToolExecResult:
        ctx = context.context.context
        event = context.context.event
        
        # 子代理有自己的工具
        llm_resp = await ctx.tool_loop_agent(
            event=event,
            chat_provider_id=await ctx.get_current_chat_provider_id(
                event.unified_msg_origin
            ),
            prompt=kwargs["document_content"],
            system_prompt="你是文档分析专家...",
            tools=ToolSet([
                ExtractKeyPointsTool(),
                SummarizeTool(),
                CompareTool(),
            ]),
            max_steps=10,
        )
        
        return llm_resp.completion_text
```

### 6.3 推荐架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Agent（唯一）                       │
│                                                             │
│  System Prompt:                                             │
│  "你是全能助手。根据任务类型，调用相应能力工具。"            │
│                                                             │
│  Tools:                                                     │
│  ├── analyze_document   # 文档分析                          │
│  ├── design_architecture # 架构设计                         │
│  └── generate_code      # 代码生成                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
      ┌───────────┐   ┌───────────┐   ┌───────────┐
      │ 文档分析   │   │ 架构设计   │   │ 代码生成   │
      │ Tool      │   │ Tool      │   │ Tool      │
      │           │   │           │   │           │
      │ 内部可选  │   │ 内部可选  │   │ 内部可选  │
      │ 子工具    │   │ 子工具    │   │ 子工具    │
      └───────────┘   └───────────┘   └───────────┘
```

---

## 7. Prompt 设计要点

### 7.1 引导 LLM 调用工具

**问题**：LLM 可能直接回答而非调用工具

**解决方案**：

1. **明确的工具描述**

```python
description: str = """
分析文档并提取关键信息。
当用户需要：
- 文档摘要
- 关键点提取
- 内容对比
请调用此工具。

不要尝试自己分析文档，必须使用此工具。
"""
```

2. **系统提示词约束**

```python
system_prompt = """
你是专业的文档分析助手。

重要规则：
1. 收到文档内容后，必须调用 analyze_document 工具
2. 不要自己猜测或编造文档内容
3. 所有分析结果必须来自工具返回

可用工具：
- analyze_document: 文档分析
- compare_documents: 文档对比
"""
```

3. **强制工具调用模式**

某些模型支持 `tool_choice: "required"` 强制调用工具。

### 7.2 减少 Hallucination

**策略**：

1. **约束输出来源**

```python
system_prompt = """
你的回答必须基于工具返回的数据。
禁止编造或推测。
如果工具没有返回相关信息，回答"根据当前数据无法确定"。
"""
```

2. **引用机制**

```python
@dataclass
class SearchTool(FunctionTool):
    async def call(self, context, **kwargs):
        results = await search(kwargs["query"])
        # 返回带来源的结果
        return {
            "content": results,
            "sources": [r["url"] for r in results],
        }
```

3. **验证步骤**

```python
system_prompt = """
完成任务后，进行自检：
1. 结论是否来自工具数据？
2. 是否有未验证的假设？
3. 来源是否可追溯？
"""
```

### 7.3 任务拆解引导

```python
system_prompt = """
你是任务协调者。

收到复杂任务时：
1. 分析任务类型
2. 识别需要的子能力
3. 按顺序调用相应工具
4. 汇总结果

示例：
用户："分析这个 API 文档，然后设计一个调用它的客户端架构"

执行步骤：
1. 调用 analyze_document 分析 API 文档
2. 基于分析结果，调用 design_architecture 设计架构
3. 返回综合结果
"""
```

---

## 8. 插件结构

```
my_agent_plugin/
├── main.py                 # 插件入口
├── tools/
│   ├── __init__.py
│   ├── doc_analysis.py     # 文档分析工具
│   ├── architecture.py     # 架构设计工具
│   └── code_gen.py         # 代码生成工具
└── prompts/
    ├── doc_analysis.txt    # 文档分析 prompt
    ├── architecture.txt    # 架构设计 prompt
    └── code_gen.txt        # 代码生成 prompt
```

### main.py 示例

```python
from astrbot.api.star import Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.agent.tool import ToolSet

from .tools import (
    DocAnalysisTool,
    ArchitectureTool,
    CodeGenTool,
)

@register("capability_agent", "author", "多能力 Agent", "1.0.0")
class CapabilityAgentPlugin(Star):
    def __init__(self, context):
        super().__init__(context)
        # 注册工具到 AstrBot
        self.context.add_llm_tools(
            DocAnalysisTool(),
            ArchitectureTool(),
            CodeGenTool(),
        )
    
    @filter.command("analyze")
    async def analyze(self, event: AstrMessageEvent, content: str = ""):
        umo = event.unified_msg_origin
        prov_id = await self.context.get_current_chat_provider_id(umo)
        
        llm_resp = await self.context.tool_loop_agent(
            event=event,
            chat_provider_id=prov_id,
            prompt=content,
            system_prompt=self._load_prompt("main_system.txt"),
            tools=ToolSet([
                DocAnalysisTool(),
                ArchitectureTool(),
                CodeGenTool(),
            ]),
            max_steps=30,
        )
        
        yield event.plain_result(llm_resp.completion_text)
```

---

## 9. 关键设计决策

| 问题 | 决策 | 理由 |
|------|------|------|
| 物理子代理 vs 逻辑子代理 | 逻辑子代理 | 减少 prompt 膨胀，保持架构简单 |
| 单次 LLM vs 嵌套 tool_loop | 按需选择 | 简单能力用单次，复杂能力用嵌套 |
| 工具粒度 | 按能力域划分 | 避免 Main Agent 选择困难 |
| prompt 位置 | 外部文件 | 便于调试和迭代 |

---

## 10. 参考

- AstrBot AI 指南: https://docs.astrbot.app/dev/star/guides/ai.html
- Agent Runner: https://docs.astrbot.app/en/use/agent-runner.html
- SubAgent 编排: https://docs.astrbot.app/en/use/subagent.html