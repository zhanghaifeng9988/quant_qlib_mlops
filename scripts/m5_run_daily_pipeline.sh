#!/usr/bin/env bash
# QuantOps 每日批处理流水线
# 关键步骤失败立即中止；行情下载允许在本地数据验证通过后降级运行。
# -e：命令失败就立即退出  ,-E ERR trap 会被函数/子 shell 继承,-u：使用未定义变量时报错,-o pipefail：管道中任何命令失败就立即退出
set -Eeuo pipefail

# 自动定位项目根目录，不再依赖固定的 /home/mlops 路径   ${BASH_SOURCE[0]} 是脚本自身路径 dirname 取所在目录  
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 可选：通过第一个参数指定预测日期    :- 表示未提供时为空字符串
REQUESTED_DATE="${1:-}"

# 临时文件清理
TMP_INSTRUMENTS=""  #初始化临时文件变量，避免 set -u 下未定义。

# 清理函数：若临时文件变量非空且文件存在，则删除。用于脚本异常退出时不留垃圾。
cleanup() {
    if [[ -n "$TMP_INSTRUMENTS" && -f "$TMP_INSTRUMENTS" ]]; then
        rm -f "$TMP_INSTRUMENTS"
    fi
}
trap cleanup EXIT

# 关键步骤发生未处理错误时，输出错误位置
on_error() {
    local exit_code=$?
    echo "❌ 流水线在第 ${BASH_LINENO[0]} 行执行失败，退出码：${exit_code}" >&2
    exit "$exit_code"
}
trap on_error ERR

echo "=================================================="
echo "🚀 启动每日 MLOps 流水线"
echo "项目目录：$PROJECT_ROOT"
echo "=================================================="

# --------------------------------------------------
# 1. 增量更新行情数据
# --------------------------------------------------
echo "▶️ [1/8] 正在从 yfinance 增量下载最新行情..."

DOWNLOAD_OK=1

if uv run python scripts/m5_update_daily_data.py; then
    echo "✅ 行情更新脚本执行结束。"
else
    DOWNLOAD_OK=0
    echo "⚠️ 行情更新脚本执行失败，尝试验证本地已有数据。"
fi

# 验证原始行情数据：
# 1. CSV 文件存在；
# 2. 文件包含必要字段；
# 3. 文件至少有一条有效记录；
# 4. 最新数据距今天不超过 10 个自然日。
#
# 注意：这是基础可用性检查，不等同于完整的逐股票数据质量验证。
LATEST_DATA_DATE="$(
uv run python - <<'PY'
import csv
import sys
from pathlib import Path
from datetime import date, datetime

raw_dir = Path("data/raw/us_daily")
required = {"date", "symbol", "open", "high", "low", "close", "volume", "factor"}

files = sorted(raw_dir.glob("*.csv"))
if not files:
    print("[ERROR] 没有找到行情 CSV 文件", file=sys.stderr)
    sys.exit(1)

latest_date = None
valid_files = 0

for path in files:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                raise ValueError("CSV 缺少表头")

            missing = required - set(reader.fieldnames)
            if missing:
                raise ValueError(f"缺少字段: {sorted(missing)}")

            row_count = 0
            for row in reader:
                if not row.get("date"):
                    continue

                parsed_date = datetime.strptime(
                    row["date"][:10], "%Y-%m-%d"
                ).date()

                row_count += 1
                if latest_date is None or parsed_date > latest_date:
                    latest_date = parsed_date

            if row_count == 0:
                raise ValueError("没有数据记录")

            valid_files += 1

    except Exception as exc:
        print(f"[ERROR] {path.name}: {exc}", file=sys.stderr)
        sys.exit(1)

if latest_date is None:
    print("[ERROR] 无法确定最新行情日期", file=sys.stderr)
    sys.exit(1)

age_days = (date.today() - latest_date).days

if age_days < -1:
    print(f"[ERROR] 最新行情日期异常：{latest_date}", file=sys.stderr)
    sys.exit(1)

if age_days > 10:
    print(
        f"[ERROR] 本地行情数据已过旧：最新日期 {latest_date}，"
        f"距今 {age_days} 天",
        file=sys.stderr
    )
    sys.exit(1)

print(
    f"[INFO] 验证通过：{valid_files} 个 CSV 文件，"
    f"最新行情日期 {latest_date}",
    file=sys.stderr
)
print(latest_date.isoformat())
PY
)"

if [[ "$DOWNLOAD_OK" -eq 0 ]]; then
    echo "⚠️ 下载失败，但本地行情数据基础验证通过，继续使用已有数据。"
else
    echo "✅ 本地行情数据基础验证通过。"
fi

# 确定预测日期
if [[ -n "$REQUESTED_DATE" ]]; then
    TARGET_DATE="$REQUESTED_DATE"
else
    TARGET_DATE="$LATEST_DATA_DATE"
fi

