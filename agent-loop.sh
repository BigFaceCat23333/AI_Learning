#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_PROJECT_ROOT="/Users/urpapa/code/proj/pyProj/ai-learning"
PROJECT_ROOT="$SCRIPT_DIR"
AGENTS_DIR="${PROJECT_ROOT}/.agents"

TARGET_PANE="${AGENT_CLAUDE_PANE:-agents:work.1}"
CODEX_PANE="${AGENT_CODEX_PANE:-agents:work.0}"
LINES="${AGENT_CAPTURE_LINES:-200}"
POLL_SECONDS="${AGENT_POLL_SECONDS:-5}"
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
  ./agent-loop.sh panes
  ./agent-loop.sh send-plan [pane]
  ./agent-loop.sh send-plan-and-wait [claude-pane] [codex-pane]
  ./agent-loop.sh send-review [pane]
  ./agent-loop.sh send-codex-review [pane]
  ./agent-loop.sh wait-review [codex-pane]
  ./agent-loop.sh wait-codex-review
  ./agent-loop.sh controller
  ./agent-loop.sh status
  ./agent-loop.sh capture [pane]
  ./agent-loop.sh diff
  ./agent-loop.sh reset
  ./agent-loop.sh archive

Environment:
  AGENT_CLAUDE_PANE          default Claude pane: agents:work.1
  AGENT_CODEX_PANE           default Codex pane: agents:work.0
  AGENT_CAPTURE_LINES        capture line count: 200
  AGENT_POLL_SECONDS         polling interval: 5
  AGENT_WAIT_TIMEOUT_SECONDS wait timeout: 1800; set 0 to disable
  AGENT_MAX_ROUNDS          maximum automatic revision rounds: 10

