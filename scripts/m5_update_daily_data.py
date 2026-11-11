#!/usr/bin/env python
"""增量更新美股日线行情数据。允许部分成功，失败股票保留原有数据。"""

import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
import pandas_market_calendars as mcal
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

# 从主流水线读取统一的目标交易日
TARGET_DATE = os.getenv("TARGET_DATE", "").strip()

if not TARGET_DATE:
    raise RuntimeError(
        "缺少 TARGET_DATE，请通过主流水线传入目标交易日。"
    )

try:
    target_date = date.fromisoformat(TARGET_DATE)
except ValueError as exc:
    raise RuntimeError(
        f"TARGET_DATE 格式错误：{TARGET_DATE}"
    ) from exc

if target_date.isoformat() != TARGET_DATE:
    raise RuntimeError(
        f"TARGET_DATE 格式错误：{TARGET_DATE}"
    )

# yfinance 的 end 参数不包含结束日期
START = (target_date - timedelta(days=3)).isoformat()
END = (target_date + timedelta(days=1)).isoformat()

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
# 8. 检查每只股票的行情新鲜度并生成报告
# ============================================================

def generate_freshness_report(symbols, target_date):
    """
    检查股票池中全部股票的 CSV 最新行情日期。

    状态：
    达标：最新行情日期等于目标交易日
    滞后：最新行情日期早于目标交易日
    异常：CSV 损坏、日期字段异常、日期不是交易日或日期超前
    缺失：CSV 文件不存在或没有数据
    """

    cal = mcal.get_calendar("NYSE")
    target = date.fromisoformat(target_date)

    records = []

    for symbol in symbols:
        csv_path = OUT / f"{symbol}.csv"

        record = {
            "symbol": symbol,
            "target_date": target_date,
            "latest_date": "",
            "lag_trading_days": "",
            "status": "",
            "detail": "",
        }

        # 1. CSV 文件不存在
        if not csv_path.exists():
            record["status"] = "缺失"
            record["detail"] = "CSV 文件不存在"
            records.append(record)
            continue

        # 2. 读取 CSV
        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:
            record["status"] = "异常"
            record["detail"] = f"CSV 读取失败：{exc}"
            records.append(record)
            continue

        if df.empty:
            record["status"] = "缺失"
            record["detail"] = "CSV 文件没有数据"
            records.append(record)
            continue

        if "date" not in df.columns:
            record["status"] = "异常"
            record["detail"] = "CSV 缺少 date 字段"
            records.append(record)
            continue

        # 3. 提取最新有效日期，并检查无效日期记录
        parsed_dates = pd.to_datetime(
            df["date"],
            errors="coerce",
        )

        invalid_count = int(parsed_dates.isna().sum())

        valid_dates = parsed_dates.dropna()

        if valid_dates.empty:
            record["status"] = "异常"
            record["detail"] = "date 字段没有有效日期"
            records.append(record)
            continue

        latest = valid_dates.dt.date.max()
        record["latest_date"] = latest.isoformat()

        if invalid_count > 0:
            record["status"] = "异常"
            record["detail"] = f"发现 {invalid_count} 条无效日期记录"
            records.append(record)
            continue

        # 4. 检查是否出现目标日期之后的数据
        if latest > target:
            record["status"] = "异常"
            record["detail"] = "最新行情日期晚于目标交易日"
            records.append(record)
            continue

        # 5. 检查最新行情日期是否为 NYSE 交易日
        latest_schedule = cal.schedule(
            start_date=latest,
            end_date=latest,
        )

        if latest_schedule.empty:
            record["status"] = "异常"
            record["detail"] = "CSV 最新行情日期不是 NYSE 交易日"
            records.append(record)
            continue

        # 6. 判断是否达到目标日期
        if latest == target:
            record["status"] = "达标"
            record["lag_trading_days"] = 0
            record["detail"] = "已更新至目标交易日"
            records.append(record)
            continue

        # 7. 计算落后的交易日数量
        lag_schedule = cal.schedule(
            start_date=latest + timedelta(days=1),
            end_date=target,
        )

        lag_days = len(lag_schedule)

        record["lag_trading_days"] = lag_days
        record["status"] = "滞后"
        record["detail"] = f"落后目标日期 {lag_days} 个交易日"

        records.append(record)

    # 8. 保存报告
    report_df = pd.DataFrame(records)

    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S_%f")

    report_path = (
        LOG_DIR
        / f"data_freshness_{target_date}_{timestamp}.csv"
    )

    report_df.to_csv(
        report_path,
        index=False,
        encoding="utf-8-sig",
    )

    # 9. 汇总统计
    counts = report_df["status"].value_counts()

    passed = int(counts.get("达标", 0))
    lagged = int(counts.get("滞后", 0))
    abnormal = int(counts.get("异常", 0))
    missing = int(counts.get("缺失", 0))

    print("\n" + "=" * 50)
    print("行情新鲜度检查结果")
    print("=" * 50)
    print(f"目标交易日：{target_date}")
    print(f"检查股票数：{len(symbols)}")
    print(f"达标：{passed}")
    print(f"滞后：{lagged}")
    print(f"异常：{abnormal}")
    print(f"缺失：{missing}")
    print(f"报告文件：{report_path}")

    if lagged or abnormal or missing:
        logger.warning(
            "行情新鲜度检查发现问题：达标=%s，滞后=%s，异常=%s，缺失=%s",
            passed,
            lagged,
            abnormal,
            missing,
        )
    else:
        logger.info("全部股票行情均已达到目标交易日")

    return report_path


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
    # 9. 行情新鲜度检查
    # ========================================================

    try:
        generate_freshness_report(
            symbols=symbols,
            target_date=TARGET_DATE,
        )
    except Exception:
        # 新鲜度报告属于监控功能。
        # 报告生成失败不改变原有下载结果和退出状态。
        logger.exception("行情新鲜度报告生成失败")
        print("[WARNING] 行情新鲜度报告生成失败，不影响后续流水线")

    # ========================================================
    # 10. 汇总结果
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
