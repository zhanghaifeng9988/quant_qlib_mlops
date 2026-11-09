#!/usr/bin/env python
# 负责建表、写入 7 窗口回测指标、测试历史预测。
"""M5: 原生 psycopg 驱动，初始化数据库并写入 M2 指标和 API 预测记录。"""
import json
import logging
import requests
from pathlib import Path
import psycopg

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUMMARY_JSON = PROJECT_ROOT / "data" / "monitoring" / "walkforward_summary.json"
API_URL = "http://localhost:8000/predict?date=2026-10-16"

# 数据库连接配置（对应 docker-compose 里的 db 服务）
DB_CONN_INFO = "host=localhost port=5432 dbname=metrics user=mlops password=mlops123"

# --- 建表语句 ---
CREATE_TABLE_STATEMENTS = """
-- 1. 7 窗口指标表
DROP TABLE IF EXISTS walkforward_metrics;
CREATE TABLE walkforward_metrics (
    window_id VARCHAR(10) PRIMARY KEY,
    run_id VARCHAR(50),
    ic FLOAT,
    rank_ic FLOAT,
    excess_return_cost FLOAT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 每日预测记录表
DROP TABLE IF EXISTS predictions;
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    predict_date DATE,
    instrument VARCHAR(10),
    score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. 数据漂移表 (PSI)
DROP TABLE IF EXISTS drift_metrics;
CREATE TABLE drift_metrics (
    id SERIAL PRIMARY KEY,
    feature_name VARCHAR(50),
    psi FLOAT,
    calc_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

def prep_db():
    """初始化数据库表结构。"""
    logging.info("Initializing database tables...")
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as curr:
            curr.execute(CREATE_TABLE_STATEMENTS)
    logging.info("Tables created successfully.")

def insert_walkforward_metrics(curr):
    """读取 M2 的 JSON，写入 7 窗口指标。"""
    if not SUMMARY_JSON.exists():
        logging.warning(f"{SUMMARY_JSON} not found, skip.")
        return

    data = json.loads(SUMMARY_JSON.read_text())
    logging.info(f"Inserting {len(data)} walkforward windows...")
    for row in data:
        curr.execute(
            """
            INSERT INTO walkforward_metrics (window_id, run_id, ic, rank_ic, excess_return_cost)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                row["window"],
                row["run_id"],
                row.get("IC"),
                row.get("Rank_IC"),
                row.get("excess_cost_ann"),
            ),
        )
    logging.info("Walkforward metrics inserted.")

def insert_predictions(curr):
    """调用 FastAPI 获取 10-16 预测，写入 predictions 表。"""
    logging.info(f"Fetching predictions from API: {API_URL}")
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.error(f"API request failed: {e}")
        return

    records = data.get("predictions", [])
    logging.info(f"Got {len(records)} predictions. Inserting...")
    
    # 由于预测是 500 条，批量插入更快
    values = [
        ("2026-10-16", r["instrument"], r["score"])
        for r in records
    ]
    with curr.copy("COPY predictions (predict_date, instrument, score) FROM STDIN") as copy:
        for v in values:
            copy.write_row(v)
    logging.info("Predictions inserted.")

def main():
    prep_db()
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as curr:
            insert_walkforward_metrics(curr)
            insert_predictions(curr)
    logging.info("M5 metrics recording completed.")

if __name__ == "__main__":
    main()