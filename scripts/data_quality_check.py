#!/usr/bin/env python
"""
【用途说明】
原始 CSV 全量数据质量检测与清洗（MLOps 数据守门员）。
规则与增量采集保持绝对一致，并记录详细的操作日志。
"""
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime

# 配置日志
LOG_DIR = Path("/home/mlops/quant_qlib_mlops/data/quality_logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "data_quality.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

RAW_DIR = Path("/home/mlops/quant_qlib_mlops/data/raw/us_daily")

def main():
    csv_files = list(RAW_DIR.glob("*.csv"))
    logging.info(f"==== 开始全量数据质检，共发现 {len(csv_files)} 个 CSV 文件 ====")
    
    cleaned_count = 0
    dropped_rows = 0
    
    for f in csv_files:
        df = pd.read_csv(f)
        original_len = len(df)
        
        if original_len == 0:
            continue
            
        # 记录原始数据，用于找出被删的行
        df_original = df.copy()
        
        # 规则1：OHLC 不能为空
        df = df.dropna(subset=["open", "high", "low", "close"])
        # 规则2：价格必须 > 0 且 volume > 0
        df = df[(df["close"] > 0) & (df["volume"] > 0)]
        
        dropped = original_len - len(df)
        if dropped > 0:
            dropped_rows += dropped
            cleaned_count += 1
            
            # 🔥 找出被删掉的日期并记录日志
            dropped_dates = set(df_original["date"]) - set(df["date"])
            for d in dropped_dates:
                logging.warning(f"[全量清洗] {f.stem} 删除日期 {d} (OHLC缺失或价格/成交量为0)")
                
            df.to_csv(f, index=False)
            
    logging.info(f"==== 全量清洗完毕！涉及 {cleaned_count} 个文件，共剔除 {dropped_rows} 行脏数据 ====")

if __name__ == "__main__":
    main()