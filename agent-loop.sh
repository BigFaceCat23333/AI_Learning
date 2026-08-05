#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_PROJECT_ROOT="/Users/urpapa/code/proj/pyProj/ai-learning"
PROJECT_ROOT="$SCRIPT_DIR"
AGENTS_DIR="${PROJECT_ROOT}/.agents"

CLAUDE_PANE="${AGENT_CLAUDE_PANE:-agents:work.1}"
CODEX_PANE="${AGENT_CODEX_PANE:-agents:work.0}"
CAPTURE_LINES="${AGENT_CAPTURE_LINES:-300}"
POLL_SECONDS="${AGENT_POLL_SECONDS:-5}"
DISPATCH_TIMEOUT_SECONDS="${AGENT_DISPATCH_TIMEOUT_SECONDS:-60}"
DISPATCH_RETRY_SECONDS="${AGENT_DISPATCH_RETRY_SECONDS:-10}"
DISPATCH_MAX_RETRIES="${AGENT_DISPATCH_MAX_RETRIES:-3}"
WAIT_TIMEOUT_SECONDS="${AGENT_WAIT_TIMEOUT_SECONDS:-1800}"
MAX_ROUNDS="${AGENT_MAX_ROUNDS:-10}"

if [[ "$PROJECT_ROOT" != "$EXPECTED_PROJECT_ROOT" ]]; then
  echo "This project-level script must stay at ${EXPECTED_PROJECT_ROOT}." >&2
  exit 1
fi

cd "$PROJECT_ROOT"

usage() {
  cat <<'EOF'
Usage:
  ./agent-loop.sh controller
  ./agent-loop.sh status
  ./agent-loop.sh panes
  ./agent-loop.sh capture [pane]
  ./agent-loop.sh diff
  ./agent-loop.sh reset
  ./agent-loop.sh archive

Environment:
  AGENT_CLAUDE_PANE              visible Claude pane; default agents:work.1
  AGENT_CODEX_PANE               visible Codex pane; default agents:work.0
  AGENT_CAPTURE_LINES            lines captured into controller.log; default 300
  AGENT_POLL_SECONDS             marker polling interval; default 5
  AGENT_DISPATCH_TIMEOUT_SECONDS prompt acceptance timeout; default 60
  AGENT_DISPATCH_RETRY_SECONDS   Enter retry interval; default 10
  AGENT_DISPATCH_MAX_RETRIES     maximum extra Enter presses; default 3
  AGENT_WAIT_TIMEOUT_SECONDS     per-agent completion timeout; default 1800
  AGENT_MAX_ROUNDS               maximum automatic revision rounds; default 10
EOF
}

validate_positive_integer() {
  local name="$1"
  local value="$2"

  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "${name} must be a positive integer." >&2
    exit 1
  fi
}

