#!/usr/bin/env bash
# SessionStart — 新会话自动加载最新 checkpoint 作为上下文锚点
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHECKPOINT_DIR="$(cd "$PROJECT_ROOT/.." && pwd)/.compact-checkpoint"
CHAT_DIR="$PROJECT_ROOT/chat-history/_auto"

# 标注已加载
cat <<INFO
【检】S_A_B ✓ | 会话上下文自动加载
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INFO

# 1. 加载最新 checkpoint
if [ -d "$CHECKPOINT_DIR" ]; then
  # 先找时间戳命名的，再找 .compact-checkpoint.md
  LATEST_CK="$(find "$CHECKPOINT_DIR" -maxdepth 1 -name '*.md' ! -name '.compact-checkpoint.md' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
  if [ -z "$LATEST_CK" ] || [ ! -f "$LATEST_CK" ]; then
    LATEST_CK="$CHECKPOINT_DIR/.compact-checkpoint.md"
  fi

  if [ -f "$LATEST_CK" ]; then
    CK_TIME="$(stat -c '%y' "$LATEST_CK" 2>/dev/null | cut -d. -f1 || echo "unknown")"
    echo "▶ 加载 checkpoint: $(basename "$LATEST_CK") ($CK_TIME)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    cat "$LATEST_CK"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  else
    echo "ℹ .compact-checkpoint/ 目录存在但无 checkpoint 文件"
  fi
else
  echo "ℹ 未找到 .compact-checkpoint/ 目录"
fi

echo ""

# 2. 检查是否有自动存档的上下文快照
LATEST_AUTO="$(ls -t "$CHAT_DIR"/*.md 2>/dev/null | grep -v '_pre_compact' | head -1)"
if [ -n "$LATEST_AUTO" ]; then
  echo "▶ 最近上下文快照: $(basename "$LATEST_AUTO")"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  head -30 "$LATEST_AUTO"
  echo ""
  echo "... (续)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

echo ""
echo "◆ 会话上下文自动加载完成"
