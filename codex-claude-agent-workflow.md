# Codex + Claude 自动协作

本流程仅用于：

```text
/Users/urpapa/code/proj/pyProj/ai-learning
```

Codex 和 Claude 的钩子、状态及控制器都是项目级配置，不修改用户全局配置。Claude 的实现过程和 Codex 的审查过程都会显示在各自的 tmux 面板中。

## 日常使用

不需要记脚本名称和内部命令。直接在 Codex 中说：

```text
分析这个需求，交给 Claude 实现并自动审查。
```

也可以说：

```text
开始双 Agent 协作。
```

项目根目录的 `AGENTS.md` 会要求 Codex 自动完成两件事：

1. 分析需求并写入 `.agents/PLAN.md`。
2. 执行 `./agent` 启动后台控制器。

后台控制器会自动运行：

```text
Codex 生成计划
→ Claude 实现并测试
→ Codex 审查
→ 如果有问题，Claude 自动修正
→ Codex 再审查
→ 循环直到 PASS
→ 自动归档
```

## 仅需记住的三个入口

通常不需要手工执行启动命令。如果需要，可以直接运行：

```bash
./agent
```

查看状态：

```bash
./agent status
```

停止后台控制器：

```bash
./agent stop
```

停止只终止后台控制器，不会关闭可视化 Agent 面板，也不会清空计划、结果或当前轮次。再次执行 `./agent` 会根据接受标记和完成标记恢复等待或重新投递。

发生问题时查看日志：

```bash
./agent logs
```

## 首次准备

进入项目并创建左右两个 tmux 面板：

```bash
cd /Users/urpapa/code/proj/pyProj/ai-learning
tmux new-session -s agents -n work
tmux split-window -h
```

左侧运行 `codex`，右侧运行 `claude`：

```text
Codex:  agents:work.0
Claude: agents:work.1
```

`./agent` 会在同一个 tmux 会话中创建后台控制器窗口：

```text
agents:controller
```

控制器会自动向两个面板粘贴并提交提示。Claude 的代码修改和测试过程显示在 Claude 面板，Codex 的审查和测试过程显示在 Codex 面板，不需要人工按 Enter。

每个阶段完成或失败时，控制器会把相关面板最近输出抓取到同一个日志文件：

```text
.agents/controller.log
```

控制器自己的状态消息也写入该文件，不拆分其他日志。完成、停止或失败后，控制器窗口会自动关闭。

### 项目钩子

Claude 和 Codex 都必须加载项目级 Stop Hook。Hook 会在 Agent 真正停止且结果文件合格后写入完成标记，控制器负责持续监控标记并推进流程。

Codex 面板首次启动或 `.codex/hooks.json`、钩子脚本发生变化后，需要执行：

```text
/hooks
```

确认来源为当前项目的 `.codex/hooks.json`，检查内容后信任。Hook 写完成标记，但不直接修改状态。

Claude 使用当前项目的 `.claude/settings.local.json`，修改后需要重新启动 Claude 面板。

## 自动流程如何判断完成

共享业务文件：

```text
.agents/PLAN.md
.agents/CLAUDE_RESULT.md
.agents/CODEX_REVIEW.md
```

状态文件：

```text
.agents/RUN_ID
.agents/ROUND
.agents/STATE
.agents/CLAUDE_STARTED
.agents/CODEX_STARTED
.agents/CLAUDE_RESULT.done
.agents/CODEX_REVIEW.done
.agents/ACTIVE_AGENT
.agents/ACTIVE_PANE
.agents/ACTIVE_PID
.agents/ACTIVE_STARTED_AT
.agents/LAST_EXIT_CODE
.agents/LAST_ERROR
```

控制器投递提示后，Agent 第一条动作会写入 `CLAUDE_STARTED` 或 `CODEX_STARTED`。控制器在短时间内没有看到接受标记时，会再次发送 Enter；多次重试仍没有接受标记就快速失败，不再盲等 1800 秒。

Agent 完成后，Stop Hook 校验结果文件包含当前 `RUN_ID` 和 `ROUND`。Codex 审查文件还必须包含以下结论之一：

```text
REVIEW_STATUS: PASS
REVIEW_STATUS: CHANGES_REQUIRED
```

Hook 校验通过后才写入对应 `.done` 标记，控制器看到当前轮次标记后推进状态。因此不会因为提示停留在输入框、Agent 仍在工作或旧结果文件残留而误判任务完成。

`./agent status` 还会显示当前 Agent、PID、进程状态、启动时间、退出码和最近错误。

## 自动归档

Codex 审查通过后，控制器自动归档本轮文件：

```text
.agents/archive/<时间戳>/
```

随后活动状态恢复为 `idle`。`./agent status` 会显示最近结果和归档位置：

```text
LAST_STATUS: PASS
LAST_ARCHIVE: .agents/archive/...
```

## 超时和轮数

提示接受默认等待 60 秒、完成默认等待 1800 秒，最多自动修正 10 轮：

```bash
export AGENT_WAIT_TIMEOUT_SECONDS=1800
export AGENT_DISPATCH_TIMEOUT_SECONDS=60
export AGENT_MAX_ROUNDS=10
```

超时或达到最大轮数后，控制器退出但保留现场：

```bash
./agent status
./agent logs
```

排除临时问题后，再执行 `./agent` 可以继续当前状态。

## 项目级文件

```text
AGENTS.md                          Codex 自然语言触发约定
agent                              面向用户的唯一入口
agent-loop.sh                      内部状态机和排错命令
.codex/hooks.json                  Codex 项目级 Stop 结果约束
.claude/settings.local.json        Claude 项目级 Stop 结果约束
.agents/hooks/agent-stop.sh        两端共用的停止前校验
.agents/controller.log             控制器及两个 Agent 的统一日志
```

`agent-loop.sh` 是内部状态机，日常流程不要直接操作它。

## 常见问题

### 提示没有 PLAN.md

先让 Codex 分析需求并写入 `.agents/PLAN.md`，再执行 `./agent`。

### 找不到 tmux 会话或面板

确认 `agents:work.0` 正在运行 Codex，`agents:work.1` 正在运行 Claude；如果面板位置不同，使用 `AGENT_CODEX_PANE` 和 `AGENT_CLAUDE_PANE` 覆盖。

### 一直等待 Claude 或 Codex

依次检查：

```bash
./agent status
./agent logs
```

然后确认：

- `ACTIVE_PANE` 是否指向预期的 Codex/Claude 面板。
- `CLAUDE_STARTED` 或 `CODEX_STARTED` 是否为当前 `RUN_ID:ROUND`。
- `LAST_EXIT_CODE` 和 `LAST_ERROR` 是否记录进程或校验失败。
- 两个面板是否仍停留在权限确认、输入框或其他交互界面。
- Codex 是否已信任项目 Hook，Claude 是否已重新加载项目设置。
- 对应结果文件是否包含当前 `RUN_ID` 和 `ROUND`。
- Codex 审查文件是否包含有效的 `REVIEW_STATUS`。
