#!/usr/bin/env python
"""增量更新美股日线行情数据。允许部分成功，失败股票保留原有数据。"""

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf


# ============================================================
# 1. 基础配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

OUT = PROJECT_ROOT / "data" / "raw" / "us_daily"
UNIVERSE = PROJECT_ROOT / "data" / "universe" / "universes.yaml"
LOG_DIR = PROJECT_ROOT / "data" / "quality_logs"
LOG_FILE = LOG_DIR / "data_quality.log"

BATCH_SIZE = 50
BATCH_SLEEP_SECONDS = 6

START = (date.today() - timedelta(days=3)).isoformat()
END = (date.today() + timedelta(days=1)).isoformat()

OUT.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 2. 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


# ============================================================
# 3. 代理配置
# ============================================================

def configure_proxy():
    proxy = os.getenv("YF_PROXY", "").strip()

    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        logger.info("使用 yfinance 代理：%s", proxy)
    else:
        logger.info("未配置 YF_PROXY，使用默认网络")


# ============================================================
# 4. 股票代码处理
# ============================================================

def normalize(symbol: str) -> str:
    return str(symbol).replace("/", "-").strip().upper()


# ============================================================
# 5. 捕获 yfinance 错误日志
# ============================================================

class YFinanceLogCapture(logging.Handler):
    """收集 yfinance 下载过程中产生的错误信息。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def is_rate_limit(messages):
    """判断下载错误中是否包含 Yahoo Finance 限流信息。"""

    keywords = (
        "ratelimit",
        "rate limit",
        "too many requests",
        "rate limited",
    )

    content = " ".join(messages).lower()
    return any(keyword in content for keyword in keywords)


# ============================================================
# 6. 提取单只股票的数据
# ============================================================

def extract_symbol_data(df, symbol):
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(0):
            sub = df[symbol]
        elif symbol in df.columns.get_level_values(1):
            sub = df.xs(symbol, axis=1, level=1)
        else:
            return None
    else:
        sub = df.copy()

    sub = sub.dropna(how="all")

    if sub.empty:
        return None

    sub = sub.reset_index()
    sub.columns = [
        str(column).lower().replace(" ", "_")
        for column in sub.columns
    ]

    required_columns = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]

    if not all(column in sub.columns for column in required_columns):
        logger.warning(
            "%s 下载结果缺少必要字段，跳过本次更新",
            symbol,
        )
        return None

    # 计算复权因子
    sub["factor"] = sub["adj_close"] / sub["close"]
    sub["symbol"] = symbol

    # 保留原有数据质量过滤规则
    sub = sub.dropna(
        subset=["open", "high", "low", "close", "volume", "factor"]
    )

    sub = sub[
        (sub["close"] > 0)
        & (sub["volume"] > 0)
        & (sub["factor"] > 0)
    ]

    if sub.empty:
        return None

    sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")

    return sub[
        [
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "factor",
        ]
    ]


# ============================================================
# 7. 保存单只股票的数据
# ============================================================

def save_symbol_data(symbol, new_df):
    target_csv = OUT / f"{symbol}.csv"

    if target_csv.exists():
        old_df = pd.read_csv(target_csv)

        old_df["date"] = pd.to_datetime(
            old_df["date"]
        ).dt.strftime("%Y-%m-%d")

        combined = pd.concat(
            [old_df, new_df],
            ignore_index=True,
        )

        # 同一天有新数据时，保留本次下载的数据
        combined = (
            combined
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
        )
    else:
        combined = new_df

    combined.to_csv(target_csv, index=False)

    logger.info(
        "%s 更新成功，最新行情日期：%s",
        symbol,
        combined["date"].max(),
    )


# ============================================================
# 8. 主程序
# ============================================================

def main():
    configure_proxy()

    with open(UNIVERSE, "r", encoding="utf-8") as file:
        doc = yaml.safe_load(file)

    raw_symbols = doc["dev_universe"]["symbols"]
    symbols = list(dict.fromkeys(normalize(s) for s in raw_symbols))

    print(
        f"[update] fetching {len(symbols)} symbols "
        f"from {START} to {END}"
    )

    success_symbols = []
    failed_symbols = []
    rate_limited = False

    # 捕获 yfinance 自身的错误日志
    yf_logger = logging.getLogger("yfinance")
    capture = YFinanceLogCapture()
    yf_logger.addHandler(capture)

    try:
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]

            print(
                f"\n[download] batch "
                f"{i // BATCH_SIZE + 1}: {len(batch)} symbols"
            )

            capture.messages.clear()

            # 每批下载一次，不对失败股票逐只重试
            try:
                df = yf.download(
                    batch,
                    start=START,
                    end=END,
                    auto_adjust=False,
                    group_by="ticker",
                    threads=False,
                    progress=False,
                )
            except Exception as exc:
                logger.error(
                    "批次下载异常，股票：%s，原因：%s",
                    ",".join(batch),
                    exc,
                )
                df = None

            # 判断是否触发 Yahoo Finance 限流
            if is_rate_limit(capture.messages):
                rate_limited = True
                logger.error(
                    "检测到 Yahoo Finance 限流，停止本轮后续下载请求。"
                )

            if df is None or df.empty:
                for symbol in batch:
                    failed_symbols.append(symbol)
                    logger.error(
                        "下载失败：%s，本次保留原有 CSV",
                        symbol,
                    )
            else:
                # 分别处理本批次每只股票
                for symbol in batch:
                    try:
                        sub = extract_symbol_data(df, symbol)

                        if sub is None or sub.empty:
                            failed_symbols.append(symbol)
                            logger.warning(
                                "下载失败或没有有效行情：%s，"
                                "本次保留原有 CSV",
                                symbol,
                            )
                            continue

                        save_symbol_data(symbol, sub)
                        success_symbols.append(symbol)

                    except Exception:
                        failed_symbols.append(symbol)
                        logger.exception(
                            "%s 数据处理或保存失败，保留原有数据（"
                            "如果保存过程中发生异常，请检查该 CSV）",
                            symbol,
                        )

            # 限流后不再请求下一批
            if rate_limited:
                remaining = symbols[i + len(batch):]
                failed_symbols.extend(remaining)

                logger.error(
                    "由于 Yahoo Finance 限流，"
                    "剩余 %s 只股票未尝试下载。",
                    len(remaining),
                )
                break

            if i + BATCH_SIZE < len(symbols):
                time.sleep(BATCH_SLEEP_SECONDS)

    finally:
        yf_logger.removeHandler(capture)

    # ========================================================
    # 9. 汇总结果
    # ========================================================

    success_count = len(success_symbols)
    failed_count = len(failed_symbols)

    print("\n" + "=" * 50)
    print("行情更新结果")
    print("=" * 50)
    print(f"股票总数：{len(symbols)}")
    print(f"更新成功：{success_count}")
    print(f"更新失败：{failed_count}")

    if failed_symbols:
        logger.warning(
            "本次更新失败股票：%s",
            ",".join(failed_symbols),
        )

    if rate_limited:
        logger.error(
            "本轮因 Yahoo Finance 限流提前结束。"
        )

    if failed_count == 0:
        print("[SUCCESS] 所有股票更新成功")
        return 0

    if success_count > 0:
        print(
            "[WARNING] 部分股票更新成功，"
            "成功数据已经保存，失败股票保留原有数据。"
        )
    else:
        print("[ERROR] 本轮没有股票更新成功。")

    # 部分成功也返回非零状态，供现有流水线识别
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
