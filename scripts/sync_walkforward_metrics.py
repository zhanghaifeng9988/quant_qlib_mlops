#!/usr/bin/env python
"""
【用途说明】
重训后同步单个最新窗口（如 W9）的指标入库。
自动从 MLflow 抓取最新跑完的 run，追加写入 PostgreSQL 的 walkforward_metrics 表。
"""
import logging
from mlflow.tracking import MlflowClient
import psycopg
from pathlib import Path
from db_config import DB_CONN_INFO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

# TRACKING_URI = "sqlite:////home/mlops/quant_qlib_mlops/mlflow.db"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MLFLOW_DB_PATH = PROJECT_ROOT / "mlflow.db"
TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH.as_posix()}"
EXPERIMENT_NAME = "america_stock_strategy"


def main():
    logging.info(f"Connecting to MLflow, looking for experiment: {EXPERIMENT_NAME}")
    client = MlflowClient(tracking_uri=TRACKING_URI)
    
    # 获取指定实验
    exps = client.search_experiments(filter_string=f"name='{EXPERIMENT_NAME}'")
    if not exps:
        logging.error(f"Experiment '{EXPERIMENT_NAME}' not found.")
        return
    exp_id = exps[0].experiment_id

    # 按开始时间降序，取最新跑完的 run
    runs = client.search_runs(
        experiment_ids=[exp_id], 
        filter_string="attributes.status = 'FINISHED'",
        order_by=["attribute.start_time DESC"],
        max_results=1
    )
    
    if not runs:
        logging.error("No FINISHED runs found.")
        return

    latest_run = runs[0]
    run_id = latest_run.info.run_id
    metrics = latest_run.data.metrics
    
    logging.info(f"Found latest FINISHED run: {run_id[:12]}")

    # 提取指标
    ic = metrics.get("IC", 0.0)
    rank_ic = metrics.get("Rank IC", 0.0)
    
    # 兼容 Qlib 可能的前缀
    ex_ret = metrics.get("excess_return_with_cost.annualized_return", 0.0)
    if ex_ret == 0.0:
        ex_ret = metrics.get("1day.excess_return_with_cost.annualized_return", 0.0)

    # 为了配合前端，我们动态算出 window_id
    # 先查数据库里当前有多少条记录
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM walkforward_metrics")
            count = cur.fetchone()[0]
            window_id = f"w{count + 1}"
            
            logging.info(f"Inserting as {window_id}: IC={ic:.4f}, RankIC={rank_ic:.4f}, ExRet={ex_ret:.4f}")
            
            # 如果这个 run_id 已经存在，就先删掉（防止重复跑）
            cur.execute("DELETE FROM walkforward_metrics WHERE run_id = %s", (run_id,))
            
            # 插入新数据
            cur.execute("""
                INSERT INTO walkforward_metrics (window_id, run_id, ic, rank_ic, excess_return_cost)
                VALUES (%s, %s, %s, %s, %s)
            """, (window_id, run_id, ic, rank_ic, ex_ret))
            
            logging.info(f"Successfully synced {window_id} to PostgreSQL.")

if __name__ == "__main__":
    main()