#!/usr/bin/env bash
# PreToolUse(compact) — compact 前完整保存 memory 状态 + checkpoint
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHECKPOINT_DIR="$(cd "$PROJECT_ROOT/.." && pwd)/.compact-checkpoint"
CHAT_DIR="$PROJECT_ROOT/chat-history/_auto"
mkdir -p "$CHECKPOINT_DIR" "$CHAT_DIR"

TIMESTAMP="$(date "+%Y-%m-%d_%H-%M-%S")"
OUTFILE="$CHECKPOINT_DIR/$TIMESTAMP.md"

cd "$PROJECT_ROOT"

{
  echo "# Compact Pre-Save — $(date "+%Y-%m-%d %H:%M:%S")"
  echo ""

  # 工作区完整状态
  echo "## 工作区状态"
  echo '```'
  git -C "$PROJECT_ROOT" status 2>/dev/null || echo "(非 git 仓库)"
  echo '```'
  echo ""

  # 完整 diff 统计
  echo "## 变更统计"
  echo '```'
  git -C "$PROJECT_ROOT" diff --stat 2>/dev/null || true
  git -C "$PROJECT_ROOT" diff --cached --stat 2>/dev/null || true
  echo '```'
  echo ""

  # 最近提交
  echo "## 最近提交"
  echo '```'
  git -C "$PROJECT_ROOT" log --oneline -10 2>/dev/null || true
  echo '```'
  echo ""

  # 当前修改的文件
  echo "## 修改的文件"
  git -C "$PROJECT_ROOT" diff --name-only 2>/dev/null || true
  echo ""

  # Memory 存储状态
  echo "## Memory 存储状态"
  for dir in "memory_storage" ".memory-workstation-dev/memory_storage" \
             "memory_export" ".memory-workstation-dev"; do
    full="$PROJECT_ROOT/$dir"
    if [ -d "$full" ]; then
      echo "### $dir/"
      ls -la "$full/" 2>/dev/null | head -30
      echo ""
    fi
  done

  # 分支信息
  BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"
  echo "分支: $BRANCH | 时间: $(date "+%H:%M:%S")"
  echo ""
  echo "---"
  echo "*自动保存于 compact 触发前*"
} > "$OUTFILE"

# 同时更新 .compact-checkpoint.md（最新快照）
cp "$OUTFILE" "$CHECKPOINT_DIR/.compact-checkpoint.md" 2>/dev/null || true

# 清理旧 checkpoint（保留最近 30 个）
COUNT=0
for f in "$CHECKPOINT_DIR"/*.md; do
  [ -f "$f" ] && COUNT=$((COUNT + 1))
done
if [ "$COUNT" -gt 30 ]; then
  ls -t "$CHECKPOINT_DIR"/*.md 2>/dev/null | grep -v '/\.' | tail -n $((COUNT - 30)) | xargs rm -f 2>/dev/null || true
fi

# 写一条自动存档记录
AUTO_LOG="$CHAT_DIR/_pre_compact_$TIMESTAMP.md"
{
  echo "# Compact 前自动存档 — $(date "+%Y-%m-%d %H:%M:%S")"
  echo ""
  echo "checkpoint: $OUTFILE"
  echo "branch: $BRANCH"
} > "$AUTO_LOG"
