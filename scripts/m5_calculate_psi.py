#!/usr/bin/env python
"""
M5: 计算特征数据漂移 (PSI) 并写入 PostgreSQL。

【PSI 衡量逻辑详细说明】
1. 为什么不能直接拿“今天的500只股票”和“2024年全年的所有股票数据”比？
   这是统计学中的“样本维度错位”。2024年的数据包含了250个交易日的时序波动，
   而当前期仅仅是单日截面。单日极易受突发事件影响（如暴跌、暴涨放量），
   如果拿单日数据去撞全年分布，PSI 会必然爆表（如 1.3），产生“假报警”。

2. 正确的工业级做法：滑动窗口截面均值对比。
   - 基准期（Base）：取 2024 年全年，计算每一天全市场 500 只股票的“截面均值”，
     得到一条包含约 250 个点的均值时间序列。这代表了模型初期的“常态波动”。
   - 当前期（Actual）：取最近 30 个交易日，同样计算每天的截面均值，
     得到一条包含 30 个点的均值时间序列。这代表了当下的“近期波动”。
   - 计算：比较这 30 个“近期均值”的分布，是否偏离了 250 个“常态均值”的分布。

3. 业务意义：
   这种算法能有效过滤掉“单日黑天鹅”的噪音，真正捕捉到“近期市场风格（如连续暴跌）
   是否已经偏离了模型训练时的常态”。如果 PSI > 0.25，说明市场结构已变，模型大概率失效。
"""
import logging
from pathlib import Path
import pandas as pd
import numpy as np
import psycopg
import requests


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATURES_PATH = PROJECT_ROOT / "data" / "features" / "features_latest.parquet"
DB_CONN_INFO = "host=localhost port=5432 dbname=metrics user=mlops password=mlops123"

# 配置时间窗口
BASE_START = "2024-01-01"
BASE_END = "2024-12-31"
# CURRENT_DATE = "2026-10-30" # 以最新交易日为基准，往前取30天
WINDOW_DAYS = 30

def calculate_psi(base_series: pd.Series, actual_series: pd.Series, buckets=10):
    """计算单列特征均值分布的 PSI"""
    # 1. 剔除空值
    base_series = base_series.dropna()
    actual_series = actual_series.dropna()
    if len(base_series) == 0 or len(actual_series) == 0:
        return 0.0

    # 2. 确定分箱边界（基于基准期的分位数）
    breakpoints = np.percentile(base_series, np.arange(0, buckets + 1) * 100 / buckets)
    # 去重，防止某些特征出现大量0导致分箱边界重合
    breakpoints = np.unique(breakpoints)
    breakpoints[0] = -np.inf
    breakpoints[-1] = np.inf

    # 3. 统计落入各个箱子的频数
    base_counts, _ = np.histogram(base_series, bins=breakpoints)
    actual_counts, _ = np.histogram(actual_series, bins=breakpoints)

    # 4. 计算占比，加 0.0001 防止除以 0 或 log(0)
    base_percents = base_counts / len(base_series) + 0.0001
    actual_percents = actual_counts / len(actual_series) + 0.0001

    # 5. 计算 PSI 公式：Sum((Actual% - Base%) * ln(Actual% / Base%))
    psi_value = np.sum((actual_percents - base_percents) * np.log(actual_percents / base_percents))
    return psi_value

""" def main():
    logging.info("Loading features data...")
    df = pd.read_parquet(FEATURES_PATH)
    
    # 获取所有交易日
    all_dates = df.index.get_level_values("datetime").unique().sort_values()
    
    # 动态获取当前期的结束日期（保证数据存在）
    current_end = pd.Timestamp(CURRENT_DATE)
    if current_end not in all_dates:
        logging.warning(f"{CURRENT_DATE} not found, falling back to latest available date.")
        current_end = all_dates[-1]
    
    # 计算当前期的起始日期
    current_dates = all_dates[all_dates <= current_end]
    if len(current_dates) < WINDOW_DAYS:
        logging.error("Not enough data for current window.")
        return
    current_start = current_dates[-WINDOW_DAYS]
    
    logging.info(f"Base Window: {BASE_START} to {BASE_END}")
    logging.info(f"Current Window: {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}")
    
    # 【核心步骤】计算每一天的截面均值
    # groupby(level="datetime") 会把每一天的 500 只股票聚合起来，求平均
    logging.info("Calculating daily cross-sectional mean...")
    base_df = df.loc[pd.IndexSlice[BASE_START:BASE_END, :], :]
    current_df = df.loc[pd.IndexSlice[current_start:current_end, :], :]
    
    base_means = base_df.groupby(level="datetime").mean()
    current_means = current_df.groupby(level="datetime").mean()
    
    logging.info(f"Base daily means shape: {base_means.shape}, Current daily means shape: {current_means.shape}")

    features = df.columns
    psi_results = []
    
    logging.info("Calculating PSI for all features based on daily means...")
    for feat in features:
        base_series = base_means[feat]
        current_series = current_means[feat]
        
        psi = calculate_psi(base_series, current_series)
        psi_results.append({"feature_name": feat, "psi": psi})

    # 排序，找出漂移最严重的
    psi_results.sort(key=lambda x: x["psi"], reverse=True)
    top_drift = psi_results[:5]
    logging.info(f"Top 5 drifted features: {[(d['feature_name'], round(d['psi'], 4)) for d in top_drift]}")

    # 写入数据库
    logging.info("Inserting to DB...")
    calc_date = current_end.strftime("%Y-%m-%d")
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as curr:
            curr.execute("DELETE FROM drift_metrics WHERE calc_date = %s", (calc_date,))
            values = [(r["feature_name"], r["psi"], calc_date) for r in psi_results]
            with curr.copy("COPY drift_metrics (feature_name, psi, calc_date) FROM STDIN") as copy:
                for v in values:
                    copy.write_row(v)
                    
    logging.info("PSI metrics inserted successfully.") """

