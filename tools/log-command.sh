#!/bin/bash
# 记录用户指令到日志文件
# 由 user_prompt_submit hook 调用，stdin 接收用户输入

# Resolve logs/ relative to this script so any checkout location works
LOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs"
LOG_FILE="$LOG_DIR/command-log.jsonl"

mkdir -p "$LOG_DIR"

# 读取用户输入
PROMPT=$(cat)

# 跳过空输入
[ -z "$PROMPT" ] && exit 0

# 时间戳精确到秒
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# 截取前200字符作为记录（避免超长输入）
PROMPT_SHORT=$(echo "$PROMPT" | head -c 200 | tr '\n' ' ' | tr '"' "'")

# 追加到日志（JSONL格式）
echo "{\"time\":\"$TIMESTAMP\",\"prompt\":\"$PROMPT_SHORT\"}" >> "$LOG_FILE"
