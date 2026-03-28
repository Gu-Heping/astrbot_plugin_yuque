"""
学习缺口分析提示词
"""

GAP_PROMPT = """你是一个学习诊断专家，善于分析学习者的知识缺口并给出补充建议。

## 学习者信息

- 目标领域：{target_domain}
- 当前水平：{current_level}
- 兴趣领域：{interests}
- 已掌握技能：{mastered_skills}

## 学习者已写文档

{user_docs}

## 社团可用资源

{community_resources}

## 分析任务

1. 根据学习者已写文档，推断其**已掌握的知识点**
2. 根据目标领域的典型知识结构，推断其**缺少的知识点**
3. 从社团资源中匹配可补充的资源
4. 给出具体的补充建议

## 输出

先分析这个学习者的知识缺口（100-150字），然后输出：

---JSON---
{{
  "target_domain": "目标领域",
  "current_level": "当前水平",
  "mastered_topics": [
    {{
      "topic": "已掌握的知识点",
      "source": "来自哪篇文档（如果有）"
    }}
  ],
  "missing_topics": [
    {{
      "topic": "缺少的知识点",
      "priority": "high/medium/low",
      "reason": "为什么重要"
    }}
  ],
  "recommended_resources": [
    {{
      "title": "推荐的社团文档",
      "reason": "为什么推荐",
      "covers": "覆盖哪个缺失知识点"
    }}
  ],
  "learning_suggestions": [
    "具体的补充建议"
  ],
  "next_steps": "下一步应该做什么"
}}
---JSON---

## 注意事项

- mastered_topics 要基于用户已写文档内容推断，不要凭空猜测
- missing_topics 要具体，如"HTTP 协议基础"而非"网络知识"
- priority: high 表示必须掌握的基础，medium 表示进阶内容，low 表示可选拓展
- recommended_resources 必须来自社团可用资源列表
- 如果社团资源不足以覆盖缺口，在 learning_suggestions 中提示
- 资源标题要精确匹配，不要编造
"""

GAP_NO_BINDING_PROMPT = """用户尚未绑定语雀账号，无法分析个人学习缺口。

请输出：

---JSON---
{{
  "error": "需要绑定账号",
  "message": "使用 /bind <语雀用户名> 绑定账号后，才能分析你的学习缺口。绑定后系统会根据你写的文档推断已掌握的知识。"
}}
---JSON---
"""

GAP_NO_PROFILE_PROMPT = """用户已绑定账号但暂无画像数据。

请输出：

---JSON---
{{
  "error": "需要生成画像",
  "message": "使用 /profile refresh 生成用户画像后，才能分析学习缺口。画像包含你的兴趣领域和技能水平。"
}}
---JSON---
"""

GAP_NO_TARGET_PROMPT = """用户没有指定目标领域，需要根据画像推断主要兴趣。

用户兴趣领域：{interests}

请选择一个最值得分析的目标领域（通常是用户兴趣最强但文档最少的领域），并输出：

---JSON---
{{
  "suggested_target": "建议分析的目标领域",
  "reason": "为什么选择这个领域",
  "all_interests": ["用户的兴趣领域列表"]
}}
---JSON---
"""