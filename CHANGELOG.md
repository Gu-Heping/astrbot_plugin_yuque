# 更新日志

所有重要的变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [v0.15.2] - 2026-03-28

### 修复
- **RAG 索引缺少 creator_id**：修复学习路径排除自己文档功能失效
  - sync.py: doc_metadata 添加 creator_id 字段
  - rag.py: 索引和搜索都传递 creator_id
  - 验证：语雀 API 的 `user_id` 字段是创建者 ID（而非最后编辑者）
- **作者显示为最后编辑者**：移除 `last_editor`，只从 `creator`/`user` 获取创建者名
  - 与 yuque2git commit 2995580 一致

## [v0.15.1] - 2026-03-28

### 修复
- **学习路径推荐排除自己的文档**：用户不再看到自己已写过的文档作为推荐资源
  - RAG 搜索阶段过滤用户自己的文档（通过 `exclude_author_id`）
  - 提示词告知 LLM 用户已写文档列表，避免推荐
  - 修复"📖 推荐资源"中显示用户自己文档的问题

## [v0.15.0] - 2026-03-28

### 新增
- **路径漂移修正**：同步前自动检测并修正文档路径变化
  - 新增 `sync_repo_path_drift()` 函数：遍历 TOC 计算所有文档理论路径
  - 使用 `git mv` 确保文件移动被 Git 正确追踪为 rename（而非 delete + add）
  - 解决父 DOC 移动/重命名后子 DOC 路径不更新的问题

### 修复
- **DOC 类型子节点路径继承**：子 DOC 现在继承父 DOC 标题作为路径前缀
  - 与 yuque2git 实现一致
  - 修复 `/profile refresh` 只显示部分文档的问题（从 7 篇增加到完整同步）

### 改进
- 同步模块鲁棒性改进（参考 yuque2git commit 0d8ea46, 4e088a8）
  - 新增 `_yuque_id_from_md()` 辅助函数：从 Markdown frontmatter 读取文档 ID

## [v0.14.9] - 2026-03-27

### 修复
- **学习路径显示"未知领域"**：修复 `target_domain` 未加入返回结果的问题
  - 现在正确显示用户输入的目标领域

## [v0.14.8] - 2026-03-27

### 新增
- **Webhook IP 白名单**：新增 `webhook_ip_whitelist` 配置项
  - 支持逗号分隔的 IP 列表（如 `47.96.64.251, 47.96.64.252`）
  - 留空则允许所有 IP 访问
  - 语雀官方 Webhook 服务器 IP：`47.96.64.251`

### 改进
- **Webhook 安全验证**：
  - IP 白名单验证优先于 User-Agent 验证
  - 通过白名单后不再警告可疑 User-Agent
  - 未设置白名单时提示建议配置

## [v0.14.7] - 2026-03-27

### 修复
- **推送链接使用错误的域名**：使用配置的 `yuque_base_url` 生成文档链接
  - 修复前：硬编码 `https://www.yuque.com/namespace/slug`
  - 修复后：使用配置的 base_url（如 `https://nova.yuque.com/namespace/slug`）

## [v0.14.6] - 2026-03-27

### 修复
- **孤儿目录自动清理**：同步时自动删除旧格式目录（如 `wg5tth_xgqv7d`）
  - 旧格式命名已废弃，所有知识库文件夹按知识库名称命名
  - 不再只是报告孤儿目录，而是直接清理

## [v0.14.5] - 2026-03-27

### 修复
- **孤儿知识库目录误判**：修复检测逻辑与目录命名逻辑不一致的问题
  - 当知识库 `name` 为空时，目录使用 `namespace` 命名（如 `wg5tth_xgqv7d`）
  - 孤儿检测现在考虑 `namespace` 作为备选，与 `sync_repo` 逻辑一致
  - 解决了每次同步都误报孤儿目录的问题

## [v0.14.4] - 2026-03-27

### 修复
- **Git 路径安全检查过于严格**：移除对 `&`、`|`、`;` 等字符的过滤
  - 这些字符在 `subprocess` 列表参数中是安全的，不会经过 shell 解析
  - 允许包含 `&` 的目录名（如 `Code&AI&Game/`）
- **Git 中文路径编码问题**：使用 `git add -A` 替代逐个指定文件路径
  - 避免中文、特殊字符的路径编码问题
  - 添加 `encoding='utf-8'` 和 `errors='replace'` 处理编码错误

