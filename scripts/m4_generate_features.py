# 拉取近一年半的数据，算出特征，存成 Parquet 文件
#  Parquet 支持保留 DataFrame 的 Index，我们可以完美保留 (datetime, instrument) 的二维结构

#!/usr/bin/env python
"""M4-a1: 离线生成近期预测所需的特征，存为 Parquet (轻量级 Feature Store)。"""
from pathlib import Path
import pandas as pd
import qlib
from qlib.constant import REG_US
from qlib.contrib.data.handler import Alpha158

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QLIB_DATA_DIR = PROJECT_ROOT / "data" / "qlib_bin" / "us_data"
OUTPUT_DIR = PROJECT_ROOT / "data" / "features"
OUTPUT_PATH = OUTPUT_DIR / "features_latest.parquet"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("[startup] Initializing Qlib...")
    qlib.init(provider_uri=str(QLIB_DATA_DIR), region=REG_US)

    print("[features] Calculating Alpha158 from 2024-01-01 to 2026-10-30...")
    # 关键点：
    # start_time 设为 2024-01-01，足够覆盖 60 天回看窗口
    # fit_start_time 和 fit_end_time 必须与 M2 训练时一致，保证截面标准化参数相同
    handler = Alpha158(
        start_time="2024-01-01", 
        end_time="2026-10-16",
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