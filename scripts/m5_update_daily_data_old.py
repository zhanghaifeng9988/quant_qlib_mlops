#!/usr/bin/env python
"""增量更新日线数据"""
import time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yaml
import yfinance as yf
import os
import logging

proxy = 'http://172.21.160.1:7890'
os.environ['HTTP_PROXY'] = proxy 
os.environ['HTTPS_PROXY'] = proxy 

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "data" / "raw" / "us_daily"
UNIVERSE = PROJECT_ROOT / "data" / "universe" / "universes.yaml"
LOG_DIR = PROJECT_ROOT / "data" / "quality_logs"
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

START = (date.today() - timedelta(days=3)).isoformat()
END = (date.today() + timedelta(days=1)).isoformat()

def normalize(symbol: str) -> str:
    return symbol.replace("/", "-").strip().upper()

def main():
    doc = yaml.safe_load(open(UNIVERSE))
    raw_symbols = doc["dev_universe"]["symbols"]
    symbols = [normalize(s) for s in raw_symbols]
    
    print(f"[update] fetching {len(symbols)} symbols from {START} to {END}")
    
    BATCH_SIZE = 50
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i+BATCH_SIZE]
        print(f"\n[download] batch {i//BATCH_SIZE + 1}: {len(batch)} symbols")
        
        df = yf.download(batch, start=START, end=END, auto_adjust=False, group_by="ticker", threads=False, progress=False)
        
        if df.empty:
            continue

        for sym in batch:
            if isinstance(df.columns, pd.MultiIndex):
                if sym not in df.columns.levels[0]:
                    continue
                sub = df[sym]
            else:
                sub = df
                
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            
            sub = sub.reset_index()
            sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
            
            if "adj_close" not in sub.columns or "close" not in sub.columns:
                continue
                
            sub["factor"] = sub["adj_close"] / sub["close"]
            sub["symbol"] = sym
            
            # 记录过滤前的长度和日期
            original_len = len(sub)
            original_dates = set(sub["date"].astype(str))
            
            # 🔥 共享数据质量标准：与 data_quality_check.py 保持绝对一致
            sub = sub.dropna(subset=["open", "high", "low", "close"])
            sub = sub[(sub["close"] > 0) & (sub["volume"] > 0)]
            
            if len(sub) < original_len:
                # 找出被删掉的日期并写入日志
                dropped_dates = original_dates - set(sub["date"].astype(str))
                for d in dropped_dates:
                    logging.warning(f"[增量清洗] {sym} 丢弃日期 {d} (OHLC缺失或价格/成交量为0)")
            
            if sub.empty:
                continue
                
            sub = sub[["date", "symbol", "open", "high", "low", "close", "volume", "factor"]]
            
            target_csv = OUT / f"{sym}.csv"
            if target_csv.exists():
                old_df = pd.read_csv(target_csv)
                old_df["date"] = pd.to_datetime(old_df["date"]).dt.strftime("%Y-%m-%d")
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                combined = pd.concat([old_df, sub]).drop_duplicates(subset=["date"]).sort_values("date")
                combined.to_csv(target_csv, index=False)
                print(f"  [ok] {sym} updated, last date: {combined['date'].max()}")
            else:
                print(f"  [warn] {target_csv} does not exist, skip")
                
        time.sleep(6)

if __name__ == "__main__":
    main()