## [v0.14.3] - 2026-03-27

### 修复
- **RAG Embedding "Event loop is closed" 错误**：重构 `embed_documents` 方法
  - 始终使用同步 HTTP 客户端（`httpx.Client`）
  - 避免在 `asyncio.to_thread` 线程中调用 `asyncio.run()` 导致的事件循环冲突
  - 修复了批量索引时交替失败的问题

## [v0.14.2] - 2026-03-27

### 新增
- **Embedding Token 监控**：RAG embedding 调用现在会记录 token 消耗
  - `/tokens` 指令现在包含 embedding 功能的 token 统计
  - 支持 DashScope embedding API（OpenAI embedding 需后续适配）

## [v0.14.1] - 2026-03-27

### 修复
- **全量同步 Git Commit**：`/sync` 同步后也会执行 git commit，确保 git 历史完整
  - 解决 `/weekly` 周报依赖 git 历史数据的问题
  - 全量同步和 Webhook 实时同步现在都会记录到 git 历史

## [v0.14.0] - 2026-03-27

### 新增
- **Git 历史分析器** (`novabot/git_analyzer.py`)
  - `GitAnalyzer` 类：提取 commit 历史统计数据
  - 支持贡献统计、活跃度分析、热门文档识别、趋势分析
  - 为周报和活跃度排行提供数据源
- **周报生成** (`/weekly` 指令)
  - 基于 Git commit 历史生成本周知识周报
  - 热门文档 TOP 5（按修改次数）
  - 活跃作者排行（按提交数和变更量）
  - 知识趋势分析
- **知识缺口发现** (`/gap` 指令)
  - 搜索日志记录 (`novabot/search_log.py`)
  - 分析无结果查询，识别知识盲区
  - 提供知识补充建议
- **Token 消耗监控** (`/tokens` 指令)
  - 记录 LLM 调用的 token 使用量
  - 按功能分类统计
  - 最近 7 天消耗趋势
- **RAG 搜索日志**：搜索时自动记录查询和结果数

### 架构
- `novabot/git_analyzer.py`：Git 历史分析器
- `novabot/weekly.py`：周报生成器
- `novabot/search_log.py`：搜索日志记录
- `novabot/knowledge_gap.py`：知识缺口分析
- `novabot/token_monitor.py`：Token 消耗监控

### 帮助信息更新
- 新增 `/weekly` - 本周知识周报
- 新增 `/gap` - 知识缺口分析
- 新增 `/tokens` - Token 消耗统计

## [v0.13.3] - 2026-03-27

### 修复
- **SQLite 连接泄漏**：为 DocIndex 添加 `__del__` 析构方法，确保对象销毁时关闭连接
- **未使用的缓存字段**：移除 Storage 中未使用的 `_cache_dirty` 字段，新增 `invalidate_cache()` 方法

### 改进
- **缓存失效机制**：Storage 新增 `invalidate_cache(cache_type)` 方法，支持手动清除缓存
- **close() 异常保护**：DocIndex.close() 添加 try-except，避免关闭时异常

## [v0.13.2] - 2026-03-27

### 修复
- **日志版本号过时**：更新 main.py 初始化日志为 v0.13.1
- **SQLite 异常处理**：为 DocIndex 所有数据库操作添加 sqlite3.Error 捕获
  - `_get_conn()`: 连接失败时抛出异常
  - `_init_db()`: 初始化失败时抛出异常
  - `add_doc()`, `add_docs()`: 添加文档失败时记录错误
  - `delete_doc()`: 删除文档失败时返回 False
  - `get_doc_by_yuque_id()`: 查询失败时返回 None
  - `search()`, `list_authors()`, `list_books()`: 查询失败时返回空列表
  - `get_stats()`: 统计失败时返回零值
  - `clear()`: 清空失败时记录错误

## [v0.13.1] - 2026-03-27

### 重构
- **LLM 提示词优化**：改进提示词设计，提升输出质量
  - 引入思维链（Chain of Thought）：要求 LLM 先分析再输出
  - 添加 Few-shot 示例：为推送判断提供正反例示范
  - 统一 JSON 提取：使用 `---JSON---` 分隔符，提高解析稳定性
  - 明确判断标准：为用户画像、推送判断定义清晰的边界条件