def main():
    logging.info("Loading features data...")
    df = pd.read_parquet(FEATURES_PATH)
    
    # 获取所有交易日
    all_dates = df.index.get_level_values("datetime").unique().sort_values()
    
    # 🔥 动态获取 Parquet 里的最新交易日，作为当前期的结束日
    current_end = all_dates[-1]
    
    # 计算当前期的起始日期
    current_dates = all_dates[all_dates <= current_end]
    if len(current_dates) < WINDOW_DAYS:
        logging.error("Not enough data for current window.")
        return
    current_start = current_dates[-WINDOW_DAYS]
    
    logging.info(f"Base Window: {BASE_START} to {BASE_END}")
    logging.info(f"Current Window: {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}")
    
    # 【核心步骤】计算每一天的截面均值
    logging.info("Calculating daily cross-sectional mean...")
    base_df = df.loc[pd.IndexSlice[BASE_START:BASE_END, :], :]
    current_df = df.loc[pd.IndexSlice[current_start:current_end, :], :]
    
    base_means = base_df.groupby(level="datetime").mean()
    current_means = current_df.groupby(level="datetime").mean()
    
    logging.info(f"Base daily means shape: {base_means.shape}, Current daily means shape: {current_means.shape}")

    features = df.columns
    psi_results = []
    
    logging.info("Calculating PSI for all features based on daily means...")
    for feat in features:
        base_series = base_means[feat]
        current_series = current_means[feat]
        
        psi = calculate_psi(base_series, current_series)
        psi_results.append({"feature_name": feat, "psi": psi})

    # 排序，找出漂移最严重的
    psi_results.sort(key=lambda x: x["psi"], reverse=True)
    top_drift = psi_results[:5]
    logging.info(f"Top 5 drifted features: {[(d['feature_name'], round(d['psi'], 4)) for d in top_drift]}")

    # 写入数据库
    logging.info("Inserting to DB...")
    calc_date = current_end.strftime("%Y-%m-%d")
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as curr:
            curr.execute("DELETE FROM drift_metrics WHERE calc_date = %s", (calc_date,))
            values = [(r["feature_name"], r["psi"], calc_date) for r in psi_results]
            with curr.copy("COPY drift_metrics (feature_name, psi, calc_date) FROM STDIN") as copy:
                for v in values:
                    copy.write_row(v)
                    
    logging.info("PSI metrics inserted successfully.")  

    # --- 告警逻辑 ---
    max_psi_feature = psi_results[0]["feature_name"]
    max_psi_value = psi_results[0]["psi"]
    
    if max_psi_value > 0.25:
        alert_msg = f"🚨 模型失效预警！特征 [{max_psi_feature}] 发生严重漂移，PSI={max_psi_value:.2f}，超过红线 0.25。请检查市场环境并准备重训！"
        logging.warning(alert_msg)
        
        # 模拟发送 Webhook（如果你有钉钉机器人地址，可以替换下面的 URL）
        webhook_url = "https://httpbin.org/post" # 用这个免费的测试接口
        payload = {"msgtype": "text", "text": {"content": alert_msg}}
        
        try:
            resp = requests.post(webhook_url, json=payload, timeout=5)
            logging.info(f"Webhook alert sent, status code: {resp.status_code}")
            
            # 打印服务器收到的内容
            res_json = resp.json()
            print("\n=== Webhook 服务器收到的内容 ===")
            print(res_json.get("json"))
            print("=================================\n")
        except Exception as e:
            logging.error(f"Failed to send webhook: {e}")

if __name__ == "__main__":
    main()