validate_settings() {
  validate_positive_integer "AGENT_CAPTURE_LINES" "$CAPTURE_LINES"
  validate_positive_integer "AGENT_POLL_SECONDS" "$POLL_SECONDS"
  validate_positive_integer "AGENT_DISPATCH_TIMEOUT_SECONDS" "$DISPATCH_TIMEOUT_SECONDS"
  validate_positive_integer "AGENT_DISPATCH_RETRY_SECONDS" "$DISPATCH_RETRY_SECONDS"
  validate_positive_integer "AGENT_DISPATCH_MAX_RETRIES" "$DISPATCH_MAX_RETRIES"
  validate_positive_integer "AGENT_MAX_ROUNDS" "$MAX_ROUNDS"
  if [[ ! "$WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "AGENT_WAIT_TIMEOUT_SECONDS must be a non-negative integer." >&2
    exit 1
  fi
}

read_single_line() {
  local file="$1"
  local value=""

  if [[ -r "$file" ]]; then
    IFS= read -r value < "$file" || true
  fi
  printf '%s' "$value"
}

write_single_line() {
  printf '%s\n' "$2" > "$1"
}

ensure_agents_dir() {
  mkdir -p "$AGENTS_DIR"
  touch \
    "$AGENTS_DIR/PLAN.md" \
    "$AGENTS_DIR/CODEX_REVIEW.md" \
    "$AGENTS_DIR/CLAUDE_RESULT.md" \
    "$AGENTS_DIR/RUN_ID" \
    "$AGENTS_DIR/ROUND" \
    "$AGENTS_DIR/STATE" \
    "$AGENTS_DIR/CLAUDE_STARTED" \
    "$AGENTS_DIR/CODEX_STARTED" \
    "$AGENTS_DIR/CLAUDE_RESULT.done" \
    "$AGENTS_DIR/CODEX_REVIEW.done" \
    "$AGENTS_DIR/ACTIVE_AGENT" \
    "$AGENTS_DIR/ACTIVE_PANE" \
    "$AGENTS_DIR/ACTIVE_PID" \
    "$AGENTS_DIR/ACTIVE_STARTED_AT" \
    "$AGENTS_DIR/LAST_EXIT_CODE" \
    "$AGENTS_DIR/LAST_ERROR" \
    "$AGENTS_DIR/CONTROLLER" \
    "$AGENTS_DIR/LAST_STATUS" \
    "$AGENTS_DIR/LAST_ARCHIVE" \
    "$AGENTS_DIR/controller.log"

  if [[ ! -s "$AGENTS_DIR/STATE" ]]; then
    write_single_line "$AGENTS_DIR/STATE" "idle"
  fi
}

set_state() {
  write_single_line "$AGENTS_DIR/STATE" "$1"
}

clear_active_agent() {
  : > "$AGENTS_DIR/ACTIVE_AGENT"
  : > "$AGENTS_DIR/ACTIVE_PANE"
  : > "$AGENTS_DIR/ACTIVE_PID"
  : > "$AGENTS_DIR/ACTIVE_STARTED_AT"
}

clear_run_diagnostics() {
  clear_active_agent
  : > "$AGENTS_DIR/LAST_EXIT_CODE"
  : > "$AGENTS_DIR/LAST_ERROR"
}

initialize_agent_output() {
  local agent_kind="$1"

  case "$agent_kind" in
    claude)
      : > "$AGENTS_DIR/CLAUDE_RESULT.md"
      : > "$AGENTS_DIR/CLAUDE_STARTED"
      : > "$AGENTS_DIR/CLAUDE_RESULT.done"
      ;;
    codex)
      : > "$AGENTS_DIR/CODEX_REVIEW.md"
      : > "$AGENTS_DIR/CODEX_STARTED"
      : > "$AGENTS_DIR/CODEX_REVIEW.done"
      ;;
    *)
      echo "Unknown agent kind: ${agent_kind}" >&2
      return 1
      ;;
  esac
}

current_token() {
  local run_id
  local round

  run_id="$(read_single_line "$AGENTS_DIR/RUN_ID")"
  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  if [[ -z "$run_id" || -z "$round" ]]; then
    return 1
  fi
  printf '%s:%s' "$run_id" "$round"
}

require_active_round() {
  if ! current_token >/dev/null; then
    echo "No active run. Start the controller with a non-empty plan first." >&2
    exit 1
  fi
}

pane_exists() {
  tmux display-message -p -t "$1" '#{pane_id}' >/dev/null 2>&1
}

pane_pid() {
  tmux display-message -p -t "$1" '#{pane_pid}' 2>/dev/null || true
}

require_agent_panes() {
  if ! pane_exists "$CLAUDE_PANE"; then
    echo "Claude pane does not exist: ${CLAUDE_PANE}" >&2
    return 1
  fi
  if ! pane_exists "$CODEX_PANE"; then
    echo "Codex pane does not exist: ${CODEX_PANE}" >&2
    return 1
  fi
}

begin_run() {
  local run_id

  ensure_agents_dir
  if [[ ! -s "$AGENTS_DIR/PLAN.md" ]]; then
    echo "File is empty or missing: .agents/PLAN.md" >&2
    exit 1
  fi

  run_id="$(date +%Y%m%d-%H%M%S)-$$"
  write_single_line "$AGENTS_DIR/RUN_ID" "$run_id"
  write_single_line "$AGENTS_DIR/ROUND" "1"
  initialize_agent_output "claude"
  initialize_agent_output "codex"
  clear_run_diagnostics
  write_single_line "$AGENTS_DIR/LAST_STATUS" "RUNNING"
  set_state "claude_working"
}

