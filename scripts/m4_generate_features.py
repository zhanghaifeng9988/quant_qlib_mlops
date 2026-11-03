# 拉取近一年半的数据，算出特征，存成 Parquet 文件
#  Parquet 支持保留 DataFrame 的 Index，我们可以完美保留 (datetime, instrument) 的二维结构
# 每日收盘后，等待m5_update_daily_data.py运行，重新 dump 生成 .bin，并处理了all.txt的冗余行后，需要运行本脚本
#!/usr/bin/env python
"""M4-a1: 离线生成近期预测所需的特征，存为 Parquet (轻量级 Feature Store)。"""
from pathlib import Path
import pandas as pd
import qlib
from qlib.constant import REG_US
from qlib.contrib.data.handler import Alpha158
from qlib.data import D

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QLIB_DATA_DIR = PROJECT_ROOT / "data" / "qlib_bin" / "us_data"
OUTPUT_DIR = PROJECT_ROOT / "data" / "features"
OUTPUT_PATH = OUTPUT_DIR / "features_latest.parquet"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("[startup] Initializing Qlib...")
    qlib.init(provider_uri=str(QLIB_DATA_DIR), region=REG_US)

    # 🔥 动态获取最新交易日
    from datetime import date
    today_str = date.today().strftime("%Y-%m-%d")
    # 从 Qlib 日历里拿最近有数据的那一天
    latest_date = D.calendar(end_time=today_str, freq="day")[-1].strftime("%Y-%m-%d")
    
    print(f"[features] Dynamic latest date detected: {latest_date}")
    
    handler = Alpha158(
        start_time="2024-01-01", 
        end_time=latest_date,      # 改成动态变量
        fit_start_time="2011-10-24",
        fit_end_time="2023-12-31",
        instruments="all"
    )
    
    # 获取特征数据 (带 MultiIndex: datetime, instrument)
    df = handler.fetch(col_set="feature", data_key="infer")
    print(f"[features] Shape: {df.shape}")
    
    # 清理列名（去掉 Qlib 默认的 MultiIndex 列名，只留特征名）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(1)
        
    print(f"[save] Saving to {OUTPUT_PATH}...")
    # 保存为 Parquet 格式（极度压缩，读取极快）
    df.to_parquet(OUTPUT_PATH)
    print("[done] Offline features generated successfully.")

if __name__ == "__main__":
    main()