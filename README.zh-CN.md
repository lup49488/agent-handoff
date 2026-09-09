# agent-handoff

[English README](README.md) · [项目设计说明](docs/design.md) · [MIT 许可证](LICENSE)

**Coding Agent 的故障接力层。** 当编码 Agent 遇到额度耗尽、限流、崩溃或长时间无响应时，保留已经完成的有效工作，并将任务交给下一 Agent，而不是从头开始。

> Agent 负责工作，Handoff Layer 负责记住工作。

## 目录

- [项目状态](#项目状态)
- [安装](#安装)
- [快速开始](#快速开始)
- [常用命令](#常用命令)
- [故障转移与检查点](#故障转移与检查点)
- [配置 Agent 链](#配置-agent-链)
- [可靠性、健康状态与会话读取](#可靠性健康状态与会话读取)
- [保存的内容](#保存的内容)
- [在 Agent 中使用](#在-agent-中使用)
- [开发](#开发)

## 项目状态

当前实现覆盖：

- 手动交接与受监管自动故障转移；
- Git 状态、diff、命令、日志和测试结果的确定性状态采集；
- `state.md` 语义检查点与新鲜度提示；
- Codex、Claude Code、Gemini、OpenCode、Pi 及配置式自定义 Agent；
- 双向、有序的 fallback 链；
- 原子写入、单写入者锁、中断恢复和交接包完整性校验；
- 用户级 Agent 健康状态与支持会话日志的有限错误/重置时间读取。

已验证：

- 自动化测试覆盖状态采集、故障转移、检查点、配置、锁、恢复、健康选择和会话读取；
- Codex → Claude Code，以及 Claude Code → Codex 的额度耗尽交接已在真实环境人工验证；
- Codex 和 Claude Code 的内置启动命令已在本机核对。Gemini、OpenCode、Pi 仍应在您的环境中先做小任务验证。

本项目明确不做 Multi-Agent 协作平台、规划器、Web 仪表板、远程状态服务或共享长期记忆。

## 安装

```bash
pip install .
```

如果 Windows 中因为项目路径含非 ASCII 字符导致 editable install 失败，可以直接从源码运行：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m agent_handoff --help
```

## 快速开始

在 Agent 开始工作前初始化任务，再使用受监管模式启动主 Agent：

```bash
handoff init "修复认证刷新问题" --from codex --to claude
handoff run
```

主 Agent 发生可识别故障时，工具会采集仓库状态、生成 `HANDOFF.md`，并启动下一个可用 Agent。正常退出会将任务标记为完成；只有非零退出或配置的无输出超时才会触发故障转移。

手动切换：

```bash
handoff switch --to claude --reason quota_exhausted
```

`--no-launch` 只准备交接包，不启动下一 Agent；`--dry-run` 只显示即将执行的命令，不改写状态。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `handoff status` | 查看任务、检查点、测试结果与最近事件。 |
| `handoff checkpoint --next "..."` | 在里程碑记录进度、决策、问题与下一步。 |
| `handoff tests "pytest -q" --exit-code 0` | 将最新测试结果写入交接包。 |
| `handoff pack --to claude` | 不启动 Agent，仅重新生成 `HANDOFF.md`。 |
| `handoff agents -v` | 查看已安装 Agent 和实际启动命令。 |
| `handoff config --init` | 创建项目级 fallback 链配置。 |
| `handoff health` | 查看用户级 Agent 冷却状态；`--refresh` 更新支持的会话日志信息。 |
| `handoff sessions` | 查看从本地会话中提取的有限故障和重置信息。 |
| `handoff verify` / `handoff recover` | 检查或恢复中断留下的状态。 |

## 故障转移与检查点

`handoff run` 会保存 stdout/stderr，并在非零退出时识别下列情形：额度耗尽、限流、上下文耗尽、Provider 错误、网络错误、进程崩溃和 Agent 静默超时。

检查点只在有意义的里程碑写入，而不是每次工具调用后写入。它应说明：当前目标、已完成内容、关键决策、当前问题和建议下一步。这样下一 Agent 能理解“为什么”，而 Git diff 和日志则提供“发生了什么”。

## 配置 Agent 链

在 `.agent-handoff/config.toml` 或项目根目录的 `handoff.toml` 中配置：

```toml
primary = "codex"
fallback = ["claude-code", "pi"]

# 让工作中的 Agent 在有意义的里程碑更新 state.md。
checkpoint_protocol = true

# 总静默时间超过此值时视为卡住；0 表示无限等待。
stall_timeout_seconds = 900

# 最近发生额度/限流故障的 Agent 会后置，但不会永久移除。
health = true

[agents.my-agent]
executable = "my-agent"
exec = ["--non-interactive", "{prompt}"]
interactive = ["{prompt}"]
```

链路可以双向配置。例如 Claude Code → Codex：

```toml
primary = "claude-code"
fallback = ["codex"]
```

## 可靠性、健康状态与会话读取

- 状态文件采用“写入 → flush → fsync → 原子替换”，避免半写入覆盖已存在状态。
- JSONL 日志可跳过进程被杀死留下的最后一条半行记录。
- 写操作会串行化；受监管运行期间，Agent 仍可以写入检查点。
- 只有 `HANDOFF.md` 通过完整性校验，才会启动 fallback Agent。
- 健康状态是用户级数据：冷却中的 Agent 会移到链尾，但不会永久禁止，以免估计错误导致任务无 Agent 可用。
- 会话读取只提取当前项目的失败消息和限流遥测；不会复制对话、命令或 diff。

## 保存的内容

```text
.agent-handoff/
├── task.json           任务与交接状态
├── git.json            分支、提交、变更与 diffstat
├── events.jsonl        追加式事件日志
├── commands.jsonl      已记录的命令与退出码
├── tests.json          最近一次测试结果
├── state.md            可选的语义检查点
├── checkpoints.jsonl   检查点新鲜度信息
├── logs/               受监管 Agent 的 stdout/stderr
├── lock, run.lock      短写入锁与受监管会话锁
└── config.toml         可选项目配置
```

`HANDOFF.md` 生成在项目根目录。运行状态、日志、构建产物和 `HANDOFF.md` 本身均由 `.gitignore` 排除，不会进入源代码提交。

## 在 Agent 中使用

CLI 本身就是完整的工具，但 Agent 还需要知道*何时*该用它。`skills/agent-handoff/`
下的 Skill 承载这份判断：对实质性的仓库工作启用跟踪、在关键节点写检查点、
未经用户明确授权不启动其他 Agent。

**Claude Code** — 在桌面端或 CLI 中把本仓库作为插件安装：

```text
/plugin marketplace add lup49488/agent-handoff
/plugin install agent-handoff@agent-handoff
```

也可以直接复制：`~/.claude/skills/` 对所有项目生效，项目内的 `.claude/skills/`
只对该项目生效。

```bash
cp -r skills/agent-handoff ~/.claude/skills/agent-handoff
```

**Codex** — 把同一个目录复制到 `~/.codex/skills/`。Codex 读取相同的
`SKILL.md`；`agents/openai.yaml` 提供它界面上显示的名称、简介和默认提示词。

```bash
cp -r skills/agent-handoff ~/.codex/skills/agent-handoff
```

两种方式都假定 `handoff` 已在 `PATH` 上；若不在，Skill 会按[安装](#安装)一节
所述从源码目录运行模块。

## 开发

```bash
python -m pytest -q
```

测试配置会从 `PATH` 隐藏真实 Coding Agent CLI，因此测试不会意外消耗额度或启动真实 Agent。

## 许可证

本项目采用 [MIT License](LICENSE)，版权所有者为 Pinjia Lu。