prepare_revision_round() {
  local token
  local round

  require_active_round
  token="$(current_token)"
  if [[ "$(read_single_line "$AGENTS_DIR/CODEX_REVIEW.done")" != "$token" ]]; then
    write_single_line "$AGENTS_DIR/LAST_ERROR" "当前 Codex 审查尚未通过 Hook 校验。"
    return 1
  fi
  if grep -Fqx 'REVIEW_STATUS: PASS' "$AGENTS_DIR/CODEX_REVIEW.md"; then
    write_single_line "$AGENTS_DIR/LAST_ERROR" "当前审查已经通过，无需开始修正轮次。"
    return 1
  fi

  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  write_single_line "$AGENTS_DIR/ROUND" "$((round + 1))"
  # 保留上一轮 CODEX_REVIEW.md，供 Claude 在修正轮次中读取；
  # Claude 完成后、下一轮 Codex 审查开始前再初始化 Codex 输出。
  initialize_agent_output "claude"
  clear_run_diagnostics
  write_single_line "$AGENTS_DIR/LAST_STATUS" "RUNNING"
  set_state "claude_working"
}

send_prompt_to_pane() {
  local pane="$1"
  local message="$2"

  tmux set-buffer "$message"
  tmux paste-buffer -t "$pane"
  tmux send-keys -t "$pane" Enter
}

mark_active_agent() {
  local agent_kind="$1"
  local pane="$2"

  write_single_line "$AGENTS_DIR/ACTIVE_AGENT" "$agent_kind"
  write_single_line "$AGENTS_DIR/ACTIVE_PANE" "$pane"
  write_single_line "$AGENTS_DIR/ACTIVE_PID" "$(pane_pid "$pane")"
  write_single_line "$AGENTS_DIR/ACTIVE_STARTED_AT" "$(date +%s)"
  : > "$AGENTS_DIR/LAST_EXIT_CODE"
  : > "$AGENTS_DIR/LAST_ERROR"
}

capture_pane_to_log() {
  local agent_kind="$1"
  local pane="$2"

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${agent_kind} pane capture (${pane}) begin"
  tmux capture-pane -t "$pane" -p -S "-${CAPTURE_LINES}" 2>&1 || true
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${agent_kind} pane capture (${pane}) end"
}

wait_for_dispatch_ack() {
  local marker_file="$1"
  local agent_kind="$2"
  local pane="$3"
  local expected_token
  local started
  local retries=0
  local next_retry

  expected_token="$(current_token)"
  started=$SECONDS
  next_retry="$DISPATCH_RETRY_SECONDS"
  echo "Waiting for ${agent_kind} prompt acceptance (${expected_token})..."

  while true; do
    if [[ "$(read_single_line "$marker_file")" == "$expected_token" ]]; then
      echo "${agent_kind} accepted the prompt (${expected_token})."
      return 0
    fi
    if ! pane_exists "$pane"; then
      write_single_line "$AGENTS_DIR/LAST_ERROR" "${agent_kind} 面板已不存在：${pane}。"
      return 1
    fi
    if (( SECONDS - started >= DISPATCH_TIMEOUT_SECONDS )); then
      write_single_line "$AGENTS_DIR/LAST_EXIT_CODE" "124"
      write_single_line "$AGENTS_DIR/LAST_ERROR" "${agent_kind} 提示在 ${DISPATCH_TIMEOUT_SECONDS} 秒内未确认执行，可能仍停留在输入框或被交互界面阻塞。"
      capture_pane_to_log "$agent_kind" "$pane"
      return 1
    fi
    if (( retries < DISPATCH_MAX_RETRIES && SECONDS - started >= next_retry )); then
      retries=$((retries + 1))
      echo "${agent_kind} has not acknowledged the prompt; retrying Enter (${retries}/${DISPATCH_MAX_RETRIES})."
      tmux send-keys -t "$pane" Enter
      next_retry=$((next_retry + DISPATCH_RETRY_SECONDS))
    fi
    sleep "$POLL_SECONDS"
  done
}

