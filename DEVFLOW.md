# NovaBot 开发流程自动化

> 状态机驱动的全流程开发系统

---

## 状态机

```
  idle ──→ planning ──→ developing ──→ testing ──→ auditing
    ↑          ↑            │              │           │
    │          └────────────┘              │           │
    │                                      ↓           ↓
    │                              committing ← pushing
    │                                  │           │
    └──── optimizing ← refactoring ←─┘           │
        ↑                                          │
        └──────────────────────────────────────────┘
```

---

## 阶段定义

### 1. idle（空闲）

**触发条件**：无当前任务，或上一轮已完成

**动作**：
1. 读取 `state.json`，找到 `status="pending"` 且 `priority` 最高（数字最小）的任务
2. 如果找到任务：
   - 设置 `current_task` 为该任务 ID
   - 进入 `planning` 阶段
3. 如果没有任务：
   - 检查是否有 `blocked` 任务可以解锁
   - 检查是否有 `questions` 需要用户确认
   - 如果都没有，保持 `idle`，记录日志

**输出**：
- 更新 `state.json`
- 日志：「idle → planning（任务 T001: 实现 /bind 指令）」

---

### 2. planning（计划）

**触发条件**：进入新任务

**动作**：
1. **读官方文档**（必须）：
   - AstrBot 开发指导手册：`vault/项目/多模态Agent系统探索/AstrBot开发指导手册-Plugin-Skills-MCP.md`
   - 相关插件示例
   - API 文档
2. 确定实现方案
3. 写计划到 `plans/{task_id}.md`
4. 更新 `state.json`：
   - `docs_read` 添加已读文档
   - 进入 `developing` 阶段

**输出**：
- `plans/{task_id}.md`（实现计划）
- 日志：「planning → developing（计划已制定）」

---

### 3. developing（开发）

**触发条件**：计划已制定

**动作**：
1. 按计划实现代码
2. 写到 `main.py` 或相应文件
3. 更新 `metadata.yaml`、`_conf_schema.json`
4. 完成后进入 `testing` 阶段

**输出**：
- 代码文件
- 日志：「developing → testing」

---

### 4. testing（测试）

**触发条件**：代码已完成

**动作**：
1. 运行 `pytest tests/` 或手动测试
2. 记录结果：
   - 通过：`tests_passed += 1`，进入 `auditing`
   - 失败：`tests_failed += 1`，回到 `developing`
3. 如果连续失败 3 次，移到 `blocked`，记录原因

**输出**：
- 测试结果
- 日志：「testing → auditing（通过 5/5）」或「testing → developing（失败 2/5）」

---

### 5. auditing（审计）

**触发条件**：测试通过

**动作**：
1. 代码审查：
   - 安全检查：无敏感信息泄露
   - 性能检查：无明显性能问题
   - 风格检查：`ruff check .`
2. 如果发现问题：
   - 记录到 `audits/{task_id}.md`
   - 回到 `developing` 修复
3. 如果通过，进入 `committing`

**输出**：
- 审计报告
- 日志：「auditing → committing（无问题）」

---

### 6. committing（提交）

**触发条件**：审计通过

**动作**：
1. 生成 commit message（遵循 Conventional Commits）
2. `git add .`
3. `git commit -m "..."`
4. 更新 `state.json`：`commits` 添加 commit hash
5. 进入 `pushing`

**输出**：
- Commit
- 日志：「committing → pushing（abc123）」

---

### 7. pushing（推送）

**触发条件**：提交完成

**动作**：
1. `git push origin main`
2. 如果推送失败：
   - `git pull --rebase`
   - 解决冲突
   - 回到 `committing`
3. 如果成功，进入 `refactoring`

**输出**：
- 日志：「pushing → refactoring」

---

### 8. refactoring（重构）

**触发条件**：推送完成

**动作**：
1. 分析代码，识别重构机会：
   - 重复代码
   - 过长函数
   - 命名不清晰
2. 如果有重构机会：
   - 执行重构
   - 回到 `testing`
3. 如果没有，进入 `optimizing`

**输出**：
- 日志：「refactoring → optimizing（无重构机会）」或「refactoring → testing（重构 3 处）」

---

### 9. optimizing（优化）

**触发条件**：重构完成

**动作**：
1. 性能分析
2. 文档更新
3. 标记任务完成：
   - 从 `task_queue` 移除
   - 添加到 `completed`
   - `current_task = null`
4. 进入 `idle`

**输出**：
- 更新文档
- 日志：「optimizing → idle（T001 完成）」

---

## state.json 结构

```json
{
  "version": "1.0",
  "project": "NovaBot",
  "last_wake": "2026-03-26T01:40:00",
  "wake_count": 1,
  
  "phase": "developing",
  "phase_entered": "2026-03-26T01:35:00",
  "phase_history": [
    {"phase": "idle", "entered": "2026-03-26T01:00:00", "exited": "2026-03-26T01:10:00"},
    {"phase": "planning", "entered": "2026-03-26T01:10:00", "exited": "2026-03-26T01:35:00"}
  ],
  
  "current_task": "T001",
  "task_queue": [...],
  "completed": [...],
  "blocked": [...],
  
  "questions": [...],
  "ideas": [...],
  
  "docs_read": ["AstrBot开发指导手册.md"],
  "commits": ["abc123"],
  "tests_passed": 2,
  "tests_failed": 0
}
```

---

## 定时任务配置

**调度**：`*/30 * * * *`（每 30 分钟）

**Payload**：
```
继续 NovaBot 开发流程：
1. 读取 state.json 获取当前状态
2. 根据阶段执行相应动作（参考 DEVFLOW.md）
3. 更新 state.json
4. 如有阻塞或问题，主动提出

项目路径：astrbot_plugin_yuque/
状态文件：state.json
流程定义：DEVFLOW.md
```

---

## 防护机制

### 防止无限循环

- 同一阶段停留超过 3 次唤醒 → 标记 `blocked`
- 测试连续失败 3 次 → 标记 `blocked`
- 推送冲突无法解决 → 标记 `blocked`

### 防止状态丢失

- 每次唤醒先读 `state.json`
- 每次动作后立即更新 `state.json`
- 关键操作记录到 `phase_history`

### 防止重复劳动

- `completed` 列表记录已完成任务 ID
- 启动时检查任务是否已完成

---

## 唤醒日志格式

```
[2026-03-26 01:40] Wake #3 | Phase: developing | Task: T001
  → 读取官方文档 3 篇
  → 实现 /bind 指令核心逻辑
  → 更新 state.json
  → 下次目标：testing

[2026-03-26 02:10] Wake #4 | Phase: testing | Task: T001
  → 运行测试：3/3 通过
  → 进入 auditing
```

---

*此文件定义了 NovaBot 的自动化开发流程*