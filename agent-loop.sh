#!/usr/bin/env bash
set -euo pipefail

TARGET_PANE="${AGENT_CLAUDE_PANE:-agents:work.1}"
CODEX_PANE="${AGENT_CODEX_PANE:-agents:work.0}"
LINES="${AGENT_CAPTURE_LINES:-200}"
POLL_SECONDS="${AGENT_POLL_SECONDS:-5}"

usage() {
  cat <<'EOF'
Usage:
  ./agent-loop.sh panes
  ./agent-loop.sh send-plan [pane]
  ./agent-loop.sh send-plan-and-wait [claude-pane] [codex-pane]
  ./agent-loop.sh send-review [pane]
  ./agent-loop.sh send-codex-review [pane]
  ./agent-loop.sh wait-review [codex-pane]
  ./agent-loop.sh capture [pane]
  ./agent-loop.sh diff
  ./agent-loop.sh reset
  ./agent-loop.sh archive

Environment:
  AGENT_CLAUDE_PANE    default target pane, default: agents:work.1
  AGENT_CODEX_PANE     default Codex pane, default: agents:work.0
  AGENT_CAPTURE_LINES  capture line count, default: 200
  AGENT_POLL_SECONDS   wait-review polling interval, default: 5

Files:
  .agents/PLAN.md
  .agents/CODEX_REVIEW.md
  .agents/CLAUDE_RESULT.md
  .agents/archive/
EOF
}

ensure_agents_dir() {
  mkdir -p .agents
  touch .agents/PLAN.md .agents/CODEX_REVIEW.md .agents/CLAUDE_RESULT.md
}

send_file_to_claude() {
  local file="$1"
  local pane="${2:-$TARGET_PANE}"
  local message

  if [[ ! -s "$file" ]]; then
    echo "File is empty or missing: $file" >&2
    exit 1
  fi

  message="请读取 ${file}，按里面的要求处理。完成后把改动摘要、测试结果、风险点写入 .agents/CLAUDE_RESULT.md。"
  tmux set-buffer "$message"
  tmux paste-buffer -t "$pane"
  tmux send-keys -t "$pane" Enter
}

wait_for_claude_result_and_send_review() {
  local codex_pane="${1:-$CODEX_PANE}"
  local start_mtime="$2"
  local current_mtime

  echo "Waiting for .agents/CLAUDE_RESULT.md to be updated..."
  while true; do
    current_mtime="$(file_mtime .agents/CLAUDE_RESULT.md)"
    if [[ "$current_mtime" -gt "$start_mtime" && -s .agents/CLAUDE_RESULT.md ]]; then
      echo ".agents/CLAUDE_RESULT.md updated; sending review prompt to Codex."
      send_codex_review_prompt "$codex_pane"
      break
    fi
    sleep "$POLL_SECONDS"
  done
}

send_prompt_to_pane() {
  local pane="$1"
  local message="$2"

  tmux set-buffer "$message"
  tmux paste-buffer -t "$pane"
  tmux send-keys -t "$pane" Enter
}

send_codex_review_prompt() {
  local pane="${1:-$CODEX_PANE}"
  local message

  message="审查当前 git diff 和 .agents/CLAUDE_RESULT.md，判断是否满足 .agents/PLAN.md，不要直接改代码，把问题和修改建议写入 .agents/CODEX_REVIEW.md。"
  send_prompt_to_pane "$pane" "$message"
}

file_mtime() {
  if [[ -e "$1" ]]; then
    stat -f %m "$1"
  else
    echo 0
  fi
}

reset_agents_files() {
  ensure_agents_dir
  : > .agents/PLAN.md
  : > .agents/CODEX_REVIEW.md
  : > .agents/CLAUDE_RESULT.md
}

archive_agents_files() {
  local archive_dir
  local stamp

  ensure_agents_dir
  stamp="$(date +%Y%m%d-%H%M%S)"
  archive_dir=".agents/archive/${stamp}"
  mkdir -p "$archive_dir"

  cp .agents/PLAN.md "$archive_dir/PLAN.md"
  cp .agents/CODEX_REVIEW.md "$archive_dir/CODEX_REVIEW.md"
  cp .agents/CLAUDE_RESULT.md "$archive_dir/CLAUDE_RESULT.md"
  reset_agents_files
  echo "Archived current agent files to ${archive_dir} and reset active files."
}

cmd="${1:-}"
case "$cmd" in
  panes)
    tmux list-panes -a
    ;;
  send-plan)
    ensure_agents_dir
    send_file_to_claude ".agents/PLAN.md" "${2:-$TARGET_PANE}"
    ;;
  send-plan-and-wait)
    ensure_agents_dir
    start_mtime="$(file_mtime .agents/CLAUDE_RESULT.md)"
    send_file_to_claude ".agents/PLAN.md" "${2:-$TARGET_PANE}"
    wait_for_claude_result_and_send_review "${3:-$CODEX_PANE}" "$start_mtime"
    ;;
  send-review)
    ensure_agents_dir
    send_file_to_claude ".agents/CODEX_REVIEW.md" "${2:-$TARGET_PANE}"
    ;;
  send-codex-review)
    ensure_agents_dir
    send_codex_review_prompt "${2:-$CODEX_PANE}"
    ;;
  wait-review)
    ensure_agents_dir
    start_mtime="$(file_mtime .agents/CLAUDE_RESULT.md)"
    wait_for_claude_result_and_send_review "${2:-$CODEX_PANE}" "$start_mtime"
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
    echo "Reset .agents/PLAN.md, .agents/CODEX_REVIEW.md, and .agents/CLAUDE_RESULT.md."
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
