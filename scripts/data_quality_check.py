
#!/usr/bin/env python
"""原始日线数据质量检查、清洗及 PostgreSQL 质量监控记录。"""

import logging
import os
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg

from db_config import DB_CONN_INFO


# ============================================================
# 1. 项目配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "us_daily"
LOG_DIR = PROJECT_ROOT / "data" / "quality_logs"
LOG_FILE = LOG_DIR / "data_quality.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

REQUIRED_COLUMNS = [
    "date", "open", "high", "low", "close", "volume"
]

NUMERIC_COLUMNS = [
    "open", "high", "low", "close", "volume"
]

PRICE_COLUMNS = [
    "open", "high", "low", "close"
]


# ============================================================
# 2. 异常记录辅助函数
# ============================================================

def add_issue(
    issues,
    symbol,
    issue_type,
    field_name=None,
    field_value=None,
    trade_date=None,
):
    """向异常明细列表添加一条记录。"""

    if pd.isna(field_value):
        field_value = None
    elif field_value is not None:
        field_value = str(field_value)[:500]

    if pd.isna(trade_date):
        trade_date = None
    elif trade_date is not None:
        trade_date = pd.Timestamp(trade_date).date()

    issues.append({
        "symbol": symbol,
        "trade_date": trade_date,
        "issue_type": issue_type,
        "field_name": field_name,
        "field_value": field_value,
    })


# ============================================================
# 3. 清洗前质量检查
# ============================================================

def inspect_dataframe(symbol, df, issues):
    """
    检查 CSV 原始数据。

    只记录异常，不改变 DataFrame。
    """

    # 日期检查
    raw_dates = df["date"]
    parsed_dates = pd.to_datetime(
        raw_dates,
        errors="coerce",
    )

    missing_dates = raw_dates.isna()

    invalid_dates = (
        ~missing_dates
        & parsed_dates.isna()
    )

    for idx in df.index[missing_dates]:
        add_issue(
            issues,
            symbol,
            "NULL_VALUE",
            "date",
            None,
        )

    for idx in df.index[invalid_dates]:
        add_issue(
            issues,
            symbol,
            "INVALID_DATE",
            "date",
            raw_dates.loc[idx],
        )

    # 数值字段检查
    numeric_data = {}

    for col in NUMERIC_COLUMNS:
        raw = df[col]

        missing = (
            raw.isna()
            | raw.astype("string").str.strip().eq("").fillna(False)
        )

        converted = pd.to_numeric(
            raw,
            errors="coerce",
        )

        invalid = ~missing & converted.isna()

        for idx in df.index[missing]:
            add_issue(
                issues,
                symbol,
                "NULL_VALUE",
                col,
                raw.loc[idx],
                parsed_dates.loc[idx],
            )

        for idx in df.index[invalid]:
            add_issue(
                issues,
                symbol,
                "INVALID_NUMERIC",
                col,
                raw.loc[idx],
                parsed_dates.loc[idx],
            )

        numeric_data[col] = converted

    # 价格必须大于 0
    for col in PRICE_COLUMNS:
        mask = numeric_data[col] <= 0

        for idx in df.index[mask.fillna(False)]:
            add_issue(
                issues,
                symbol,
                "NON_POSITIVE_PRICE",
                col,
                numeric_data[col].loc[idx],
                parsed_dates.loc[idx],
            )

    # 成交量必须大于 0
    mask = numeric_data["volume"] <= 0

    for idx in df.index[mask.fillna(False)]:
        add_issue(
            issues,
            symbol,
            "NON_POSITIVE_VOLUME",
            "volume",
            numeric_data["volume"].loc[idx],
            parsed_dates.loc[idx],
        )

    # OHLC 价格关系检查
    ohlc = pd.DataFrame({
        col: numeric_data[col]
        for col in PRICE_COLUMNS
    })

    valid_ohlc = ohlc.notna().all(axis=1)

    invalid_high = (
        valid_ohlc
        & (
            ohlc["high"]
            < ohlc[["open", "close", "low"]].max(axis=1)
        )
    )

    invalid_low = (
        valid_ohlc
        & (
            ohlc["low"]
            > ohlc[["open", "close", "high"]].min(axis=1)
        )
    )

    for idx in ohlc.index[invalid_high]:
        add_issue(
            issues,
            symbol,
            "INVALID_OHLC",
            "high",
            ohlc.loc[idx, "high"],
            parsed_dates.loc[idx],
        )

    for idx in ohlc.index[invalid_low]:
        add_issue(
            issues,
            symbol,
            "INVALID_OHLC",
            "low",
            ohlc.loc[idx, "low"],
            parsed_dates.loc[idx],
        )

    # 重复交易日期检查
    duplicate_dates = (
        parsed_dates.notna()
        & parsed_dates.duplicated(keep=False)
    )

    for idx in df.index[duplicate_dates]:
        add_issue(
            issues,
            symbol,
            "DUPLICATE_DATE",
            "date",
            parsed_dates.loc[idx],
            parsed_dates.loc[idx],
        )


