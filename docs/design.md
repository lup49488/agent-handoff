# Lightweight Cross-Agent Failover / Handoff Tool

## 1. 项目目标

开发一个轻量级的 Coding Agent 插件 / CLI 工具，用于解决一个明确的问题：

> 当当前 Coding Agent 因额度耗尽、rate limit、进程异常或用户主动切换而无法继续任务时，将已有工作状态可靠地交给另一个 Agent，使其能够尽可能无缝地继续任务，而不是从头开始。

项目不定位为 Multi-Agent 平台，不负责复杂的 Agent 协作、规划、投票或长期记忆。

核心目标只有：

**Reliable Agent Handoff.**

第一阶段主要支持：

```text
Codex CLI
    ↓
Claude Code
```

后续再考虑：

```text
Claude Code → Codex
Codex / Claude → Pi
Gemini CLI
OpenCode
其他 Coding Agents
```

---

# 2. 核心问题

典型场景：

```text
用户：
修复 repository 中的 authentication bug

        ↓

Codex
读取代码
修改 auth.py
增加测试
运行 pytest
继续 debug

        ↓

Quota exhausted

        ↓

原 Agent 无法再产生 Token
```

传统方案的问题是：

```text
Claude Code
↓
重新读取任务
↓
重新扫描 repository
↓
重新理解已有修改
↓
重新判断测试结果
↓
重复 Codex 已经完成的工作
```

目标方案：

```text
Codex
   ↓
持续留下可恢复状态
   ↓
Quota exhausted
   ↓
Handoff Layer
   ↓
Claude Code
   ↓
读取已有工作状态
   ↓
继续完成任务
```

---

# 3. 核心设计原则

## 3.1 Agent 不负责自己的故障恢复

不依赖：

```text
“额度快没了，请写一个总结。”
```

因为 Agent 可能：

- quota 突然耗尽
- API 直接拒绝请求
- terminal crash
- context overflow
- network failure
- process 被 kill

因此：

> Agent 负责工作，Handoff Layer 负责记住工作。

---

# 4. 状态分为两种

## A. Deterministic State

由程序自动采集，不需要 LLM。

例如：

```text
git branch
git HEAD
git status
git diff
modified files
created files
deleted files

commands executed
test results
build results

stdout
stderr

process exit code
timestamps
```

这些内容：

**额外 LLM Token ≈ 0**

---

## B. Semantic State

只有 Agent 自己更容易知道的信息：

```text
当前目标
已经确认的结论
为什么选择这种方案
尝试过但失败的方法
目前正在解决什么问题
推荐的下一步
```

例如：

```markdown
Current objective:
Fix refresh-token race condition.

Completed:
- Reproduced the issue.
- Added regression test.
- Located race condition in refresh_token().

Decision:
Keep existing JWT library.
Do not modify public API.

Current issue:
Lock is released before token persistence completes.

Next:
Move persistence inside refresh lock and rerun auth tests.
```

这部分需要一些 Token，但应当：

> 低频更新，而不是持续更新。

---

# 5. Token 消耗策略

默认模式：

```text
LLM-generated checkpoint:
仅 milestone 时触发
```

而不是：

```text
每个 tool call
每次 edit
每次 shell command
```

推荐触发条件：

```text
完成一个明确子任务
测试状态发生重大变化
做出重要架构决定
用户主动要求 checkpoint
Agent 准备结束一次较长工作阶段
```

目标：

```text
正常 Agent workload
+
约 2%–8% checkpoint overhead
```

具体比例取决于任务长度和 checkpoint 频率。

对于长任务，handoff 后避免重复探索所节省的 Token 很可能超过 checkpoint 成本。

因此设计目标不是：

> Zero additional tokens

而是：

> Significantly cheaper than restarting the task.

---

# 6. Handoff 数据结构

工作目录：