wait_for_completion_marker() {
  local marker_file="$1"
  local agent_kind="$2"
  local pane="$3"
  local expected_token
  local started

  expected_token="$(current_token)"
  started=$SECONDS
  echo "Waiting for ${agent_kind} completion (${expected_token})..."

  while true; do
    if [[ "$(read_single_line "$marker_file")" == "$expected_token" ]]; then
      write_single_line "$AGENTS_DIR/LAST_EXIT_CODE" "0"
      capture_pane_to_log "$agent_kind" "$pane"
      clear_active_agent
      return 0
    fi
    if ! pane_exists "$pane"; then
      write_single_line "$AGENTS_DIR/LAST_ERROR" "${agent_kind} 执行期间面板已不存在：${pane}。"
      return 1
    fi
    if (( WAIT_TIMEOUT_SECONDS > 0 && SECONDS - started >= WAIT_TIMEOUT_SECONDS )); then
      write_single_line "$AGENTS_DIR/LAST_EXIT_CODE" "124"
      write_single_line "$AGENTS_DIR/LAST_ERROR" "等待 ${agent_kind} 完成超过 ${WAIT_TIMEOUT_SECONDS} 秒。"
      capture_pane_to_log "$agent_kind" "$pane"
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

build_claude_prompt() {
  local run_id
  local round
  local token
  local source_file
  local action

  run_id="$(read_single_line "$AGENTS_DIR/RUN_ID")"
  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  token="$(current_token)"
  if (( round == 1 )); then
    source_file=".agents/PLAN.md"
    action="实现计划中的需求"
  else
    source_file=".agents/CODEX_REVIEW.md"
    action="修正 Codex 审查指出的问题"
  fi

  printf '%s' "当前是双 Agent 自动协作任务。第一步必须立即使用文件写入或编辑工具，把 ${token} 作为唯一一行写入 .agents/CLAUDE_STARTED，用它确认你已开始处理；不要只显示待执行命令，也不要等待人工操作。然后读取 ${source_file}，自主执行代码修改和必要测试，${action}。需要运行的安全项目命令请直接执行，不要等待人工复制命令。当前协作标识：RUN_ID=${run_id}，ROUND=${round}。完成后把改动摘要、测试结果和风险点写入 .agents/CLAUDE_RESULT.md，并保留两个独立行：RUN_ID: ${run_id} 和 ROUND: ${round}。不要提交 Git。"
}

build_codex_prompt() {
  local run_id
  local round
  local token

  run_id="$(read_single_line "$AGENTS_DIR/RUN_ID")"
  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  token="$(current_token)"
  printf '%s' "当前是双 Agent 自动协作审查任务。第一步必须立即使用文件写入或编辑工具，把 ${token} 作为唯一一行写入 .agents/CODEX_STARTED，用它确认你已开始处理；不要只显示待执行命令，也不要等待人工操作。然后审查当前 git diff 和 .agents/CLAUDE_RESULT.md，判断是否满足 .agents/PLAN.md。自主执行必要的检查和测试，不要等待人工复制命令，不要修改业务代码，也不要提交 Git。把问题和修改建议写入 .agents/CODEX_REVIEW.md。文件必须包含独立行 RUN_ID: ${run_id}、ROUND: ${round}，并用 REVIEW_STATUS: PASS 或 REVIEW_STATUS: CHANGES_REQUIRED 表示结论。"
}

dispatch_and_wait_claude() {
  local token
  local prompt

  token="$(current_token)"
  if [[ "$(read_single_line "$AGENTS_DIR/CLAUDE_RESULT.done")" == "$token" ]]; then
    clear_active_agent
    return 0
  fi

  mark_active_agent "claude" "$CLAUDE_PANE"
  if [[ "$(read_single_line "$AGENTS_DIR/CLAUDE_STARTED")" != "$token" ]]; then
    prompt="$(build_claude_prompt)"
    echo "Dispatching round $(read_single_line "$AGENTS_DIR/ROUND") to visible Claude pane ${CLAUDE_PANE}."
    send_prompt_to_pane "$CLAUDE_PANE" "$prompt"
    if ! wait_for_dispatch_ack "$AGENTS_DIR/CLAUDE_STARTED" "Claude" "$CLAUDE_PANE"; then
      return 1
    fi
  else
    echo "Claude acceptance marker already exists; resuming completion wait."
  fi
  wait_for_completion_marker "$AGENTS_DIR/CLAUDE_RESULT.done" "Claude" "$CLAUDE_PANE"
}

dispatch_and_wait_codex() {
  local token
  local prompt

  token="$(current_token)"
  if [[ "$(read_single_line "$AGENTS_DIR/CODEX_REVIEW.done")" == "$token" ]]; then
    clear_active_agent
    return 0
  fi

  mark_active_agent "codex" "$CODEX_PANE"
  if [[ "$(read_single_line "$AGENTS_DIR/CODEX_STARTED")" != "$token" ]]; then
    prompt="$(build_codex_prompt)"
    echo "Dispatching round $(read_single_line "$AGENTS_DIR/ROUND") review to visible Codex pane ${CODEX_PANE}."
    send_prompt_to_pane "$CODEX_PANE" "$prompt"
    if ! wait_for_dispatch_ack "$AGENTS_DIR/CODEX_STARTED" "Codex" "$CODEX_PANE"; then
      return 1
    fi
  else
    echo "Codex acceptance marker already exists; resuming completion wait."
  fi
  wait_for_completion_marker "$AGENTS_DIR/CODEX_REVIEW.done" "Codex" "$CODEX_PANE"
}

reset_agents_files() {
  ensure_agents_dir
  : > "$AGENTS_DIR/PLAN.md"
  : > "$AGENTS_DIR/CODEX_REVIEW.md"
  : > "$AGENTS_DIR/CLAUDE_RESULT.md"
  : > "$AGENTS_DIR/RUN_ID"
  : > "$AGENTS_DIR/ROUND"
  : > "$AGENTS_DIR/CLAUDE_STARTED"
  : > "$AGENTS_DIR/CODEX_STARTED"
  : > "$AGENTS_DIR/CLAUDE_RESULT.done"
  : > "$AGENTS_DIR/CODEX_REVIEW.done"
  clear_run_diagnostics
  set_state "idle"
}

archive_agents_files() {
  local archive_dir
  local stamp
  local file
  local final_status="${1:-ARCHIVED}"

  ensure_agents_dir
  stamp="$(date +%Y%m%d-%H%M%S)-$$"
  archive_dir="$AGENTS_DIR/archive/${stamp}"
  mkdir -p "$archive_dir"
  for file in PLAN.md CODEX_REVIEW.md CLAUDE_RESULT.md RUN_ID ROUND STATE CLAUDE_STARTED CODEX_STARTED CLAUDE_RESULT.done CODEX_REVIEW.done LAST_EXIT_CODE LAST_ERROR; do
    cp "$AGENTS_DIR/$file" "$archive_dir/$file"
  done
  reset_agents_files
  write_single_line "$AGENTS_DIR/LAST_STATUS" "$final_status"
  write_single_line "$AGENTS_DIR/LAST_ARCHIVE" "${archive_dir#"$PROJECT_ROOT/"}"
  echo "Archived current agent files to ${archive_dir#"$PROJECT_ROOT/"} and reset active files."
}

show_status() {
  local active_pid
  local process_state=""

  ensure_agents_dir
  active_pid="$(read_single_line "$AGENTS_DIR/ACTIVE_PID")"
  if [[ -n "$active_pid" ]]; then
    if kill -0 "$active_pid" 2>/dev/null; then
      process_state="pane_running"
    else
      process_state="pane_not_running"
    fi
  fi

  printf 'RUN_ID: %s\n' "$(read_single_line "$AGENTS_DIR/RUN_ID")"
  printf 'ROUND: %s\n' "$(read_single_line "$AGENTS_DIR/ROUND")"
  printf 'STATE: %s\n' "$(read_single_line "$AGENTS_DIR/STATE")"
  printf 'ACTIVE_AGENT: %s\n' "$(read_single_line "$AGENTS_DIR/ACTIVE_AGENT")"
  printf 'ACTIVE_PANE: %s\n' "$(read_single_line "$AGENTS_DIR/ACTIVE_PANE")"
  printf 'ACTIVE_PID: %s\n' "$active_pid"
  printf 'ACTIVE_PROCESS_STATE: %s\n' "$process_state"
  printf 'ACTIVE_STARTED_AT: %s\n' "$(read_single_line "$AGENTS_DIR/ACTIVE_STARTED_AT")"
  printf 'CLAUDE_STARTED: %s\n' "$(read_single_line "$AGENTS_DIR/CLAUDE_STARTED")"
  printf 'CODEX_STARTED: %s\n' "$(read_single_line "$AGENTS_DIR/CODEX_STARTED")"
  printf 'LAST_EXIT_CODE: %s\n' "$(read_single_line "$AGENTS_DIR/LAST_EXIT_CODE")"
  printf 'LAST_ERROR: %s\n' "$(read_single_line "$AGENTS_DIR/LAST_ERROR")"
  printf 'CLAUDE_RESULT.done: %s\n' "$(read_single_line "$AGENTS_DIR/CLAUDE_RESULT.done")"
  printf 'CODEX_REVIEW.done: %s\n' "$(read_single_line "$AGENTS_DIR/CODEX_REVIEW.done")"
  printf 'CONTROLLER: %s\n' "$(read_single_line "$AGENTS_DIR/CONTROLLER")"
  printf 'LAST_STATUS: %s\n' "$(read_single_line "$AGENTS_DIR/LAST_STATUS")"
  printf 'LAST_ARCHIVE: %s\n' "$(read_single_line "$AGENTS_DIR/LAST_ARCHIVE")"
}

notify_controller() {
  local message="$1"

  echo "$message"
  tmux display-message -d 5000 "$message" 2>/dev/null || true
}

controller_cleanup() {
  clear_active_agent
  : > "$AGENTS_DIR/CONTROLLER"
}

controller_fail() {
  local message="$1"

  write_single_line "$AGENTS_DIR/LAST_STATUS" "FAILED"
  if [[ ! -s "$AGENTS_DIR/LAST_ERROR" ]]; then
    write_single_line "$AGENTS_DIR/LAST_ERROR" "$message"
  fi
  notify_controller "双 Agent 协作失败：${message}。运行 ./agent status 和 ./agent logs 查看详情。"
  return 1
}

controller_loop() {
  local state
  local round
  local review_status

  ensure_agents_dir
  if ! require_agent_panes; then
    controller_fail "Codex 或 Claude 可视化面板不存在"
    return 1
  fi
  write_single_line "$AGENTS_DIR/CONTROLLER" "${TMUX_PANE:-controller}"
  trap controller_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  notify_controller "双 Agent 可视化后台控制器已启动。"

  while true; do
    state="$(read_single_line "$AGENTS_DIR/STATE")"
    case "$state" in
      idle)
        begin_run
        ;;
      claude_working)
        if ! dispatch_and_wait_claude; then
          controller_fail "Claude 提示投递或执行结果等待失败"
          return 1
        fi
        set_state "claude_result_ready"
        notify_controller "Claude 已完成当前轮次，准备在 Codex 面板启动审查。"
        ;;
      claude_result_ready)
        initialize_agent_output "codex"
        set_state "codex_reviewing"
        ;;
      codex_reviewing)
        if ! dispatch_and_wait_codex; then
          controller_fail "Codex 提示投递或审查结果等待失败"
          return 1
        fi
        set_state "review_ready"
        notify_controller "Codex 已完成当前轮次审查。"
        ;;
      review_ready)
        review_status="$(awk '/^REVIEW_STATUS: / { sub(/^REVIEW_STATUS: /, ""); print; exit }' "$AGENTS_DIR/CODEX_REVIEW.md")"
        case "$review_status" in
          PASS)
            notify_controller "Codex 审查通过，正在归档本次协作。"
            archive_agents_files "PASS"
            notify_controller "双 Agent 协作已完成并通过审查。"
            return 0
            ;;
          CHANGES_REQUIRED)
            round="$(read_single_line "$AGENTS_DIR/ROUND")"
            if (( round >= MAX_ROUNDS )); then
              controller_fail "已达到最大修正轮数 ${MAX_ROUNDS}"
              return 1
            fi
            if ! prepare_revision_round; then
              controller_fail "准备下一修正轮次失败"
              return 1
            fi
            notify_controller "Codex 要求修正，正在自动开始第 $(read_single_line "$AGENTS_DIR/ROUND") 轮。"
            ;;
          *)
            controller_fail "无法识别 Codex 审查结论"
            return 1
            ;;
        esac
        ;;
      *)
        controller_fail "未知状态 ${state}"
        return 1
        ;;
    esac
  done
}

validate_settings
cmd="${1:-}"
case "$cmd" in
  controller)
    controller_loop
    ;;
  status)
    show_status
    ;;
  panes)
    tmux list-panes -a -F '#{session_name}:#{window_name}.#{pane_index} pid=#{pane_pid} cmd=#{pane_current_command}'
    ;;
  capture)
    tmux capture-pane -t "${2:-$CLAUDE_PANE}" -p -S "-${CAPTURE_LINES}"
    ;;
  diff)
    git status --short
    git diff --stat
    git diff
    ;;
  reset)
    reset_agents_files
    echo "Reset active collaboration files and state; project hooks were preserved."
    ;;
  archive)
    archive_agents_files
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