# ============================================================
# 4. 读取最新行情新鲜度报告
# ============================================================

def load_freshness_report(target_date):
    """读取本次日更生成的新鲜度 CSV。"""

    pattern = f"data_freshness_{target_date}_*.csv"
    reports = list(LOG_DIR.glob(pattern))

    if not reports:
        logging.warning(
            "没有找到目标日期 %s 的新鲜度报告。",
            target_date,
        )
        return []

    report_file = max(
        reports,
        key=lambda path: path.stat().st_mtime,
    )

    df = pd.read_csv(report_file)

    required = {
        "symbol",
        "target_date",
        "latest_date",
        "lag_trading_days",
        "status",
        "detail",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"新鲜度报告缺少字段：{sorted(missing)}"
        )

    records = []

    for row in df.to_dict("records"):
        latest_date = row["latest_date"]

        records.append({
            "target_date": pd.Timestamp(
                row["target_date"]
            ).date(),
            "symbol": str(row["symbol"]),
            "latest_date": (
                pd.to_datetime(latest_date).date()
                if pd.notna(latest_date)
                else None
            ),
            "lag_trading_days": (
                int(row["lag_trading_days"])
                if pd.notna(row["lag_trading_days"])
                else None
            ),
            "status": str(row["status"]),
            "detail": (
                str(row["detail"])
                if pd.notna(row["detail"])
                else None
            ),
        })

    logging.info(
        "已读取新鲜度报告：%s，共 %d 条记录",
        report_file.name,
        len(records),
    )

    return records


# ============================================================
# 5. PostgreSQL 写入
# ============================================================

def save_quality_records(
    run_id,
    target_date,
    total_symbols,
    cleaned_symbols,
    dropped_rows,
    issues,
    freshness_records,
    status,
):
    """在一个事务中写入本次质检汇总、异常和新鲜度数据。"""

    issue_symbols = len({
        item["symbol"] for item in issues
    })

    with psycopg.connect(DB_CONN_INFO) as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO data_quality_runs (
                    run_id,
                    target_date,
                    total_symbols,
                    cleaned_symbols,
                    dropped_rows,
                    issue_symbols,
                    issue_count,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    target_date,
                    total_symbols,
                    cleaned_symbols,
                    dropped_rows,
                    issue_symbols,
                    len(issues),
                    status,
                ),
            )

            if issues:
                cur.executemany(
                    """
                    INSERT INTO data_quality_issues (
                        run_id,
                        symbol,
                        trade_date,
                        issue_type,
                        field_name,
                        field_value
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            run_id,
                            item["symbol"],
                            item["trade_date"],
                            item["issue_type"],
                            item["field_name"],
                            item["field_value"],
                        )
                        for item in issues
                    ],
                )

            if freshness_records:
                cur.executemany(
                    """
                    INSERT INTO data_freshness_metrics (
                        run_id,
                        target_date,
                        symbol,
                        latest_date,
                        lag_trading_days,
                        status,
                        detail
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            run_id,
                            item["target_date"],
                            item["symbol"],
                            item["latest_date"],
                            item["lag_trading_days"],
                            item["status"],
                            item["detail"],
                        )
                        for item in freshness_records
                    ],
                )

        # psycopg 上下文正常退出时自动提交事务。

    logging.info(
        "PostgreSQL 记录成功：run_id=%s，"
        "异常=%d，新鲜度=%d",
        run_id,
        len(issues),
        len(freshness_records),
    )