```text
.agent-handoff/
│
├── task.json
├── state.md
├── events.jsonl
├── git.json
├── tests.json
├── commands.jsonl
└── logs/
    ├── stdout.log
    └── stderr.log
```

---

## task.json

保存任务本身：

```json
{
  "task_id": "abc123",
  "source_agent": "codex",
  "fallback_agent": "claude-code",
  "original_request": "Fix the authentication refresh bug",
  "created_at": "...",
  "status": "running"
}
```

---

## events.jsonl

Append-only journal：

```json
{"event":"agent_started","agent":"codex"}
{"event":"file_modified","path":"src/auth.py"}
{"event":"command","cmd":"pytest tests/test_auth.py"}
{"event":"test_failed","test":"test_refresh"}
{"event":"file_modified","path":"src/auth.py"}
{"event":"agent_failure","reason":"quota_exhausted"}
```

类似于：

**Write-Ahead Log**

即使进程突然死亡，已经写入的数据仍然存在。

---

## git.json

```json
{
  "branch": "fix/auth-refresh",
  "head": "7f18ca",
  "modified": [
    "src/auth.py",
    "tests/test_auth.py"
  ]
}
```

实际 diff 不一定复制进去，可以运行：

```bash
git diff
```

动态获得。

---

## state.md

Agent 可选更新的语义状态：

```markdown
# Current Objective

Fix refresh-token race condition.

# Completed

- Reproduced bug
- Added regression test
- Located race condition

# Decisions

- Keep current JWT library
- Do not change public API

# Current Problem

Refresh lock does not cover persistence operation.

# Next Suggested Step

Move persistence into lock and rerun auth tests.
```

如果最后一次 state.md 较旧：

**没关系。**

新 Agent 可以结合：

```text
state.md
+
git diff
+
events
+
test output
```

自行推断最新状态。

---

# 7. Agent Handoff Package

真正切换时，不需要调用原 Agent。

Handoff Layer 自动产生：

```text
HANDOFF
```

内容包括：

```text
Original task
Source agent
Failure reason

Last semantic checkpoint

Current repository state
Current git diff

Files modified

Recent commands

Latest test results

Last stdout/stderr

Resume instructions
```

Fallback Agent 的第一条 instruction：

```text
You are continuing an unfinished coding task from another agent.

Do not restart the task from scratch.

First inspect:
1. HANDOFF.md
2. current git diff
3. modified files
4. latest test results

Verify the current repository state before making further changes.

Continue the original task from the most advanced valid state available.
```

---

# 8. Handoff 触发原因

统一抽象：

```text
agent_unavailable
```

具体 reason：

```text
quota_exhausted
rate_limited
context_exhausted
process_crashed
provider_error
network_failure
manual_handoff
```

例如：

```json
{
  "event": "agent_unavailable",
  "agent": "codex",
  "reason": "quota_exhausted",
  "recoverable": true
}
```

---

# 9. Agent Adapter

不要把系统写死为 Codex。

统一接口：

```text
AgentAdapter
│
├── start()
├── stop()
├── detect_failure()
├── get_output()
├── resume()
└── capabilities()
```

然后：

```text
CodexAdapter
ClaudeCodeAdapter
PiAdapter
GeminiAdapter
```

总体：

```text
                Handoff Core
                     │
        ┌────────────┼────────────┐
        │            │            │
     Codex        Claude         Pi
     Adapter       Adapter      Adapter
```

这样后续支持新 Agent 时，只新增 adapter。

---

# 10. MVP — Phase 0

目标：

验证：

> Agent A → Agent B 的交接是否真的有价值。

只支持：

```text
Codex → Claude Code
```

功能：

```text
手动 handoff
```

命令示例：

```bash
handoff codex --to claude
```

插件：

1. 保存原始任务
2. 获取 git status
3. 获取 git diff
4. 获取最近日志
5. 生成 HANDOFF.md
6. 启动 Claude Code
7. 注入 resume instruction

暂时：

