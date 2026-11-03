#!/usr/bin/env bash
# M6: 量化每日批处理自动化流水线
#set -e # 任何一步报错，立刻停止，防止脏数据污染
set -uo pipefail  # 只用 -u，不要 -e。这样某一步挂了，后续步骤还能继续（比如后续可以发个报警）

PROJECT_ROOT="/home/mlops/quant_qlib_mlops"
cd "$PROJECT_ROOT"

# 获取当前日期（可以手动传参，默认用今天）
TARGET_DATE=${1:-$(date -d "yesterday" +%Y-%m-%d)}

echo "=================================================="
echo "🚀 启动每日 MLOps 流水线: $TARGET_DATE"
echo "=================================================="

# 1. 增量更新行情数据
echo "▶️ [1/6] 正在从 yfinance 增量下载最新行情..."
uv run python scripts/m5_update_daily_data.py

# 2. 重新 dump .bin
echo "▶️ [2/6] 正在更新 Qlib .bin 数据..."
uv run python scripts/qlib_tools/dump_bin.py dump_all \
  --data_path "$PROJECT_ROOT/data/raw/us_daily" \
  --qlib_dir "$PROJECT_ROOT/data/qlib_bin/us_data" \
  --include_fields open,high,low,close,volume,factor \
  --max_workers 2 --freq day

# 3. 将 SPX 从 instruments 中剔除（防止污染训练集）
echo "▶️ [3/6] 正在清理基准数据 SPX..."
grep -vE "^SPX[[:space:]]" "$PROJECT_ROOT/data/qlib_bin/us_data/instruments/all.txt" > /tmp/all_no_spx.txt
mv /tmp/all_no_spx.txt "$PROJECT_ROOT/data/qlib_bin/us_data/instruments/all.txt"

# 4. 离线计算最新特征 (Parquet)
echo "▶️ [4/6] 正在生成最新特征 Parquet 文件..."
uv run python scripts/m4_generate_features.py

# 5. 获取预测并入库 (注意：这里要求 FastAPI 服务是启动的)
echo "▶️ [5/6] 正在调用 API 获取预测并写入 PostgreSQL..."
uv run python scripts/m5_prediction_nextday_return_record.py --date "$TARGET_DATE"

# 6. 计算 PSI 漂移指标
echo "▶️ [6/6] 正在计算今日数据漂移 (PSI)..."
# 注意：如果你的 PSI 脚本里写死了 CURRENT_DATE，需要改成读取传入的日期
uv run python scripts/m5_calculate_psi.py

echo "=================================================="
echo "✅ 流水线执行完毕！请前往 Grafana 查看最新监控。"
echo "=================================================="