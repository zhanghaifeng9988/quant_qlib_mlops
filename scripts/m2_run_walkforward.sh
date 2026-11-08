#!/usr/bin/env bash
# M2b-2: 逐窗口串行 qrun，记录 run_id，失败不中断
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CFG_DIR="$PROJECT_ROOT/configs/walkforward"
LOG_DIR="$PROJECT_ROOT/logs/walkforward"
RUN_MAP="$LOG_DIR/run_ids.txt"

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"
: > "$RUN_MAP"

FAILED=()
for w in w1 w2 w3 w4 w5 w6 w7; do
  echo "===== $w start $(date '+%H:%M:%S') ====="
  free -h | awk '/^Mem:/{print "  available: "$7}'

  if uv run qrun "$CFG_DIR/${w}.yaml" america_stock_strategy > "$LOG_DIR/${w}.log" 2>&1; then
    RUN_ID=$(grep -oP 'Recorder \K[a-f0-9]{32}' "$LOG_DIR/${w}.log" | head -1)
    echo "$w $RUN_ID" >> "$RUN_MAP"
    echo "[$w] OK  run_id=$RUN_ID"
  else
    echo "[$w] FAILED  see $LOG_DIR/${w}.log"
    FAILED+=("$w")
  fi

  free -h | awk '/^Mem:/{print "  available: "$7}'
done

echo ""
echo "run map:"
cat "$RUN_MAP"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED windows: ${FAILED[*]}"
  exit 1
fi
echo "ALL 7 WINDOWS DONE"

