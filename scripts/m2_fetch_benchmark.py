#!/usr/bin/env python
"""M2b-0: 下载 S&P 500 指数 (^GSPC) 作为回测基准。"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
import os

proxy = 'http://172.21.160.1:7892' # 代理设置
os.environ['HTTP_PROXY'] = proxy 
os.environ['HTTPS_PROXY'] = proxy 

# 项目根：基于脚本位置解析为绝对路径，避免依赖 cwd
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "data" / "raw" / "us_daily" / "SPX.csv"

START = (date.today() - timedelta(days=365 * 15)).isoformat()
END = date.today().isoformat()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] ^GSPC {START} -> {END}")
    print(f"[output] {OUT}")

    df = yf.download("^GSPC", start=START, end=END,
                     auto_adjust=False, threads=False, progress=False)
    # 单 ticker 下载返回 MultiIndex (field, ticker)，先拍平到 field
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    print(f"[cols] {list(df.columns)}")
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if "adj_close" in df.columns:
        df["factor"] = df["adj_close"] / df["close"]
    else:
        # 指数无分红拆股，无复权因子
        df["factor"] = 1.0
    df["symbol"] = "SPX"
    df = df[["date", "symbol", "open", "high", "low", "close", "volume", "factor"]]
    df = df.dropna(subset=["close"])
    df.to_csv(OUT, index=False)
    print(f"[saved] {OUT} rows={len(df)} range={df['date'].iloc[0]} -> {df['date'].iloc[-1]}")


if __name__ == "__main__":
    main()