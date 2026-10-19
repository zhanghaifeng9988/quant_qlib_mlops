#!/usr/bin/env python
"""M1 Step 3: 分批采集 yfinance 日线，落盘即释放。"""
import gc
import json
import time
from datetime import datetime, timezone,timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf
import os

proxy = 'http://172.21.160.1:7892' # 代理设置
os.environ['HTTP_PROXY'] = proxy 
os.environ['HTTPS_PROXY'] = proxy 

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "us_daily"
LOG = ROOT / "data" / "raw" / "collection_log.jsonl"
UNIVERSE = ROOT / "data" / "universe" / "universes.yaml"

BATCH_SIZE = 50
YEARS = 15
END = datetime.today().strftime("%Y-%m-%d")
START = (datetime.today() - timedelta(days=365 * YEARS)).strftime("%Y-%m-%d")
SLEEP_BETWEEN_BATCHES = 5  # 秒，避免触发 Yahoo 限流

def normalize(symbol: str) -> str:
    return symbol.replace("/", "-").strip().upper()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)

    doc = yaml.safe_load(open(UNIVERSE))
    raw_symbols = doc["dev_universe"]["symbols"]
    symbols = [normalize(s) for s in raw_symbols]
    print(f"[universe] {len(symbols)} symbols, range {START} -> {END}")

    batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    print(f"[plan] {len(batches)} batches of {BATCH_SIZE}")

    total_ok, total_fail = 0, []
    for i, batch in enumerate(batches, 1):
        t0 = time.time()
        try:
            df = yf.download(
                tickers=batch,
                start=START,
                end=END,
                auto_adjust=False,   # 保留原始 Close + Adj Close，自行算 factor
                group_by="ticker",
                threads=False,       # 单线程，控内存 + 避限流
                progress=False,
            )
        except Exception as e:
            print(f"[batch {i}] download exception: {e}")
            df = None

        ok, failed, rows = 0, [], 0
        if df is not None and not df.empty:
            for sym in batch:
                try: #把多级表头拆开，提取出单只股票的数据。如果不是多级列表，就直接用 df
                    sub = df[sym] if isinstance(df.columns, pd.MultiIndex) else df 
                    # 删掉整行都是空值的数据（比如周末、停牌日）。
                    sub = sub.dropna(how="all")
                    # 如果这只股票的数据是空的，或者缺了最核心的“收盘价”，就直接把它判定为失败（拉黑），跳过后续处理。
                    if sub.empty or "Close" not in sub.columns:
                        failed.append(sym)
                        continue
                    sub = sub.reset_index()
                    # 把列名变成纯小写，并把空格变成下划线。
                    sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
                    # factor = Adj Close / Close 
                    # 量化灵魂操作：计算复权因子（Factor）。因为股票有拆股和分红，
                    # 今天的 100 块和十年前的 100 块不一样。
                    # 保留这个因子，以后回测算收益率时就可以完美还原，这也是 Qlib 认的标准格式。
                    sub["factor"] = sub["adj_close"] / sub["close"]
                    sub["symbol"] = sym
                    # 强制筛选并固定列的顺序。
                    sub = sub[["date", "symbol", "open", "high", "low", "close", "volume", "factor"]]
                    # 只要 close（收盘价）这一列是空的，就把这一整行数据直接删掉。
                    sub = sub.dropna(subset=["close"])
                    if sub.empty:
                        failed.append(sym)
                        continue
                    sub.to_csv(OUT / f"{sym}.csv", index=False)
                    ok += 1
                    rows += len(sub)
                except Exception as e:
                    failed.append(sym)
                    print(f"  [skip] {sym}: {e}")
        else:
            failed = list(batch)

        rec = {
            "batch_id": i,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requested": len(batch),
            "ok": ok,
            "failed": failed,
            "rows": rows,
            "elapsed_s": round(time.time() - t0, 1),
        }
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")

        total_ok += ok
        total_fail.extend(failed)
        print(f"[batch {i}/{len(batches)}] ok={ok} fail={len(failed)} "
              f"rows={rows} {rec['elapsed_s']}s")

        del df
        # 强制内存清理命令
        gc.collect()
        if i < len(batches):
            time.sleep(SLEEP_BETWEEN_BATCHES)

    print(f"\n[done] ok={total_ok} failed={len(total_fail)}")
    if total_fail:
        print(f"[failed symbols] {total_fail}")

if __name__ == "__main__":
    main()