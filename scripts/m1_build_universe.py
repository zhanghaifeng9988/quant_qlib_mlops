#!/usr/bin/env python
"""M1 Step 1-2: 构建 universe 快照与行业映射。负责“画圈”（定股票池）和“打标签”（定行业分类）。"""
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "universe" / "raw"
OUT = ROOT / "data" / "universe"
SNAPSHOT_DATE = "2026-10-19"

# Nasdaq/GICS sector -> 10 个方向（Real Estate 并入 Financials）
SECTOR_MAP = {
    "Technology": "Technology",
    "Information Technology": "Technology",
    "Telecommunications": "Communication Services",
    "Communication Services": "Communication Services",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Health Care": "Health Care",
    "Healthcare": "Health Care",
    "Financials": "Financials",
    "Finance": "Financials",
    "Real Estate": "Financials",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Materials": "Materials",
    "Basic Materials": "Materials",
    "Utilities": "Utilities",
    "Miscellaneous": "Industrials",
}

# 
SYMBOL_OVERRIDE = {
    "BRK/B": "Financials",
    "FERG": "Industrials",
}

# 统一数据格式为字符串类型，全部大写
def norm_symbol(s):
    if pd.isna(s):
        return None
    return str(s).strip().upper()

# 把原始数据中的symbol列中的数据全部转换成大写，然后将结果存储在新的列_symbol中
def load_tickers():
    df = pd.read_csv(RAW / "tickers_v2.csv")
    print(f"[tickers] shape={df.shape}, cols={list(df.columns)}")
    df.columns = [c.strip() for c in df.columns]
    # 
    sym_col = next((c for c in df.columns if c.lower() == "symbol"), None)
    if sym_col is None:
        raise SystemExit(f"tickers_v2.csv 找不到 symbol 列: {list(df.columns)}")
    df["_symbol"] = df[sym_col].map(norm_symbol) # 把归一化函数，应用到每一个元素
    return df

# 将原始数据中的 symbol 或者 ticker 列中的数据全部转换成大写，然后将结果存储在set中
def load_index(path, name):
    df = pd.read_csv(path)
    print(f"[{name}] shape={df.shape}, cols={list(df.columns)}")
    sym_col = next((c for c in df.columns if c.lower() in ("symbol", "ticker")), None)
    if sym_col is None:
        raise SystemExit(f"{name} 找不到 symbol/ticker 列: {list(df.columns)}")
    return set(df[sym_col].map(norm_symbol).dropna())

# 处理布尔值列的数据，有空值的返回 False，不在白名单中的，返回 False，满足bool 类型的，返回原始值
def parse_bool(s):
    if pd.isna(s):
        return False
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ("true", "1", "yes", "y")

# 处理市值列
def parse_market_cap(s):
    if pd.isna(s):
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    # # 1. 基础清洗（去空格、去美元符、去逗号）
    t = str(s).strip().replace(",", "").replace("$", "")
    mult = 1.0
    # 处理金融数据里的“K/M/B/T缩写后缀”
    if t and t[-1].upper() in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[t[-1].upper()]
        t = t[:-1]
    # 2. 尝试转换为浮点数，如果失败则返回 0.0
    try:
        return float(t) * mult
    except ValueError:
        return 0.0

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tickers = load_tickers()
    nasdaq100 = load_index(RAW / "nasdaq100.csv", "nasdaq100")
    dowjones = load_index(RAW / "dowjones.csv", "dowjones")

    sp_col = next((c for c in tickers.columns if c.lower() == "is_sp500"), None)
    if sp_col is None:
        raise SystemExit(f"tickers_v2.csv 找不到 is_sp500 列: {list(tickers.columns)}")
    sp500 = set(tickers.loc[tickers[sp_col].map(parse_bool), "_symbol"].dropna())
    # 将三个指数中的股票代码进行合并，做并集处理
    # set类型，做并集处理，自动去重，再减去 None
    target = (sp500 | nasdaq100 | dowjones) - {None}
    print(f"[target] sp500={len(sp500)} nasdaq100={len(nasdaq100)} "
          f"dowjones={len(dowjones)} union={len(target)}")
    
    # 处理市值列数据
    cap_col = next((c for c in tickers.columns
                    if c.lower() in ("market_cap", "marketcap", "mktcap")), None)
    if cap_col is None:
        print(f"[warn] 无 market_cap 列，dev 按 symbol 排序取前 500: {list(tickers.columns)}")
        cap_map = {}
    else:
        cap_map = dict(zip(tickers["_symbol"], tickers[cap_col].map(parse_market_cap)))

    # 按市值排序，取前 500 个。s是股票代码，cap_map.get(s, 0.0)是查询市值，查不到返回 0.0
    target_sorted = sorted(target, key=lambda s: cap_map.get(s, 0.0), reverse=True)
    dev = target_sorted[:500]

    # 处理行业列数据
    sec_col = next((c for c in tickers.columns if c.lower() == "sector"), None)
    if sec_col is None:
        raise SystemExit(f"tickers_v2.csv 找不到 sector 列: {list(tickers.columns)}")
    sec_map = dict(zip(tickers["_symbol"], tickers[sec_col]))

    # 构建行业映射
    rows = []
    for s in dev:
        src = sec_map.get(s)
        # 用dev中的股票代码，查询对应行业列数据后，将行业列数据在字典SECTOR_MAP中进行映射，没有和不匹配的返回 "Unknown"
        # mapped = SECTOR_MAP.get(str(src).strip(), "Unknown") if pd.notna(src) else "Unknown"
        mapped = SYMBOL_OVERRIDE.get(s) or (
            SECTOR_MAP.get(str(src).strip(), "Unknown") if pd.notna(src) else "Unknown"
        )
        rows.append({"symbol": s, "source_sector": src, "mapped_direction": mapped})
    ind_df = pd.DataFrame(rows)
    ind_df.to_csv(OUT / "industry_mapping.csv", index=False)

    covered = int((ind_df["mapped_direction"] != "Unknown").sum())
    print(f"[industry] dev={len(dev)} covered={covered} "
          f"({covered / max(len(dev), 1):.1%})")
    print("[industry] 方向分布:")
    print(ind_df["mapped_direction"].value_counts().to_string())

    # 构建 universes.yaml，为量化系统生成一份“股票池配置文件”（元数据快照）
    # 在 MLOps 的规范里，这叫数据溯源（Data Lineage）或者数据快照记录。它告诉未来的你（或者任何看这个项目的人）
    # #：“我今天训练模型用到的股票池，是从哪里来的？包含哪些股票？是怎么筛选出来的？”
    doc = {
        "snapshot_date": SNAPSHOT_DATE,
        "sources": {
            "tickers": "https://raw.githubusercontent.com/zyhe16/top-us-stock-tickers/main/data/v2/tickers.csv",
            "nasdaq100": "https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv",
            "dowjones": "https://yfiua.github.io/index-constituents/constituents-dowjones.csv",
        },
        "target_universe": {"count": len(target), "symbols": sorted(target)},
        "dev_universe": {
            "count": len(dev),
            "rule": "top 500 by market_cap from target_universe",
            "symbols": dev,
        },
    }
    # 写入 universes.yaml 文件
    with open(OUT / "universes.yaml", "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)

    print(f"[done] {OUT / 'universes.yaml'}")
    print(f"[done] {OUT / 'industry_mapping.csv'}")

if __name__ == "__main__":
    main()