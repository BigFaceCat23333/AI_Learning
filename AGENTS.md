# 项目协作约定

## Codex + Claude 自动协作

当用户表达以下意图时，使用本项目的双 Agent 自动协作流程：

- “交给 Claude 实现并自动审查”
- “开始双 Agent 协作”
- “让 Claude 修改，Codex 审查”
- 其他语义等价的明确要求

执行规则：

1. Codex 先分析需求，不直接修改业务代码。
2. 把实现计划写入项目根目录 `.agents/PLAN.md`，至少包含目标、影响文件、实现步骤、测试建议和注意事项。
3. 执行项目根目录的 `./agent`。该命令只负责启动后台控制器，会立即返回；不要直接运行或等待 `agent-loop.sh` 的内部循环命令。
4. 告知用户双 Agent 协作已经在后台启动，可使用 `./agent status` 查看状态。
5. 后台控制器会自动完成 Claude 实现、Codex 审查、Claude 修正和再次审查，直到通过或达到失败条件。

当用户询问协作进度时，运行：

```bash
./agent status
```

当用户要求停止协作时，运行：

```bash
./agent stop
```

只有排查故障时才使用：

```bash
./agent logs
```

`agent-loop.sh` 是内部实现，不要求用户记忆或直接操作。
