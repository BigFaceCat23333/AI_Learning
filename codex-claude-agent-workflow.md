# Codex + Claude 双终端协作流程

## 目标

左边运行 Codex CLI，右边运行 Claude CLI。

Codex 负责：

- 分析需求
- 生成实现计划
- 审查 git diff
- 提出修改建议
- 最终验收

Claude 负责：

- 按 Codex 计划修改代码
- 补充测试
- 根据 Codex 审查意见继续修正

共享文件：

```text
.agents/PLAN.md
.agents/CLAUDE_RESULT.md
.agents/CODEX_REVIEW.md
```

## 启动双终端

进入项目目录：

```bash
cd /Users/urpapa/code/proj/pyProj/ai-learning
```

启动 tmux：

```bash
tmux new-session -s agents -n work
tmux split-window -h
```

左边运行：

```bash
codex
```

右边运行：

```bash
claude
```

切换左右 pane：

```text
Ctrl-b 松开 ←
Ctrl-b 松开 →
```

查看 pane 编号：

```bash
./agent-loop.sh panes
```

常见编号：

```text
Codex:  agents:0.0
Claude: agents:0.1
```

## 开始新任务前清理

如果上一轮已经完成，并且要保留记录：

```bash
./agent-loop.sh archive
```

如果只是测试流程，不需要保留：

```bash
./agent-loop.sh reset
```

## 第一步：让 Codex 生成计划

在左边 Codex 里输入：

```text
请分析这个需求，生成给 Claude 执行的实现计划。
不要改代码。
把计划写入 .agents/PLAN.md。
计划要包含：目标、影响文件、实现步骤、测试建议、注意事项。
```

Codex 完成后会写入：

```text
.agents/PLAN.md
```

## 第二步：把计划发给 Claude

在左边终端执行：

```bash
./agent-loop.sh send-plan agents:0.1
```

如果默认 pane 配对正确，也可以：

```bash
./agent-loop.sh send-plan
```

Claude 会收到：

```text
请读取 .agents/PLAN.md，按里面的要求处理。
完成后把改动摘要、测试结果、风险点写入 .agents/CLAUDE_RESULT.md。
```

如果希望发送计划后自动等待 Claude 完成，并自动触发 Codex 审查，可以直接执行：

```bash
./agent-loop.sh send-plan-and-wait agents:0.1 agents:0.0
```

## 第三步：等待 Claude 完成并自动触发 Codex 审查

如果第二步只执行了 `send-plan`，发送计划后需要继续执行：

```bash
./agent-loop.sh wait-review agents:0.0
```

它会等待：

```text
.agents/CLAUDE_RESULT.md
```

被 Claude 更新。更新后自动把审查指令发给 Codex：

```text
审查当前 git diff 和 .agents/CLAUDE_RESULT.md，
判断是否满足 .agents/PLAN.md，
不要直接改代码，
把问题和修改建议写入 .agents/CODEX_REVIEW.md。
```

## 第四步：Codex 审查

Codex 会读取：

```text
.agents/PLAN.md
.agents/CLAUDE_RESULT.md
git diff
```

然后把审查结果写入：

```text
.agents/CODEX_REVIEW.md
```

如果 Codex 判断通过，可以进入最终验收或提交。

如果 Codex 发现问题，继续下一步。

## 第五步：把 Codex 审查意见发回 Claude

执行：

```bash
./agent-loop.sh send-review agents:0.1
```

或者：

```bash
./agent-loop.sh send-review
```

Claude 会读取：

```text
.agents/CODEX_REVIEW.md
```

然后继续修改代码，并更新：

```text
.agents/CLAUDE_RESULT.md
```

## 第六步：再次等待 Codex 审查

执行：

```bash
./agent-loop.sh wait-review agents:0.0
```

之后重复：

```text
Codex 审查 -> 写 CODEX_REVIEW.md
Claude 修正 -> 写 CLAUDE_RESULT.md
Codex 再审查
```

直到 Codex 判断通过。

## 辅助命令

查看 Claude 最近输出：

```bash
./agent-loop.sh capture agents:0.1
```

抓更多行：

```bash
AGENT_CAPTURE_LINES=1000 ./agent-loop.sh capture agents:0.1
```

查看当前代码改动：

```bash
./agent-loop.sh diff
```

手动触发 Codex 审查：

```bash
./agent-loop.sh send-codex-review agents:0.0
```

清空当前协作文件：

```bash
./agent-loop.sh reset
```

归档当前协作文件并清空：

```bash
./agent-loop.sh archive
```

## 推荐完整命令顺序

开始新任务前：

```bash
./agent-loop.sh archive
```

在 Codex 里生成 `.agents/PLAN.md` 后：

```bash
./agent-loop.sh send-plan agents:0.1
./agent-loop.sh wait-review agents:0.0
```

如果 Codex 审查发现问题：

```bash
./agent-loop.sh send-review agents:0.1
./agent-loop.sh wait-review agents:0.0
```

通过后：

```bash
./agent-loop.sh archive
```

## 注意点

- `wait-review` 不是检测 Claude 是否思考结束，而是检测 `.agents/CLAUDE_RESULT.md` 是否更新。
- Claude 必须按要求写 `.agents/CLAUDE_RESULT.md`，否则 `wait-review` 不会继续。
- 新任务开始前必须 `reset` 或 `archive`，避免旧内容污染新任务。
- Codex 不直接等待 Claude，两个终端是独立运行的。
- 如果 pane 编号变了，用 `./agent-loop.sh panes` 重新确认。
- tmux 里看历史输出，用 `Ctrl-b [`，不要依赖鼠标滚轮。

## 一句话流程

```text
Codex 写计划 -> 脚本发给 Claude -> Claude 改代码并写结果 ->
脚本自动叫 Codex review -> Codex 写 review ->
脚本发回 Claude 修正 -> 反复直到 Codex 通过 -> archive
```
