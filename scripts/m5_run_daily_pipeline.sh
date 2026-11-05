#!/usr/bin/env bash
#量化每日批处理自动化流水线 (终极完美版)
# 不开启 set -e，防止某一步网络超时导致后续的监控和入库全部瘫痪
set -uo pipefail

PROJECT_ROOT="/home/mlops/quant_qlib_mlops"
cd "$PROJECT_ROOT"

# 默认预测日期为昨天（防止美股未收盘导致数据为空），可以通过外部传参覆盖
TARGET_DATE=${1:-$(date -d "yesterday" +%Y-%m-%d)}

echo "=================================================="
echo "🚀 启动每日 MLOps 流水线: $TARGET_DATE"
echo "=================================================="

# 1. 增量更新行情数据
echo "▶️ [1/8] 正在从 yfinance 增量下载最新行情..."
uv run python scripts/m5_update_daily_data.py || echo "⚠️ 增量下载可能超时，继续执行"

# 2. 数据质量守门员清洗（极度重要！）
echo "▶️ [2/8] 正在执行数据质量检测与清洗..."
uv run python scripts/data_quality_check.py

# 3. 重新 dump .bin
echo "▶️ [3/8] 正在更新 Qlib .bin 数据..."
uv run python scripts/qlib_tools/dump_bin.py dump_all \
  --data_path "$PROJECT_ROOT/data/raw/us_daily" \
  --qlib_dir "$PROJECT_ROOT/data/qlib_bin/us_data" \
  --include_fields open,high,low,close,volume,factor \
  --max_workers 2 --freq day

# 4. 剔除 SPX（防止污染训练集）
echo "▶️ [4/8] 正在清理基准数据 SPX..."
grep -vE "^SPX[[:space:]]" "$PROJECT_ROOT/data/qlib_bin/us_data/instruments/all.txt" > /tmp/all_no_spx.txt
mv /tmp/all_no_spx.txt "$PROJECT_ROOT/data/qlib_bin/us_data/instruments/all.txt"

# 5. 离线计算最新特征 (Parquet)
echo "▶️ [5/8] 正在生成最新特征 Parquet 文件..."
uv run python scripts/m4_generate_features.py

# 6. 获取预测并入库 (要求 FastAPI 服务处于启动状态)
echo "▶️ [6/8] 正在调用 API 获取预测并写入 PostgreSQL..."
uv run python scripts/m5_prediction_nextday_return_record.py --date "$TARGET_DATE" || echo "⚠️ 预测入库失败，可能是 API 未启动或没有当日特征"

# 7. 计算实盘 IC（T+2 收益验证）
echo "▶️ [7/8] 正在计算实盘滚动 IC..."
uv run python scripts/m5_calculate_daily_ic.py

# 8. 计算 PSI 漂移指标与告警
echo "▶️ [8/8] 正在计算今日数据漂移 (PSI)..."
uv run python scripts/m5_calculate_daily_psi.py

echo "=================================================="
echo "✅ 流水线执行完毕！请前往 Grafana 查看最新监控。"
echo "=================================================="