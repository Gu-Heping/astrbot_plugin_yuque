# NovaBot

> NOVA 社团智能助手 · 以语雀知识库为核心的 AstrBot Plugin

完全自包含，内置语雀同步 + RAG 检索，不依赖外部服务。

---

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Gu-Heping/astrbot_plugin_yuque.git
```

## 配置

| 配置项 | 说明 |
|--------|------|
| `yuque_token` | 语雀团队 Token |
| `yuque_base_url` | 语雀 API 地址，默认 `https://nova.yuque.com/api/v2` |
| `embedding_api_key` | Embedding API Key |
| `embedding_base_url` | Embedding API 地址（可选） |
| `embedding_model` | Embedding 模型，默认 `text-embedding-3-small` |

## 指令

| 指令 | 说明 |
|------|------|
| `/bind <用户名>` | 绑定语雀账号 |
| `/unbind` | 解除绑定 |
| `/profile` | 查看用户画像 |
| `/sync` | 同步知识库 |
| `/sync members` | 同步团队成员 |
| `/rag search <关键词>` | 搜索文档 |
| `/novabot` | 帮助信息 |

## 使用流程

```
1. 管理员配置 yuque_token
2. 管理员: /sync members
3. 用户: /bind <用户名>
4. 用户: /sync
```

---

## 版本历史

| 版本 | 变更 |
|------|------|
| v0.4.0 | Markdown + frontmatter 同步 |
| v0.3.0 | `/sync members`，模糊匹配绑定 |
| v0.2.0 | 内置同步 + RAG |
| v0.1.0 | 初始版本 |