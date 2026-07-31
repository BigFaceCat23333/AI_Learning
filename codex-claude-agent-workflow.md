# Codex + Claude 自动协作

本流程仅用于：

```text
/Users/urpapa/code/proj/pyProj/ai-learning
```

Codex 和 Claude 的钩子、状态及控制器都是项目级配置，不修改用户全局配置。

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

停止只会终止后台等待，不会清空计划、结果或当前轮次。再次执行 `./agent` 会从保留的状态继续。

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

左侧运行 Codex：

```bash
codex
```

右侧运行 Claude：

```bash
claude
```

默认面板：

```text
Codex:  agents:work.0
Claude: agents:work.1
```

`./agent` 会创建一个独立的后台窗口：

```text
agents:controller
```

控制器独立运行，因此不会阻塞 Codex 接收审查指令。完成、停止或失败后，该窗口会自动关闭。

### 信任项目钩子

首次启动或 `.codex/hooks.json`、钩子脚本发生变化后，在 Codex CLI 中执行：

```text
/hooks
```

确认来源为当前项目的 `.codex/hooks.json`，检查内容后信任。Codex 只会在项目受信任时加载项目钩子。

Claude 使用当前项目的 `.claude/settings.local.json`。修改配置后重新启动该项目的 Claude CLI。

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
.agents/CLAUDE_RESULT.done
.agents/CODEX_REVIEW.done
```

Claude 的项目级 `Stop` 钩子会确认结果文件包含当前 `RUN_ID` 和 `ROUND`。Codex 的项目级 `Stop` 钩子还会要求以下结论之一：

```text
REVIEW_STATUS: PASS
REVIEW_STATUS: CHANGES_REQUIRED
```

因此控制器不会因为 Agent 暂停输出就误判任务完成。

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

每个等待阶段默认最多 1800 秒，最多自动修正 10 轮：

```bash
export AGENT_WAIT_TIMEOUT_SECONDS=1800
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
.codex/hooks.json                  Codex 项目级 Stop 钩子
.claude/settings.local.json        Claude 项目级 Stop 钩子
.agents/hooks/agent-stop.sh        两端共用的完成校验
.agents/controller.log             后台控制器日志
```

`agent-loop.sh` 保留原有细粒度命令以便排错，但日常流程不要直接操作它。

## 常见问题

### 提示没有 PLAN.md

先让 Codex 分析需求并写入 `.agents/PLAN.md`，再执行 `./agent`。

### 找不到 tmux 会话

确认 Codex 和 Claude 已经运行在名为 `agents` 的 tmux 会话中。

### 一直等待 Claude 或 Codex

依次检查：

```bash
./agent status
./agent logs
```

然后确认：

- Codex 已通过 `/hooks` 信任项目钩子。
- Claude 已重启并加载项目设置。
- 两个面板名称和编号与默认配置一致。
- 对应结果文件包含当前 `RUN_ID` 和 `ROUND`。
