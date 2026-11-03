
#!/usr/bin/env python
"""增量更新日线数据"""
import json
import time
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
import yaml
import yfinance as yf
import os

proxy = 'http://172.21.160.1:7890' # 代理设置
os.environ['HTTP_PROXY'] = proxy 
os.environ['HTTPS_PROXY'] = proxy 


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "data" / "raw" / "us_daily"
UNIVERSE = PROJECT_ROOT / "data" / "universe" / "universes.yaml"

# 每次更新：从 10 天前开始拉取（自然日），自动覆盖最近的交易日
START = (date.today() - timedelta(days=3)).isoformat()
# yfinance 的 end 是排他（exclusive）的，所以往后推 1 天，确保能拉到今天的最新数据
END = (date.today() + timedelta(days=1)).isoformat()

def normalize(symbol: str) -> str:
    return symbol.replace("/", "-").strip().upper()

def main():
    doc = yaml.safe_load(open(UNIVERSE))
    raw_symbols = doc["dev_universe"]["symbols"]
    symbols = [normalize(s) for s in raw_symbols]
    
    print(f"[update] fetching {len(symbols)} symbols from {START} to {END}")
    
    # 按 50 只一批下载
    BATCH_SIZE = 20
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i+BATCH_SIZE]
        print(f"\n[download] batch {i//BATCH_SIZE + 1}: {len(batch)} symbols")
        
        df = yf.download(batch, start=START, end=END, auto_adjust=False, group_by="ticker", threads=False, progress=False)
        
        if df.empty:
            print("  [warn] df is empty, skip this batch")
            continue

        for sym in batch:
            # 1. 提取单只股票数据
            if isinstance(df.columns, pd.MultiIndex):
                if sym not in df.columns.levels[0]:
                    print(f"  [skip] {sym} not in downloaded data")
                    continue
                sub = df[sym]
            else:
                sub = df
                
            sub = sub.dropna(how="all")
            if sub.empty:
                print(f"  [skip] {sym} has no valid rows (empty)")
                continue
            
            # 2. 整理数据
            sub = sub.reset_index()
            sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
            
            if "adj_close" not in sub.columns or "close" not in sub.columns:
                print(f"  [skip] {sym} missing adj_close or close columns")
                continue
                
            sub["factor"] = sub["adj_close"] / sub["close"]
            sub["symbol"] = sym
            sub = sub[["date", "symbol", "open", "high", "low", "close", "volume", "factor"]]
            
            # 3. 追加写入原有 CSV
            target_csv = OUT / f"{sym}.csv"
            if target_csv.exists():
                old_df = pd.read_csv(target_csv)
                
                # 🔥 强制统一时间类型（都转为 YYYY-MM-DD 字符串）
                old_df["date"] = pd.to_datetime(old_df["date"]).dt.strftime("%Y-%m-%d")
                sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
                
                # 合并去重（按 date）
                combined = pd.concat([old_df, sub]).drop_duplicates(subset=["date"]).sort_values("date")
                combined.to_csv(target_csv, index=False)
                print(f"  [ok] {sym} updated, last date: {combined['date'].max()}")
            else:
                print(f"  [warn] {target_csv} does not exist, skip")
                
        time.sleep(6)

if __name__ == "__main__":
    main()