```text
❌ 自动 quota detection
❌ checkpoint summary
❌ 多 provider
```

成功标准：

Claude 可以明显比从零开始更快恢复任务。

---

# 11. Phase 1 — Automatic Failover

加入：

```text
quota detection
rate-limit detection
process failure detection
```

运行方式：

```bash
handoff run codex --fallback claude
```

流程：

```text
spawn Codex
↓
monitor process
↓
normal completion
→ exit

quota detected
↓
snapshot
↓
launch Claude
↓
resume
```

增加：

```text
events.jsonl
stdout/stderr logging
failure classifier
```

成功标准：

Codex 因 quota 中断后，不需要用户人工复制上下文，Claude 自动继续。

---

# 12. Phase 2 — Semantic Checkpoints

加入：

```text
state.md
```

Agent instruction：

```text
When you complete a meaningful milestone,
briefly update .agent-handoff/state.md.
```

每次摘要：

目标：

```text
<300 tokens
```

避免：

```text
完整 conversation summary
```

只记录：

```text
decision
progress
current issue
next step
```

成功标准：

Fallback Agent 不只是知道“发生过什么”，还知道“为什么”。

---

# 13. Phase 3 — Bidirectional Handoff

支持：

```text
Codex ↔ Claude Code
```

以及：

```text
manual switch
automatic failover
```

配置：

```toml
primary = "codex"
fallback = ["claude-code"]
```

之后可扩展：

```toml
fallback = [
    "claude-code",
    "pi"
]
```

---

# 14. Phase 4 — Universal Agent Adapter

新增：

```text
Pi
Gemini CLI
OpenCode
```

形成：

```text
Agent
↓
Adapter
↓
Universal Handoff State
↓
Adapter
↓
Another Agent
```

这个阶段才真正形成：

> Cross-Agent Handoff Layer

---

# 15. Phase 5 — Reliability

加入：

```text
checkpoint integrity
atomic writes
lock files
partial-write recovery
crash recovery
handoff validation
```

例如：

```text
state.tmp
↓
fsync
↓
atomic rename
↓
state.json
```

避免 handoff layer 自己 crash 后损坏 checkpoint。

---

# 16. Phase 6 — Optional Advanced Features

这些都不是 MVP：

```text
Agent selection
cost-aware routing
model-quality routing
provider health detection
multiple fallbacks
remote task state
team/shared memory
web dashboard
```

例如未来：

```text
Codex quota exhausted
↓
Claude available?
├─ yes → Claude
└─ no
   ↓
Pi + local model
```

但这些应当建立在 handoff 本身稳定之后。

---

# 17. 明确不做什么

至少早期：

```text
❌ Multi-Agent platform
❌ Agent conversation system
❌ Planner
❌ Reviewer Agent
❌ voting
❌ RAG
❌ long-term personal memory
❌ cloud account system
❌ Web UI
❌ proprietary workflow engine
```

原则：

> 一个小工具，只解决 Agent 工作中断后的可靠接力问题。

---

# 18. 项目的一句话介绍

可以暂时定义为：

**A lightweight failover layer that lets one coding agent continue another agent's unfinished work when quota, rate limits, or runtime failures interrupt the task.**

更短：

**Failover for coding agents.**

或者：

**Don't restart when your coding agent stops. Hand it off.**

---

# 19. 最核心的技术思想

整个项目最重要的不是“让两个 Agent 对话”。

而是：

```text
Agent A
      │
      │ work
      ▼
Repository + Durable Journal
      │
      X Agent A disappears
      │
      ▼
Agent B
```

换句话说：

> **我们不需要保存 Agent A 本身。**

我们需要保存的是：

> **Agent A 对现实世界已经造成的、与任务相关的有效状态变化。**

对于 Coding Agent 而言，这个“现实世界”主要就是：

```text
Repository
Git
Tests
Commands
Artifacts
Decisions
```

只要这些能够被可靠传递，另一个 Agent 就有可能继续工作。