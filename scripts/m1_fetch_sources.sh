#!/usr/bin/env bash
# M1 前置：下载三大指数成分股源 CSV 到 data/universe/raw/
# 用法：bash scripts/m1_fetch_sources.sh
# 第2，第3条数据下载，如果不能正常下载，请参照第一条下载，增加代理的代码，修改后重新下载

echo "=================================================="
echo "🚀 [START] M1 数据源获取流程，前置工作，开始"
echo "⏰ 启动时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "第2，第3条数据下载，如果不能正常下载，请参照第一条下载，增加代理的代码，修改后重新下载"
echo "=================================================="


# 只要代码里出现任何一点小错误，脚本立刻停止运行并报错，绝不允许“带病带伤继续往下跑”。
set -euo pipefail 
cd "$(dirname "$0")/.."
mkdir -p data/universe/raw

curl -L --fail -o data/universe/raw/tickers_v2.csv \
  https://gh-proxy.org/https://raw.githubusercontent.com/zyhe16/top-us-stock-tickers/main/data/v2/tickers.csv
curl -L --fail -o data/universe/raw/nasdaq100.csv \
  https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv
curl -L --fail -o data/universe/raw/dowjones.csv \
  https://yfiua.github.io/index-constituents/constituents-dowjones.csv

echo "[done] sources downloaded:"
wc -l data/universe/raw/*.csv