# 检查日期格式
if ! date -d "$TARGET_DATE" "+%Y-%m-%d" >/dev/null 2>&1; then
    echo "❌ 预测日期格式错误：$TARGET_DATE" >&2
    exit 1
fi

echo "📅 本次预测日期：$TARGET_DATE"

# --------------------------------------------------
# 2. 数据质量检测与清洗
# --------------------------------------------------
echo "▶️ [2/8] 正在执行数据质量检测与清洗..."
uv run python scripts/data_quality_check.py
echo "✅ 数据质量检测完成。"

# --------------------------------------------------
# 3. 更新 Qlib .bin 数据
# --------------------------------------------------
echo "▶️ [3/8] 正在更新 Qlib .bin 数据..."
uv run python scripts/qlib_tools/dump_bin.py dump_all \
    --data_path "$PROJECT_ROOT/data/raw/us_daily" \
    --qlib_dir "$PROJECT_ROOT/data/qlib_bin/us_data" \
    --include_fields open,high,low,close,volume,factor \
    --max_workers 2 \
    --freq day
echo "✅ Qlib .bin 更新完成。"

# --------------------------------------------------
# 4. 剔除 SPX
# --------------------------------------------------
echo "▶️ [4/8] 正在清理基准数据 SPX..."

INSTRUMENTS_FILE="$PROJECT_ROOT/data/qlib_bin/us_data/instruments/all.txt"

if [[ ! -f "$INSTRUMENTS_FILE" ]]; then
    echo "❌ 找不到 Qlib instruments 文件：$INSTRUMENTS_FILE" >&2
    exit 1
fi

TMP_INSTRUMENTS="$(mktemp "${INSTRUMENTS_FILE}.XXXXXX")"
awk '$1 != "SPX" { print }' "$INSTRUMENTS_FILE" > "$TMP_INSTRUMENTS"
mv "$TMP_INSTRUMENTS" "$INSTRUMENTS_FILE"
TMP_INSTRUMENTS=""

echo "✅ SPX 清理完成。"

# --------------------------------------------------
# 4.5 更新数据血缘 manifest
# --------------------------------------------------
echo "▶️ [4.5/8] 正在更新数据血缘 manifest..."
uv run python scripts/m1_write_manifest.py
echo "✅ Manifest 更新完成。"

# --------------------------------------------------
# 5. 生成特征 Parquet
# --------------------------------------------------
echo "▶️ [5/8] 正在生成最新特征 Parquet 文件..."
uv run python scripts/m4_generate_features.py
echo "✅ 特征生成完成。"

# --------------------------------------------------
# 可选步骤：记录失败，但不阻断后续独立监控任务
# --------------------------------------------------
OPTIONAL_FAILURE=0

run_optional() {
    local step_name="$1"
    shift

    echo "▶️ $step_name"

    if "$@"; then
        echo "✅ $step_name 完成。"
    else
        local exit_code=$?
        echo "⚠️ $step_name 失败，退出码：$exit_code" >&2
        OPTIONAL_FAILURE=1
    fi
}

# --------------------------------------------------
# 5. 强制重启api服务，
# --------------------------------------------------

echo "===== 重启预测 API ====="

if ! docker restart quant-qlib-api; then
    echo "[ERROR] API 容器重启失败"
    exit 1
fi

echo "[INFO] 等待 API 完成初始化..."

API_READY=0

for i in {1..30}; do
    if curl -fsS http://localhost:8000/health >/dev/null; then
        API_READY=1
        break
    fi

    sleep 2
done

if [ "$API_READY" -ne 1 ]; then
    echo "[ERROR] API 在 60 秒内未能恢复健康"
    docker logs --tail 50 quant-qlib-api
    exit 1
fi

echo "[SUCCESS] API 已恢复健康，可以开始预测"

# --------------------------------------------------
# 6. 获取预测并入库
# --------------------------------------------------
run_optional \
    "[6/8] 调用 API 获取预测并写入 PostgreSQL" \
    uv run python scripts/m5_prediction_nextday_return_record.py \
    --date "$TARGET_DATE"

# --------------------------------------------------
# 7. 计算实盘 IC
# --------------------------------------------------
run_optional \
    "[7/8] 计算实盘滚动 IC" \
    uv run python scripts/m5_calculate_daily_ic.py

# --------------------------------------------------
# 8. 计算 PSI
# --------------------------------------------------
run_optional \
    "[8/8] 计算数据漂移 PSI" \
    uv run python scripts/m5_calculate_daily_psi.py

echo "=================================================="

if [[ "$OPTIONAL_FAILURE" -ne 0 ]]; then
    echo "⚠️ 流水线主体步骤完成，但至少一个监控/预测步骤失败。"
    echo "请检查日志及 Grafana，不应将本次任务视为完全成功。"
    exit 1
fi

echo "✅ 流水线全部步骤执行成功。"
echo "=================================================="