- **统一 LLM 调用封装**：`novabot/llm_utils.py`
  - `call_llm()`: 统一的 LLM 调用接口
  - `extract_json()`: 支持 `---JSON---` 和 ` ```json ` 两种格式
  - 自动重试机制：JSON 解析失败时提示重试
- **提示词模块化**：`novabot/prompts/`
  - `profile.py`: 用户画像和领域评估提示词
  - `knowledge_card.py`: 知识卡片生成提示词
  - `learning_path.py`: 学习路径推荐提示词（含无资源备选）
  - `push.py`: 智能推送判断提示词

### 改进
- 用户画像新增 `trajectory`（技术轨迹）和 `style`（学习风格）字段
- 知识卡片新增 `structure`（知识结构）和 `learning_order`（学习顺序）
- 学习路径新增 `gap_analysis`（差距分析）和 `milestones`（里程碑）
- 推送判断区分首次发布和更新两种场景，各有独立判断逻辑

### 修复
- **版本号不一致**：修复装饰器版本号与 metadata.yaml 不同步的问题
- **变量未定义**：修复 `sync_cmd` 中 `state` 变量在锁检查前未加载的问题
- **Storage 性能优化**：为 `load_bindings()` 和 `load_members()` 添加内存缓存，避免重复文件读取
- **HTTP 超时配置**：细化语雀客户端超时设置，区分连接/读取/写入超时
- **用户画像技能显示错误**：修复 `interests` 和 `skills` 键名不匹配导致全部显示"入门"的问题
  - 显示逻辑增加模糊匹配
  - 画像生成时自动对齐 `skills` 键名与 `interests` 一致
  - 提示词增加强制一致性要求
- **文档数统计错误**：修复画像中文档数多于实际的问题
  - 同步时存储 `creator_id` 到 frontmatter
  - 查询时优先通过 `creator_id` 精确匹配，避免名称匹配错误
  - 添加文档 ID 去重，防止重复统计

### 安全改进
- **Webhook 并发保护**：添加文档级别的异步锁，防止同一文档并发处理导致竞态条件

## [v0.13.0] - 2026-03-27

### 新增
- **伙伴推荐 `/partner`**：基于用户画像匹配学习伙伴和导师
  - `/partner` - 查看所有推荐
  - `/partner <主题>` - 按主题筛选
  - 支持水平相近的学习伙伴和经验更丰富的导师推荐
- **知识卡片生成**：LLM 工具 `generate_knowledge_card`
  - 聚合多篇相关文档生成结构化知识卡片
  - 提取核心知识点、工具资源、个人思考
  - 当用户说"我想学爬虫"等表达学习意愿时自动触发
- **领域认知评估**：`/profile assess <领域>`
  - 分析用户在特定领域的掌握程度
  - 识别已掌握知识点、正在学习内容
  - 给出下一步学习建议
- **学习路径推荐**：`/path <领域>`
  - 根据用户当前水平生成阶段性学习计划
  - 推荐社团内相关文档资源
  - 建议学习伙伴或导师
- **智能推送订阅**：`/subscribe` 指令
  - `/subscribe` - 查看订阅列表
  - `/subscribe repo <知识库名>` - 订阅知识库更新
  - `/subscribe author <作者名>` - 订阅作者更新
  - `/subscribe all` - 订阅全部更新
  - `/unsubscribe <ID>` - 取消订阅
  - 支持多群、私聊推送目标
  - LLM 智能判断更新价值，过滤无意义推送
  - 基于 commit diff 比较，避免重复推送
  - 首次推送读取文档原文，非首次推送分析 diff 变更

### 安全修复
- **Git 命令注入防护**：`git_ops.py` 新增路径和提交信息安全校验
  - 防止路径遍历攻击（`..` 检查）
  - 阻止危险字符注入（反引号、分号、管道符等）
  - 禁止以 `-` 开头的路径（Git 参数注入）
- **并发访问保护**：`subscribe.py` 使用 `asyncio.Lock` 保护订阅数据读写
- **资源泄漏防护**：`webhook.py` 使用上下文管理器管理 SQLite 连接
- **配置项安全**：`push_notifier.py` 从配置读取敏感参数，避免硬编码

### Bug 修复
- **推送消息显示文档链接**：新增 `url` 字段，显示语雀原文链接
- **作者名匹配优化**：
  - 新增 `find_member_by_id()` 方法，支持 user_id 精确匹配
  - 区分文档创建者（user_id）和编辑者（last_editor_id）
  - 推送消息显示编辑者，文档元数据记录创建者
- **全量同步作者匹配**：修复 sync.py 使用错误的字段名，现在正确使用 `user_id/creator_id`

## [v0.12.7] - 2026-03-27

### 依赖修复
- **修复 langchain/tenacity 版本冲突**：langchain 0.3.10+ 支持 tenacity>=9.0.0，兼容 AstrBot 核心
- 更新 requirements.txt 最小版本要求

## [v0.12.6] - 2026-03-27

### 性能优化
- **DashScopeEmbeddings 改用异步请求**：新增 `aembed_documents`/`aembed_query` 异步方法，使用 `httpx.AsyncClient` 避免阻塞事件循环

### 依赖管理
- **锁定依赖版本上限**：requirements.txt 添加 `<` 限制，避免版本不兼容
  - httpx<1.0.0, aiohttp<4.0.0, pyyaml<7.0
  - langchain 系列 <0.4.0, chromadb<0.6.0

## [v0.12.5] - 2026-03-27

### 架构改进
- **Tools 层解耦**：工具类不再直接导入 DocIndex/YuqueClient，通过 BaseTool 提供的方法访问
  - `get_doc_index()`: 获取文档索引实例
  - `slug_safe()`: 安全文件名转换
- 统一工具层的数据访问方式，降低耦合

## [v0.12.4] - 2026-03-27

### 架构改进
- **消除循环依赖**：WebhookHandler 不再依赖 NovaBotPlugin，改为通过构造函数注入具体依赖
- **合并 YuqueSync**：删除冗余的 YuqueSync 类，`get_docs_by_author()` 移至 Storage 类
- **Storage 添加 docs_dir 属性**：统一管理文档目录

### 代码精简
- main.py 减少约 40 行代码

## [v0.12.3] - 2026-03-27

### 改进
- **DocIndex/YuqueClient 支持上下文管理器**：支持 `with` 和 `async with` 语法，自动关闭连接
- **提取公共 frontmatter 解析函数**：`YuqueClient.parse_frontmatter()` 统一处理 YAML 解析，减少代码重复

## [v0.12.2] - 2026-03-27

### 安全修复
- **移除无效的 webhook_secret 配置**：语雀 Webhook 不支持自定义 Header，密钥验证无效
- 改用 User-Agent 检查作为基础验证（可配合反向代理 IP 白名单增强安全性）

### Bug 修复
- **修复 terminate 方法重复定义**：第二个方法会覆盖第一个，导致异常处理丢失
- **修复 JSON 解析异常未捕获**：`storage.py` 中多处 JSON 解析未处理损坏文件

### 并发安全
- **添加同步锁**：使用 `asyncio.Lock` 防止多个同步任务并发执行

## [v0.12.1] - 2026-03-26

### 修复
- **`list_repo_docs` 层级显示**：修复工具优先读 SQLite 导致层级结构丢失，现在正确显示 TITLE 分组

## [v0.12.0] - 2026-03-26

### 安全修复
- **HTTP 返回码修正**：处理失败时正确返回 4xx/5xx 状态码，而非固定 200
- **Git 配置安全**：禁止 GitOps 自动修改 git 用户配置，未配置身份时跳过 commit

### 一致性修复
- **路径生成统一**：Webhook 增量同步与全量同步使用相同的路径解析逻辑
  - 新增 `_find_toc_item_path()` 按 TOC 解析文档子目录
  - 新增 `_resolve_doc_output()` 统一输出路径决策
- **作者解析统一**：Webhook 和全量同步共用 `YuqueClient.author_name_from_detail()`
- **时间格式统一**：统一使用 `YuqueClient.normalize_timestamp()` 标准化时间

### 新增
- **`/sync clean` 指令**：清理孤儿知识库目录

### 性能优化
- **索引查询优化**：用 SQLite 索引替代全盘扫描查找旧文档（`_get_old_record()`）
- **RAG 去重优化**：按文档 ID 去重而非标题，避免标题相同误杀
- **删除时 TOC 更新**：优先重新拉取完整 TOC 覆盖，避免本地修补导致结构破坏
- **孤儿目录检测**：简化为只检查当前命名格式 `slug_safe(name)`，旧格式目录会被自动清理

## [v0.11.0] - 2026-03-26

### 新增
- **Webhook 实时同步**：语雀文档变更实时同步到本地
  - 支持 `publish`、`update`、`delete` 三种事件
  - 三层数据同步：本地 Markdown + SQLite + ChromaDB
  - 内嵌 aiohttp HTTP 服务，无需独立进程
- **Git 版本控制**：每次文档变更自动 commit，保留完整历史
  - `git_enabled`: 启用 Git 版本控制
  - `git_auto_push`: 自动推送到远程仓库
- **`/webhook` 指令**：查看 Webhook 服务状态
- **增量更新 API**：
  - `RAG.upsert_doc()`: 更新或插入单个文档向量
  - `RAG.delete_doc()`: 删除指定文档向量
  - `DocIndex.delete_doc()`: 删除索引记录
  - `DocIndex.get_doc_by_yuque_id()`: 按语雀 ID 查询

### 架构
- `novabot/webhook.py`：Webhook 处理器，处理语雀事件
- `novabot/git_ops.py`：Git 操作封装（init、add、commit、push）
- `main.py`：内嵌 aiohttp 服务器，生命周期与 AstrBot 同步

### 配置
- `webhook_enabled`: 启用 Webhook 服务（默认 false）
- `webhook_port`: Webhook 服务端口（默认 8766）
- `git_enabled`: 启用 Git 版本控制（默认 true）
- `git_auto_push`: 自动推送到远程仓库（默认 false）

## [v0.10.0] - 2026-03-26

### 新增
- **元数据索引**：同步时构建 SQLite 索引，支持高效元数据查询
- **`search_docs` 工具**：按作者、知识库、标题搜索文档
- **`list_authors` 工具**：列出所有作者及贡献统计
- **`doc_stats` 工具**：获取文档统计（总数、字数、知识库数）

### 架构
- `novabot/doc_index.py`：SQLite 元数据索引模块
- 同步时自动收集 frontmatter 元数据
- 支持按作者、知识库、标题、时间、字数查询和排序

### 数据存储
- `data/nova/doc_index.db`：SQLite 元数据索引
- 时区：所有时间使用 `Asia/Shanghai` 时区

### 说明
- **双重存储**：Markdown 文件（内容）+ SQLite 索引（元数据）
- **工具封装**：AI 调用工具传参数，无需编写 SQL
- **同步维护**：写入 Markdown 时自动更新索引

### 新增
- **`read_doc` 工具**：读取指定路径的文档完整内容（最长 8000 字符）
- grep 结果显示文档路径，方便 AI 进一步读取

### 改进
- **grep 按匹配数排序**：匹配越多的文档越靠前
- **grep 显示 2 个上下文**：更多信息帮助 AI 判断相关性
- **grep 高亮匹配词**：用 `**keyword**` 标记匹配位置
- 工具链更完整：`list_knowledge_bases` → `grep_local_docs` → `read_doc`

## [v0.9.6] - 2026-03-26

### 改进
- **RAG 搜索结果显示知识库**：返回结果包含 `📚 知识库: xxx`，方便 AI 判断来源
- **grep 工具支持知识库过滤**：新增 `repo_filter` 参数，可在特定知识库中精确搜索
- **工具描述优化**：引导 AI 先用 `list_knowledge_bases` 确定知识库，再用 `grep` 精确搜索
- **搜索结果提示**：语义搜索结果标注"可能不精确，建议用 grep 精确搜索"

## [v0.9.5] - 2026-03-26

### 修复
- **RAG 搜索去重**：按文档标题去重，避免返回同一文档的多个片段
- **RAG 全量重建**：索引前清空旧数据，避免重复索引
- **搜索结果质量**：返回更多上下文（500字符），多取结果用于去重

## [v0.9.4] - 2026-03-26

### 新增
- **孤儿文件清理**：全量同步时删除不在当前 TOC 中的 .md 文件
- **文档移动检测**：文档位置变更时自动删除旧路径
- **空目录清理**：清理只含 .toc.json 的残留目录
- **全局 ID 索引**：保存 `.yuque-id-to-path.json` 跟踪文档位置

### 改进
- 参考 yuque2git 实现完善同步逻辑
- 同步日志显示清理文件数量

## [v0.9.3] - 2026-03-26

### 修复
- **知识库列表路径**：同步时同时保存 `yuque_repos.json` 供 LLM 工具读取
- **知识库名称为空**：name 为空时使用 namespace 作为备选
- **目录名无效**：确保目录名不为空，空时使用 namespace

## [v0.9.2] - 2026-03-26

### 修复
- **同步状态显示**：修复知识库数显示为 0 的问题（使用 repos_count）
- **Token 类型显示**：sync_all_repos 返回 token_type，保存到状态
- **同步进度显示**：恢复进度回调，同步时实时更新进度
- **异常处理**：替换空异常处理为具体异常类型，添加日志记录
- **RAG 阻塞问题**：修复 embed_documents 中重复的线程池调用

## [v0.9.1] - 2026-03-26

### 修复
- **同步错误处理**：获取用户信息失败时优雅返回错误，避免 NoneType 异常

## [v0.9.0] - 2026-03-26

### 重构
- **模块拆分**：将语雀同步功能拆分到独立模块
  - `novabot/yuque_client.py`：语雀 API 客户端（限流、重试）
  - `novabot/sync.py`：文档同步器（TOC 层级处理）
- 基于 yuque2git 实现完整的 TOC 处理逻辑
  - TITLE 类型：创建目录，不写文件
  - DOC 类型：写入 Markdown 文件
  - 支持按层级创建目录结构
- **main.py 精简**：使用新模块替换原有同步逻辑
  - YuqueSync 类简化，只保留 get_docs_by_author 方法
  - sync_cmd 使用新模块的 sync_all_repos 函数

### 新增
- `toc_list_children()` 函数：按链表顺序遍历 TOC 子节点
- `DocSyncer` 类：完整文档同步器
- `sync_all_repos()` 函数：同步所有知识库

## [v0.8.0] - 2026-03-26

### 新增
- **LLM 工具调用**：AI 可自动调用知识库搜索工具
  - `search_knowledge_base`：RAG 语义搜索
  - `grep_local_docs`：本地文档关键词精确匹配
  - `list_knowledge_bases`：列出所有知识库
  - `list_repo_docs`：列出某知识库的文档结构（支持 TITLE/DOC 层级）
- **Agentic RAG**：AI 自主决定何时搜索、搜索什么
- **TOC 同步**：同步时保存 `.toc.json` 和 `.repos.json`

### 改进
- 优化文档表格过滤逻辑，跳过开头的元信息表格
- `list_repo_docs` 区分 TITLE（分组）和 DOC（实际文档）

## [v0.7.1] - 2026-03-26

### 修复
- **水平值标准化**：支持 LLM 返回中文/英文水平值，统一转换为英文
- **PROMPT 优化**：明确要求返回英文格式

## [v0.7.0] - 2026-03-26

### 重构
- **用户画像改为主动触发**：不再自动生成，只通过 `/profile refresh` 生成
- **LLM 深度分析**：使用 AI 分析用户文档，生成更准确的技术画像
- **更丰富的画像内容**：新增标签、一句话概括
- **移除关键词匹配**：不再依赖预设关键词列表，AI 自动识别领域

### 改进
- 绑定后提示用户使用 `/profile refresh` 生成画像
- 降低 LLM 调用成本：只有用户主动触发时才调用

## [v0.6.1] - 2026-03-26

### 新增
- **绑定时生成画像**：绑定账号后立即分析文档生成画像
- **`/profile refresh`**：手动刷新用户画像
- **动态兴趣发现**：自动从文档标题中提取新兴趣领域

### 改进
- **兴趣关键词大幅扩充**：从 11 个领域扩展到 28 个领域
  - 新增：机器学习、深度学习、C/C++、Kotlin、Verilog、MATLAB
  - 新增：全栈、Flutter、数学建模、算法、统计学
  - 新增：计算机体系结构、操作系统、学术写作、数学物理
  - 新增：产品运营、创业、游戏开发、Java Mod、浏览器插件
- **技能细分**：每个兴趣领域单独评估水平（入门/进阶/高级/探索中）
- **更丰富的画像显示**：统计文档数、知识库列表、整体水平
- **内容分析增强**：分析正文内容（前500字），不只是标题和描述

## [v0.6.0] - 2026-03-26

### 改进
- **RAG 搜索质量优化**：过滤 Markdown 元信息表格，只索引正文内容
- 使用正则表达式去除文档开头的表格格式干扰

## [v0.5.9] - 2026-03-26

### 修复
- 使用 ThreadPoolExecutor 避免 HTTP 请求阻塞事件循环

## [v0.5.8] - 2026-03-26

### 修复
- **DashScope Embedding 兼容**：自定义 Embedding 类处理阿里云 API 格式
- 自动检测 DashScope URL 并使用专用封装

## [v0.5.7] - 2026-03-26

### 修复
- **Embedding 内容验证**：严格验证文档内容类型，确保为有效字符串
- **分批索引**：每批 50 篇文档，失败时逐个重试定位问题
- **Embedding 测试**：索引前测试 API 可用性

## [v0.5.6] - 2026-03-26

### 修复
- **ChromaDB 初始化问题**：使用 `PersistentClient` + `get_or_create_collection`
- 损坏时使用 `client.reset()` 正确重置数据库
- 使用 AstrBot logger 替代 print

## [v0.5.5] - 2026-03-26

### 新增
- **用户画像自动生成**：同步完成后自动为已绑定用户生成画像
- 根据文档作者匹配用户，提取兴趣领域和水平

### 修复
- **RAG 数据库重建**：删除损坏数据库后重新创建目录

## [v0.5.4] - 2026-03-26

### 修复
- **ChromaDB 数据库自动修复**：加载时验证完整性，损坏时自动重建
- 添加详细日志输出，便于排查问题
- Windows 文件锁定时等待后重试删除

## [v0.5.3] - 2026-03-26

### 重构
- **RAG 模块重构**：精简代码从 300 行到 218 行
  - 移除已弃用的 `persist()` 调用，ChromaDB 自动持久化
  - 延迟初始化向量库，避免不必要的数据库连接
  - `clear()` 添加 `gc.collect()` 释放 Windows 文件句柄
  - 增强内容验证，跳过无效内容避免 Embedding API 错误

## [v0.5.2] - 2026-03-26

### 修复
- **ChromaDB 数据库损坏**：初始化时验证数据库，损坏时自动重置
- **RAG Embedding API 错误**：添加内容验证，跳过无效内容

## [v0.5.1] - 2026-03-26

### 新增
- **后台同步**：`/sync` 现在在后台运行，不阻塞用户操作
- **同步进度显示**：`/sync status` 显示当前同步进度（已同步/总知识库数）

### 修复
- **字符串拼接问题**：修复 `"━" * N` 导致消息重复输出的问题

## [v0.5.0] - 2026-03-26

### 重构
- **代码结构重构**：从 1100 行精简到 480 行，提升可维护性
- **统一存储逻辑**：所有数据操作集中到 `Storage` 类
- **客户端复用**：添加懒加载机制，避免重复创建 HTTP 客户端

### 修复
- **团队 Token 同步失败**：修复团队 Token 无法获取知识库的问题，改用 `/groups/{id}/repos` API
- **团队成员识别错误**：修复 `group_id` 获取逻辑，正确判断团队 Token 类型

### 新增
- **API 限流机制**：并发控制（最大 3 并发）、请求间隔（0.25s）、指数退避重试
- **开发文档**：添加 CLAUDE.md 和 AstrBot 开发指南

## [v0.4.0] - 2026-03-25

### 新增
- **Markdown + frontmatter 同步**：同步输出带 YAML 元数据的 Markdown 文件
- **作者信息**：从团队成员缓存获取作者姓名

### 修复
- **LangChain 导入错误**：修复模块导入路径
- **`/sync members` 循环依赖**：解耦同步逻辑

## [v0.3.0] - 2026-03-24

### 新增
- **`/sync members` 指令**：单独同步团队成员，无需绑定
- **模糊匹配绑定**：支持用户名部分匹配

### 改进
- 使用缓存的团队成员数据进行绑定，减少 API 调用

## [v0.2.0] - 2026-03-23

### 新增
- **内置同步模块**：语雀知识库自动同步
- **RAG 检索**：基于 LangChain + ChromaDB 的语义搜索
- **知识库自动发现**：无需手动配置知识库列表

### 修复
- 绑定逻辑修正：使用团队 Token + 用户名绑定模式

## [v0.1.0] - 2026-03-22

### 新增
- **`/bind` 指令**：绑定语雀账号
- **`/unbind` 指令**：解除绑定
- **`/profile` 指令**：查看用户画像
- **`/sync` 指令**：同步语雀知识库
- **`/rag` 指令**：RAG 检索管理
- **`/novabot` 指令**：帮助信息
- **用户画像生成**：基于关键词提取兴趣领域
- **LLM 系统提示钩子**：引导 AI 检索行为