Project-local files:
  .codex/hooks.json
  .claude/settings.local.json
  .agents/hooks/agent-stop.sh
  .agents/PLAN.md
  .agents/CLAUDE_RESULT.md
  .agents/CODEX_REVIEW.md
  .agents/RUN_ID
  .agents/ROUND
  .agents/STATE
  .agents/*.done
  .agents/CONTROLLER
  .agents/LAST_STATUS
  .agents/LAST_ARCHIVE
  .agents/controller.log
  .agents/archive/
EOF
}

validate_settings() {
  if [[ ! "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    echo "AGENT_POLL_SECONDS must be a positive integer." >&2
    exit 1
  fi
  if [[ ! "$WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "AGENT_WAIT_TIMEOUT_SECONDS must be a non-negative integer." >&2
    exit 1
  fi
  if [[ ! "$MAX_ROUNDS" =~ ^[1-9][0-9]*$ ]]; then
    echo "AGENT_MAX_ROUNDS must be a positive integer." >&2
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

ensure_agents_dir() {
  mkdir -p "$AGENTS_DIR/hooks"
  touch \
    "$AGENTS_DIR/PLAN.md" \
    "$AGENTS_DIR/CODEX_REVIEW.md" \
    "$AGENTS_DIR/CLAUDE_RESULT.md" \
    "$AGENTS_DIR/RUN_ID" \
    "$AGENTS_DIR/ROUND" \
    "$AGENTS_DIR/STATE" \
    "$AGENTS_DIR/CLAUDE_RESULT.done" \
    "$AGENTS_DIR/CODEX_REVIEW.done" \
    "$AGENTS_DIR/CONTROLLER" \
    "$AGENTS_DIR/LAST_STATUS" \
    "$AGENTS_DIR/LAST_ARCHIVE" \
    "$AGENTS_DIR/controller.log"

  if [[ ! -s "$AGENTS_DIR/STATE" ]]; then
    printf 'idle\n' > "$AGENTS_DIR/STATE"
  fi
}

set_state() {
  printf '%s\n' "$1" > "$AGENTS_DIR/STATE"
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
    echo "No active run. Send a plan first." >&2
    exit 1
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
  printf '%s\n' "$run_id" > "$AGENTS_DIR/RUN_ID"
  printf '1\n' > "$AGENTS_DIR/ROUND"
  : > "$AGENTS_DIR/CLAUDE_RESULT.md"
  : > "$AGENTS_DIR/CODEX_REVIEW.md"
  : > "$AGENTS_DIR/CLAUDE_RESULT.done"
  : > "$AGENTS_DIR/CODEX_REVIEW.done"
  set_state "claude_working"
}

prepare_revision_round() {
  local token
  local round

  ensure_agents_dir
  require_active_round
  token="$(current_token)"

  if [[ "$(read_single_line "$AGENTS_DIR/CODEX_REVIEW.done")" != "$token" ]]; then
    echo "The current Codex review has not passed the project hook yet." >&2
    exit 1
  fi
  if grep -Fqx 'REVIEW_STATUS: PASS' "$AGENTS_DIR/CODEX_REVIEW.md"; then
    echo "The current review already passed; no revision round is needed." >&2
    exit 1
  fi

  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  printf '%s\n' "$((round + 1))" > "$AGENTS_DIR/ROUND"
  : > "$AGENTS_DIR/CLAUDE_RESULT.md"
  : > "$AGENTS_DIR/CLAUDE_RESULT.done"
  : > "$AGENTS_DIR/CODEX_REVIEW.done"
  set_state "claude_working"
}

send_prompt_to_pane() {
  local pane="$1"
  local message="$2"

  tmux set-buffer "$message"
  tmux paste-buffer -t "$pane"
  tmux send-keys -t "$pane" Enter
}

send_file_to_claude() {
  local file="$1"
  local pane="${2:-$TARGET_PANE}"
  local run_id
  local round
  local message

  if [[ ! -s "$file" ]]; then
    echo "File is empty or missing: $file" >&2
    exit 1
  fi

  run_id="$(read_single_line "$AGENTS_DIR/RUN_ID")"
  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  message="请读取 ${file}，按里面的要求处理。当前协作标识：RUN_ID=${run_id}，ROUND=${round}。完成后把改动摘要、测试结果、风险点写入 .agents/CLAUDE_RESULT.md，并在文件中保留两个独立行：RUN_ID: ${run_id} 和 ROUND: ${round}。"
  send_prompt_to_pane "$pane" "$message"
}

send_codex_review_prompt() {
  local pane="${1:-$CODEX_PANE}"
  local run_id
  local round
  local message

  require_active_round
  run_id="$(read_single_line "$AGENTS_DIR/RUN_ID")"
  round="$(read_single_line "$AGENTS_DIR/ROUND")"
  message="审查当前 git diff 和 .agents/CLAUDE_RESULT.md，判断是否满足 .agents/PLAN.md，不要直接改代码，把问题和修改建议写入 .agents/CODEX_REVIEW.md。文件必须包含独立行 RUN_ID: ${run_id}、ROUND: ${round}，并用 REVIEW_STATUS: PASS 或 REVIEW_STATUS: CHANGES_REQUIRED 表示结论。"
  send_prompt_to_pane "$pane" "$message"
}

wait_for_marker() {
  local marker_file="$1"
  local description="$2"
  local expected_token
  local started

  expected_token="$(current_token)"
  started=$SECONDS
  echo "Waiting for ${description} (${expected_token})..."

  while true; do
    if [[ "$(read_single_line "$marker_file")" == "$expected_token" ]]; then
      return 0
    fi
    if (( WAIT_TIMEOUT_SECONDS > 0 && SECONDS - started >= WAIT_TIMEOUT_SECONDS )); then
      echo "Timed out waiting for ${description} after ${WAIT_TIMEOUT_SECONDS}s." >&2
      echo "Use './agent-loop.sh status' and './agent-loop.sh capture' to inspect the run." >&2
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

wait_for_claude_result_and_send_review() {
  local codex_pane="${1:-$CODEX_PANE}"

  require_active_round
  wait_for_marker "$AGENTS_DIR/CLAUDE_RESULT.done" ".agents/CLAUDE_RESULT.md hook validation"
  set_state "codex_reviewing"
  echo "Claude result validated; sending review prompt to Codex."
  send_codex_review_prompt "$codex_pane"
}

wait_for_codex_review() {
  require_active_round
  wait_for_marker "$AGENTS_DIR/CODEX_REVIEW.done" ".agents/CODEX_REVIEW.md hook validation"
  echo "Codex review validated:"
  grep -E '^REVIEW_STATUS: (PASS|CHANGES_REQUIRED)$' "$AGENTS_DIR/CODEX_REVIEW.md"
}

reset_agents_files() {
  ensure_agents_dir
  : > "$AGENTS_DIR/PLAN.md"
  : > "$AGENTS_DIR/CODEX_REVIEW.md"
  : > "$AGENTS_DIR/CLAUDE_RESULT.md"
  : > "$AGENTS_DIR/RUN_ID"
  : > "$AGENTS_DIR/ROUND"
  : > "$AGENTS_DIR/CLAUDE_RESULT.done"
  : > "$AGENTS_DIR/CODEX_REVIEW.done"
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

  for file in PLAN.md CODEX_REVIEW.md CLAUDE_RESULT.md RUN_ID ROUND STATE CLAUDE_RESULT.done CODEX_REVIEW.done; do
    cp "$AGENTS_DIR/$file" "$archive_dir/$file"
  done
  reset_agents_files
  printf '%s\n' "$final_status" > "$AGENTS_DIR/LAST_STATUS"
  printf '%s\n' "${archive_dir#"$PROJECT_ROOT/"}" > "$AGENTS_DIR/LAST_ARCHIVE"
  echo "Archived current agent files to ${archive_dir#"$PROJECT_ROOT/"} and reset active files."
}

show_status() {
  ensure_agents_dir
  printf 'RUN_ID: %s\n' "$(read_single_line "$AGENTS_DIR/RUN_ID")"
  printf 'ROUND: %s\n' "$(read_single_line "$AGENTS_DIR/ROUND")"
  printf 'STATE: %s\n' "$(read_single_line "$AGENTS_DIR/STATE")"
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
  : > "$AGENTS_DIR/CONTROLLER"
}

controller_fail() {
  local message="$1"

  printf 'FAILED\n' > "$AGENTS_DIR/LAST_STATUS"
  notify_controller "双 Agent 协作失败：${message}。运行 ./agent status 和 ./agent logs 查看详情。"
  return 1
}

controller_loop() {
  local state
  local round
  local review_status

  ensure_agents_dir
  printf '%s\n' "${TMUX_PANE:-controller}" > "$AGENTS_DIR/CONTROLLER"
  trap controller_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  notify_controller "双 Agent 后台控制器已启动。"

  while true; do
    state="$(read_single_line "$AGENTS_DIR/STATE")"
    case "$state" in
      idle)
        begin_run
        send_file_to_claude "$AGENTS_DIR/PLAN.md" "$TARGET_PANE"
        notify_controller "计划已发送给 Claude，等待实现结果。"
        ;;
      claude_working)
        if ! wait_for_marker "$AGENTS_DIR/CLAUDE_RESULT.done" ".agents/CLAUDE_RESULT.md hook validation"; then
          controller_fail "等待 Claude 超时"
          return 1
        fi
        set_state "claude_result_ready"
        ;;
      claude_result_ready)
        set_state "codex_reviewing"
        send_codex_review_prompt "$CODEX_PANE"
        notify_controller "Claude 结果已校验，等待 Codex 审查。"
        ;;
      codex_reviewing)
        if ! wait_for_marker "$AGENTS_DIR/CODEX_REVIEW.done" ".agents/CODEX_REVIEW.md hook validation"; then
          controller_fail "等待 Codex 审查超时"
          return 1
        fi
        set_state "review_ready"
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
            prepare_revision_round
            send_file_to_claude "$AGENTS_DIR/CODEX_REVIEW.md" "$TARGET_PANE"
            notify_controller "Codex 要求修正，已自动开始第 $(read_single_line "$AGENTS_DIR/ROUND") 轮。"
            ;;
          *)
            controller_fail "无法识别 Codex 审查结论"
            return 1
            ;;
        esac
        ;;
      failed)
        controller_fail "当前状态已标记为失败，请排查后运行 ./agent-loop.sh reset"
        return 1
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
  panes)
    tmux list-panes -a
    ;;
  send-plan)
    begin_run
    send_file_to_claude "$AGENTS_DIR/PLAN.md" "${2:-$TARGET_PANE}"
    ;;
  send-plan-and-wait)
    begin_run
    send_file_to_claude "$AGENTS_DIR/PLAN.md" "${2:-$TARGET_PANE}"
    wait_for_claude_result_and_send_review "${3:-$CODEX_PANE}"
    ;;
  send-review)
    prepare_revision_round
    send_file_to_claude "$AGENTS_DIR/CODEX_REVIEW.md" "${2:-$TARGET_PANE}"
    ;;
  send-codex-review)
    ensure_agents_dir
    require_active_round
    if [[ "$(read_single_line "$AGENTS_DIR/CLAUDE_RESULT.done")" != "$(current_token)" ]]; then
      echo "The current Claude result has not passed the project hook yet." >&2
      exit 1
    fi
    set_state "codex_reviewing"
    send_codex_review_prompt "${2:-$CODEX_PANE}"
    ;;
  wait-review)
    ensure_agents_dir
    wait_for_claude_result_and_send_review "${2:-$CODEX_PANE}"
    ;;
  wait-codex-review)
    ensure_agents_dir
    wait_for_codex_review
    ;;
  controller)
    controller_loop
    ;;
  status)
    show_status
    ;;
  capture)
    tmux capture-pane -t "${2:-$TARGET_PANE}" -p -S "-${LINES}"
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
