#!/usr/bin/env bash
# UserPromptSubmit — 每次用户提交前自动保存上下文快照
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHAT_DIR="$PROJECT_ROOT/chat-history/_auto"
CHECKPOINT_DIR="$PROJECT_ROOT/../.compact-checkpoint"
mkdir -p "$CHAT_DIR"

TIMESTAMP="$(date "+%Y-%m-%d_%H-%M-%S")"
OUTFILE="$CHAT_DIR/$TIMESTAMP.md"

# 清理旧快照（保留最近 50 个）
COUNT=0
for f in "$CHAT_DIR"/*.md; do
  [ -f "$f" ] && COUNT=$((COUNT + 1))
done
if [ "$COUNT" -gt 50 ]; then
  ls -t "$CHAT_DIR"/*.md 2>/dev/null | tail -n $((COUNT - 50)) | xargs rm -f 2>/dev/null || true
fi

cd "$PROJECT_ROOT"

{
  echo "# 上下文快照 — $(date "+%Y-%m-%d %H:%M:%S")"
  echo ""

  # 工作区状态（精简）
  echo "## 工作区状态"
  echo '```'
  git -C "$PROJECT_ROOT" status --short 2>/dev/null || echo "(非 git 仓库或不在 worktree)"
  echo '```'
  echo ""

  # 近 5 条 git 记录
  echo "## 最近提交"
  echo '```'
  git -C "$PROJECT_ROOT" log --oneline -5 2>/dev/null || true
  echo '```'
  echo ""

  # 修改概览
  echo "## 未提交变更概览"
  echo '```diff'
  git -C "$PROJECT_ROOT" diff --stat 2>/dev/null || true
  echo '```'
  echo ""

  # 最新 checkpoint（如果有）
  if [ -d "$CHECKPOINT_DIR" ]; then
    LATEST_CK="$(ls -t "$CHECKPOINT_DIR"/*.md 2>/dev/null | head -1)"
    if [ -n "$LATEST_CK" ]; then
      echo "## 最新 checkpoint"
      echo "\`$LATEST_CK\`"
      echo ""
      tail -20 "$LATEST_CK" 2>/dev/null | head -20
      echo ""
    fi
  fi

  # 当前分支
  BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
  echo "分支: $BRANCH | 时间: $(date "+%H:%M:%S")"
} > "$OUTFILE"
