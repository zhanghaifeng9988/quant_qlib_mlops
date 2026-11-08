#!/usr/bin/env python
"""计算股票截面特征分布 PSI，并写入 PostgreSQL。"""

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FEATURES_PATH = (
    PROJECT_ROOT / "data" / "features" / "features_latest.parquet"
)

DB_CONN_INFO = (
    "host=localhost port=5432 dbname=metrics "
    "user=mlops password=mlops123"
)

BASE_START = "2024-01-01"
BASE_END = "2024-12-31"

WINDOW_DAYS = 30
BUCKETS = 10
PSI_THRESHOLD = 0.25

# 从环境变量读取真实 Webhook，不再使用 httpbin 测试接口
WEBHOOK_URL = os.getenv("PSI_WEBHOOK_URL", "").strip()


def calculate_psi(base_series, actual_series, buckets=BUCKETS):
    """基于基准期分位数分箱，计算两个样本分布的 PSI。"""

    base = (
        pd.to_numeric(base_series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy()
    )

    actual = (
        pd.to_numeric(actual_series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .to_numpy()
    )

    if len(base) == 0 or len(actual) == 0:
        raise ValueError("PSI 样本为空。")

    # 根据基准期分位数确定分箱边界
    quantiles = np.quantile(
        base,
        np.linspace(0, 1, buckets + 1)
    )

    unique_edges = np.unique(quantiles)

    # 基准样本为常数时，构造三个有效区间
    if len(unique_edges) == 1:
        center = unique_edges[0]
        width = max(abs(center) * 1e-6, 1e-6)
        inner_edges = [center - width, center + width]
    else:
        inner_edges = unique_edges[1:-1]

    edges = np.concatenate((
        [-np.inf],
        inner_edges,
        [np.inf]
    ))

    base_counts, _ = np.histogram(base, bins=edges)
    actual_counts, _ = np.histogram(actual, bins=edges)

    # 平滑处理，避免某个分箱占比为 0
    epsilon = 0.5

    base_pct = (
        base_counts + epsilon
    ) / (
        len(base) + epsilon * len(base_counts)
    )

    actual_pct = (
        actual_counts + epsilon
    ) / (
        len(actual) + epsilon * len(actual_counts)
    )

    psi = np.sum(
        (actual_pct - base_pct)
        * np.log(actual_pct / base_pct)
    )

    return float(psi)


def send_alert(message):
    """发送可选的真实 Webhook 告警。"""

    if not WEBHOOK_URL:
        logging.warning(
            "PSI 超过阈值，但未配置 PSI_WEBHOOK_URL，跳过 Webhook 发送。"
        )
        return

    payload = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }

    try:
        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        response.raise_for_status()

        logging.info(
            "PSI webhook sent, HTTP status: %s",
            response.status_code
        )

    except Exception:
        # 告警发送失败不影响 PSI 指标入库
        logging.exception("PSI Webhook 发送失败。")


def main():
    logging.info("Loading feature data: %s", FEATURES_PATH)

    if not FEATURES_PATH.exists():
        raise FileNotFoundError(FEATURES_PATH)

    df = pd.read_parquet(FEATURES_PATH)

    if df.empty:
        raise ValueError("特征 Parquet 文件为空。")

    if not isinstance(df.index, pd.MultiIndex):
        raise ValueError("特征数据必须使用 MultiIndex。")

    if "datetime" not in df.index.names:
        raise ValueError("特征索引中不存在 datetime 层。")

    # 获取真实交易日
    dates = pd.DatetimeIndex(
        pd.to_datetime(df.index.get_level_values("datetime"))
    ).normalize()

    all_dates = pd.DatetimeIndex(
        dates.unique()
    ).sort_values()

    if len(all_dates) < WINDOW_DAYS:
        raise ValueError("可用交易日不足，无法计算 PSI。")

    # 基准期：2024 年全部股票截面观测值
    base_mask = (
        (dates >= pd.Timestamp(BASE_START))
        & (dates <= pd.Timestamp(BASE_END))
    )

    # 当前期：最近 30 个交易日
    current_dates = all_dates[-WINDOW_DAYS:]
    current_mask = dates.isin(current_dates)

    base_df = df.iloc[np.flatnonzero(base_mask)]
    current_df = df.iloc[np.flatnonzero(current_mask)]

    if base_df.empty:
        raise ValueError("基准期没有可用特征数据。")

    if current_df.empty:
        raise ValueError("当前期没有可用特征数据。")

    # Alpha158 特征为数值列
    features = df.select_dtypes(include="number").columns.tolist()

    if not features:
        raise ValueError("没有发现数值型特征列。")

    logging.info(
        "Base window: %s to %s, rows=%d",
        BASE_START, BASE_END, len(base_df)
    )

    logging.info(
        "Current window: %s to %s, trading_days=%d, rows=%d",
        current_dates[0].strftime("%Y-%m-%d"),
        current_dates[-1].strftime("%Y-%m-%d"),
        len(current_dates),
        len(current_df)
    )

    # 对每个特征，直接比较股票层面的截面观测值分布
    psi_results = []

    for feature in features:
        base_values = base_df[feature]
        current_values = current_df[feature]

        base_count = base_values.notna().sum()
        current_count = current_values.notna().sum()

        if base_count == 0 or current_count == 0:
            logging.warning(
                "Skip %s: insufficient valid observations.",
                feature
            )
            continue

        psi = calculate_psi(base_values, current_values)

        psi_results.append({
            "feature_name": str(feature),
            "psi": psi
        })

        logging.info(
            "[PSI] %s = %.6f | base_n=%d | current_n=%d",
            feature, psi, base_count, current_count
        )

    if not psi_results:
        raise ValueError("没有生成有效 PSI 结果。")

    psi_results.sort(
        key=lambda x: x["psi"],
        reverse=True
    )

    calc_date = current_dates[-1].strftime("%Y-%m-%d")

    # DELETE + COPY 使用同一个事务
    with psycopg.connect(DB_CONN_INFO) as conn:
        with conn.cursor() as curr:
            curr.execute(
                """
                DELETE FROM drift_metrics
                WHERE calc_date = %s
                """,
                (calc_date,)
            )

            with curr.copy(
                """
                COPY drift_metrics (feature_name, psi, calc_date)
                FROM STDIN
                """
            ) as copy:
                for result in psi_results:
                    copy.write_row((
                        result["feature_name"],
                        result["psi"],
                        calc_date
                    ))

    logging.info(
        "PSI metrics committed: %d features, calc_date=%s",
        len(psi_results), calc_date
    )

    # 输出漂移最严重的五个特征
    logging.info(
        "Top 5 drifted features: %s",
        [
            (item["feature_name"], round(item["psi"], 4))
            for item in psi_results[:5]
        ]
    )

    max_feature = psi_results[0]["feature_name"]
    max_psi = psi_results[0]["psi"]

    if max_psi > PSI_THRESHOLD:
        message = (
            f"模型特征分布漂移预警：{max_feature} "
            f"PSI={max_psi:.4f}，超过阈值 {PSI_THRESHOLD}。"
        )

        logging.warning(message)
        send_alert(message)

    print(
        f"[success] PSI calculated for {len(psi_results)} features, "
        f"date={calc_date}, max_psi={max_psi:.4f}"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("PSI calculation failed.")
        raise SystemExit(1)
