"""
NovaBot LLM 调用封装
统一 LLM 调用接口，自动处理 JSON 提取和重试
"""

import json
import re
from typing import TYPE_CHECKING, Optional, Union

from astrbot.api import logger

if TYPE_CHECKING:
    from .token_monitor import TokenMonitor


def sanitize_user_input(text: str, max_length: int = 500) -> str:
    """清理用户输入，防止 prompt 注入

    Args:
        text: 用户输入文本
        max_length: 最大长度限制

    Returns:
        清理后的文本
    """
    if not text:
        return ""

    # 截断过长输入
    if len(text) > max_length:
        text = text[:max_length]

    # 移除可能的 JSON 分隔符（防止伪造输出）
    text = text.replace("---JSON---", "")
    text = text.replace("```json", "``'")
    text = text.replace("```", "`")

    # 移除可能的指令注入模式
    injection_patterns = [
        r"忽略.*指令",
        r"ignore.*instruction",
        r"system\s*:",
        r"assistant\s*:",
        r"user\s*:",
    ]
    for pattern in injection_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    return text.strip()


async def call_llm(
    provider,
    prompt: str,
    system_prompt: str = "你是一个专业助手。",
    require_json: bool = True,
    max_retries: int = 2,
    token_monitor: Optional["TokenMonitor"] = None,
    feature: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Union[dict, str]:
    """统一的 LLM 调用封装

    Args:
        provider: LLM Provider 实例
        prompt: 用户提示词
        system_prompt: 系统提示词
        require_json: 是否需要返回 JSON
        max_retries: 最大重试次数
        token_monitor: Token 监控器（可选）
        feature: 功能名称（用于 token 记录）
        user_id: 用户 ID（用于 token 记录）

    Returns:
        如果 require_json=True，返回解析后的 dict
        否则返回原始文本
    """
    last_error = None
    last_result = None

    for attempt in range(max_retries + 1):
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt=system_prompt,
            )

            result = resp.completion_text.strip()
            last_result = result

            # 记录 token 使用
            if token_monitor and feature:
                try:
                    # 尝试从响应中获取 token 使用量
                    input_tokens = 0
                    output_tokens = 0

                    # AstrBot LLMResponse 结构：
                    # - raw_completion: ChatCompletion (OpenAI 格式)
                    # - raw_completion.usage.prompt_tokens / completion_tokens
                    if hasattr(resp, "raw_completion") and resp.raw_completion:
                        usage = getattr(resp.raw_completion, "usage", None)
                        if usage:
                            input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                            output_tokens = getattr(usage, "completion_tokens", 0) or 0

                    # 回退：尝试其他可能的字段
                    if input_tokens == 0 and output_tokens == 0:
                        if hasattr(resp, "usage") and resp.usage:
                            input_tokens = getattr(resp.usage, "prompt_tokens", 0) or 0
                            output_tokens = getattr(resp.usage, "completion_tokens", 0) or 0
                        elif hasattr(resp, "prompt_tokens"):
                            input_tokens = resp.prompt_tokens or 0
                            output_tokens = resp.completion_tokens or 0

                    if input_tokens > 0 or output_tokens > 0:
                        token_monitor.log_usage(
                            feature=feature,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            model=getattr(provider, "model", None),
                            user_id=user_id,
                        )
                except Exception as e:
                    logger.debug(f"[LLM] 记录 token 使用失败: {e}")

            if not require_json:
                return result

            # 尝试提取 JSON
            parsed = extract_json(result)
            return parsed

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"[LLM] JSON 解析失败 (attempt {attempt + 1}): {e}")
            if attempt < max_retries:
                # 重试时添加提示
                prompt = f"{prompt}\n\n注意：上次输出格式有误，请确保输出有效的 JSON。"
        except Exception as e:
            logger.error(f"[LLM] 调用失败: {e}")
            raise

    # 所有重试都失败，返回默认值或抛出异常
    if require_json:
        logger.error(f"[LLM] JSON 解析最终失败: {last_error}")
        raise last_error or ValueError("JSON 解析失败")

    return last_result or ""


def extract_json(text: str) -> dict:
    """从文本中提取 JSON

    支持格式：
    1. ---JSON---\n{...}\n---JSON---
    2. ```json\n{...}\n```
    3. 直接的 JSON 对象

    Args:
        text: 包含 JSON 的文本

    Returns:
        解析后的 dict

    Raises:
        json.JSONDecodeError: 无法解析 JSON
    """
    text = text.strip()

    # 1. 尝试提取 ---JSON--- 分隔符包裹的内容
    json_match = re.search(r'---JSON---\s*\n(.*?)\n\s*---JSON---', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        return json.loads(json_str)

    # 2. 尝试提取 ```json 代码块
    json_match = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        return json.loads(json_str)

    # 3. 尝试提取 ``` 代码块（不带 json 标记）
    json_match = re.search(r'```\s*\n?(.*?)\n?```', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
        # 检查是否像 JSON
        if json_str.startswith('{') or json_str.startswith('['):
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    # 4. 尝试直接解析整个文本
    if text.startswith('{') or text.startswith('['):
        return json.loads(text)

    # 5. 尝试查找文本中的 JSON 对象
    brace_start = text.find('{')
    if brace_start != -1:
        # 找到匹配的闭合括号
        depth = 0
        for i, char in enumerate(text[brace_start:], brace_start):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    json_str = text[brace_start:i + 1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        break

    raise json.JSONDecodeError("无法从文本中提取 JSON", text, 0)


def format_docs_for_profile(docs: list[dict], max_docs: int = 20, max_chars: int = 3000) -> str:
    """格式化文档列表用于画像生成

    Args:
        docs: 文档列表
        max_docs: 最大文档数
        max_chars: 最大字符数

    Returns:
        格式化的文档摘要
    """
    if not docs:
        return "暂无文档"

    lines = []
    total_chars = 0

    for i, doc in enumerate(docs[:max_docs]):
        title = doc.get("title", "无标题")
        author = doc.get("author", "未知")
        book_name = doc.get("book_name", "")
        updated_at = doc.get("updated_at", "")
        description = doc.get("description", "")

        line = f"{i + 1}. 《{title}》"
        if author:
            line += f" - {author}"
        if book_name:
            line += f" [{book_name}]"
        if updated_at:
            line += f" ({updated_at[:10]})"
        if description:
            desc_preview = description[:100] + "..." if len(description) > 100 else description
            line += f"\n   {desc_preview}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)


def format_resources_for_path(resources: list[dict], max_resources: int = 15) -> str:
    """格式化资源列表用于学习路径生成

    Args:
        resources: 资源列表
        max_resources: 最大资源数

    Returns:
        格式化的资源摘要
    """
    if not resources:
        return "暂无相关资源"

    lines = []
    for i, res in enumerate(resources[:max_resources]):
        title = res.get("title", "无标题")
        author = res.get("author", "未知")
        book_name = res.get("book_name", "")

        line = f"{i + 1}. 《{title}》"
        if author:
            line += f" - {author}"
        if book_name:
            line += f" [{book_name}]"

        lines.append(line)

    return "\n".join(lines)