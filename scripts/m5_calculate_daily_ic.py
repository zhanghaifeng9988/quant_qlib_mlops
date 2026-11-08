#!/usr/bin/env python
"""计算已有完整未来收益数据、且尚未计算过的预测日期的 IC。"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import qlib

from qlib.constant import REG_US
from qlib.data import D


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

QLIB_DATA_DIR = (
    PROJECT_ROOT / "data" / "qlib_bin" / "us_data"
)

DB_CONN_INFO = (
    "host=localhost port=5432 dbname=metrics "
    "user=mlops password=mlops123"
)

MIN_SAMPLES = 30

# 在预测日期 t 计算：
# Close(T+2) / Close(T+1) - 1
RETURN_EXPR = "Ref($close, -2) / Ref($close, -1) - 1"


def main():
    logging.info("Initializing Qlib: %s", QLIB_DATA_DIR)

    if not QLIB_DATA_DIR.exists():
        raise FileNotFoundError(QLIB_DATA_DIR)

    qlib.init(
        provider_uri=str(QLIB_DATA_DIR),
        region=REG_US
    )

    # 使用 Qlib 真实交易日历
    calendar = pd.DatetimeIndex(
        pd.to_datetime(D.calendar(freq="day"))
    ).normalize()

    calendar = calendar.drop_duplicates().sort_values()

    if len(calendar) < 3:
        raise ValueError("Qlib 交易日历不足，无法计算 T+2 收益。")

    # 最新一个交易日只能作为 T+2，
    # 因此最晚可计算的预测日期是倒数第三个交易日
    latest_matured_date = calendar[-3].date()

    logging.info(
        "Latest mature prediction date: %s",
        latest_matured_date
    )

    # 只查询尚未计算且已经成熟的预测
    with psycopg.connect(DB_CONN_INFO) as conn:
        with conn.cursor() as curr:
            curr.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_ic_metrics (
                    calc_date DATE PRIMARY KEY,
                    ic FLOAT,
                    rank_ic FLOAT
                )
                """
            )

            curr.execute(
                """
                SELECT
                    p.predict_date,
                    p.instrument,
                    p.score
                FROM predictions p
                LEFT JOIN daily_ic_metrics m
                    ON m.calc_date = p.predict_date
                WHERE
                    m.calc_date IS NULL
                    AND p.predict_date <= %s
                ORDER BY p.predict_date, p.instrument
                """,
                (latest_matured_date,)
            )

            rows = curr.fetchall()

    if not rows:
        logging.info("No mature pending prediction dates.")
        return 0

    pred_df = pd.DataFrame(
        rows,
        columns=["predict_date", "instrument", "score"]
    )

    # 按预测日期分组，一次处理一个交易日
    records = []
    failed_dates = []

    for pred_date, group in pred_df.groupby("predict_date"):
        pred_ts = pd.Timestamp(pred_date).normalize()
        date_str = pred_ts.strftime("%Y-%m-%d")

        logging.info("Calculating IC for %s", date_str)

        try:
            # 确认预测日期确实存在于 Qlib 交易日历
            calendar_pos = calendar.get_indexer([pred_ts])[0]

            if calendar_pos < 0:
                raise ValueError(
                    f"{date_str} 不在 Qlib 交易日历中。"
                )

            t2_pos = calendar_pos + 2

            if t2_pos >= len(calendar):
                logging.info(
                    "Skipping %s: T+2 data is not mature.",
                    date_str
                )
                continue

            t2_date = calendar[t2_pos]

            # 整理预测数据
            df_pred = group[
                ["instrument", "score"]
            ].copy()

            df_pred["score"] = pd.to_numeric(
                df_pred["score"], errors="coerce"
            )

            df_pred = (
                df_pred
                .dropna(subset=["instrument", "score"])
                .drop_duplicates(subset=["instrument"], keep="last")
                .set_index("instrument")
            )

            if df_pred.empty:
                logging.warning(
                    "Skipping %s: no valid predictions.",
                    date_str
                )
                continue

            # 明确指定 T+2 为查询结束日期
            # 不再使用自然日 +10 天
            df_ret = D.features(
                instruments=df_pred.index.tolist(),
                fields=[RETURN_EXPR],
                start_time=date_str,
                end_time=t2_date.strftime("%Y-%m-%d")
            )

            if df_ret is None or df_ret.empty:
                logging.warning(
                    "Skipping %s: no Qlib return data.",
                    date_str
                )
                continue

            if not isinstance(df_ret.index, pd.MultiIndex):
                raise ValueError("Qlib 收益数据缺少 datetime MultiIndex。")

            # 提取预测日期 t 对应的 T+1 到 T+2 收益
            df_ret = df_ret.xs(
                pred_ts,
                level="datetime"
            )

            df_ret.columns = ["ret"]

            df_ret["ret"] = pd.to_numeric(
                df_ret["ret"], errors="coerce"
            )

            # 预测分数与真实收益按 instrument 对齐
            df = df_pred.join(
                df_ret,
                how="inner"
            )

            df = df.replace(
                [np.inf, -np.inf],
                np.nan
            ).dropna()

            if len(df) < MIN_SAMPLES:
                logging.warning(
                    "Skipping %s: only %d valid samples, need %d.",
                    date_str, len(df), MIN_SAMPLES
                )
                continue

            ic = df["score"].corr(df["ret"])
            rank_ic = df["score"].corr(
                df["ret"],
                method="spearman"
            )

            if not np.isfinite(ic) or not np.isfinite(rank_ic):
                logging.warning(
                    "Skipping %s: IC or Rank IC is not finite.",
                    date_str
                )
                continue

            records.append((
                pred_date,
                float(ic),
                float(rank_ic)
            ))

            logging.info(
                "[%s] T+2=%s samples=%d IC=%.4f RankIC=%.4f",
                date_str,
                t2_date.strftime("%Y-%m-%d"),
                len(df),
                ic,
                rank_ic
            )

        except Exception:
            logging.exception(
                "IC calculation failed for %s.",
                date_str
            )
            failed_dates.append(date_str)

    # 成功计算的日期统一写入数据库
    if records:
        with psycopg.connect(DB_CONN_INFO) as conn:
            with conn.cursor() as curr:
                curr.executemany(
                    """
                    INSERT INTO daily_ic_metrics
                        (calc_date, ic, rank_ic)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (calc_date) DO NOTHING
                    """,
                    records
                )

        logging.info(
            "Committed %d daily IC records.",
            len(records)
        )

    if failed_dates:
        logging.error(
            "IC calculation failed for dates: %s",
            ", ".join(failed_dates)
        )
        return 1

    logging.info("Daily IC calculation completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("Daily IC task failed.")
        raise SystemExit(1)