def update_run_status(run_id, status):
    """更新本次质量检查的最终状态。"""

    with psycopg.connect(DB_CONN_INFO) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE data_quality_runs
                SET status = %s
                WHERE run_id = %s
                """,
                (status, run_id),
            )


# ============================================================
# 6. 主流程
# ============================================================

def main():

    if not RAW_DIR.is_dir():
        logging.error("原始数据目录不存在：%s", RAW_DIR)
        return 1

    csv_files = sorted(RAW_DIR.glob("*.csv"))

    if not csv_files:
        logging.error("原始数据目录中没有 CSV 文件。")
        return 1

    target_date = os.getenv("TARGET_DATE", "").strip()

    if not target_date:
        logging.error("缺少 TARGET_DATE 环境变量。")
        return 1

    try:
        target_date = date.fromisoformat(target_date)
    except ValueError:
        logging.error("TARGET_DATE 格式无效。")
        return 1

    run_id = uuid.uuid4()

    logging.info(
        "==== 开始数据质检：run_id=%s，文件数=%d ====",
        run_id,
        len(csv_files),
    )

    cleaned_count = 0
    dropped_rows = 0
    failed_files = []
    cleaned_data = {}
    issues = []

    # --------------------------------------------------------
    # 先读取全部文件、检查异常、计算原有清洗结果
    # --------------------------------------------------------

    for file in csv_files:
        symbol = file.stem.upper()

        try:
            df = pd.read_csv(file)

            if df.empty:
                raise ValueError("CSV 文件为空")

            missing = [
                col for col in REQUIRED_COLUMNS
                if col not in df.columns
            ]

            if missing:
                raise ValueError(
                    f"缺少必要字段：{', '.join(missing)}"
                )

            # 清洗前检查，保留原始数据用于异常记录
            inspect_dataframe(symbol, df, issues)

            original_len = len(df)

            # 以下保持原有清洗逻辑不变
            for col in NUMERIC_COLUMNS:
                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce",
                )

            df = df.dropna(
                subset=REQUIRED_COLUMNS
            )

            df = df[
                (df["close"] > 0)
                & (df["volume"] > 0)
            ]

            dropped = original_len - len(df)

            cleaned_data[file] = df

            if dropped > 0:
                cleaned_count += 1
                dropped_rows += dropped

                logging.warning(
                    "[数据清洗] %s：剔除 %d 行无效数据",
                    file.name,
                    dropped,
                )

        except Exception as exc:
            failed_files.append(file.name)

            add_issue(
                issues,
                symbol,
                "FILE_CHECK_FAILED",
                None,
                str(exc),
            )

            logging.exception(
                "[质检失败] %s：%s",
                file.name,
                exc,
            )

    # --------------------------------------------------------
    # 读取新鲜度报告
    # --------------------------------------------------------

    try:
        freshness_records = load_freshness_report(
            target_date
        )
    except Exception:
        logging.exception("读取新鲜度报告失败")
        return 1

    # --------------------------------------------------------
    # 先将检查结果写入数据库，再执行文件清洗
    # --------------------------------------------------------

    initial_status = (
        "FAILED" if failed_files else "CHECKED"
    )

    try:
        save_quality_records(
            run_id=run_id,
            target_date=target_date,
            total_symbols=len(csv_files),
            cleaned_symbols=cleaned_count,
            dropped_rows=dropped_rows,
            issues=issues,
            freshness_records=freshness_records,
            status=initial_status,
        )
    except Exception:
        logging.exception(
            "数据质量记录写入 PostgreSQL 失败，"
            "停止后续清洗。"
        )
        return 1

    # --------------------------------------------------------
    # 任一文件结构或读取检查失败，保留原有全部文件
    # --------------------------------------------------------

    if failed_files:
        logging.error(
            "数据质检失败，涉及文件：%s",
            ", ".join(failed_files),
        )
        return 1

    # --------------------------------------------------------
    # 所有文件检查通过后，执行原有清洗写入
    # --------------------------------------------------------

    try:
        for file, df in cleaned_data.items():

            original = pd.read_csv(file)

            if len(df) == len(original):
                continue

            temp_file = file.with_suffix(
                file.suffix + ".tmp"
            )

            try:
                df.to_csv(temp_file, index=False)
                temp_file.replace(file)

            except Exception:
                if temp_file.exists():
                    temp_file.unlink()
                raise

    except Exception:
        logging.exception("写入清洗结果失败")

        try:
            update_run_status(run_id, "CLEANUP_FAILED")
        except Exception:
            logging.exception("更新质检状态失败")

        return 1

    # --------------------------------------------------------
    # 更新最终运行状态
    # --------------------------------------------------------

    final_status = (
        "COMPLETED_ISSUES"
        if issues
        else "COMPLETED"
    )

    try:
        update_run_status(run_id, final_status)
    except Exception:
        logging.exception("更新质检最终状态失败")
        return 1

    logging.info(
        "==== 数据质检完成：检查 %d 个文件，"
        "清洗 %d 个文件，剔除 %d 行，"
        "发现 %d 条异常 ====",
        len(csv_files),
        cleaned_count,
        dropped_rows,
        len(issues),
    )

    print(
        f"[success] checked {len(csv_files)} CSV files, "
        f"cleaned {cleaned_count} files, "
        f"removed {dropped_rows} rows, "
        f"issues {len(issues)}, "
        f"run_id={run_id}"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("数据质量检查任务发生异常。")
        raise SystemExit(1)
