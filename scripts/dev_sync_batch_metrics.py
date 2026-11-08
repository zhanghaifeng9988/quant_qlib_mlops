#!/usr/bin/env python
"""
【用途说明】
开发环境专用：批量同步历史窗口指标。
由于开发过程中可能出现大量历史 run（如重跑 W1-W8），此脚本
会自动抓取 MLflow 中最近的 N 个 FINISHED run，全量覆盖 PG 的 walkforward_metrics 表。
不影响 predictions, drift_metrics, daily_ic_metrics 等其他监控表。
"""
from pathlib import Path
import logging
import pandas as pd
from mlflow.tracking import MlflowClient
from sqlalchemy import create_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MLFLOW_DB_PATH = PROJECT_ROOT / "mlflow.db"
TRACKING_URI = f"sqlite:///{MLFLOW_DB_PATH.as_posix()}"
DB_URI = "postgresql://mlops:mlops123@localhost:5432/metrics"
EXPERIMENT_NAME = "america_stock_strategy"

#  指定你要抓取的最新 run 数量（目前是 W1-W9，所以是 9）
NUM_WINDOWS = 9

def main():
    client = MlflowClient(tracking_uri=TRACKING_URI)
    engine = create_engine(DB_URI)
    
    logging.info(f"Fetching latest {NUM_WINDOWS} runs from experiment: {EXPERIMENT_NAME}...")
    exps = client.search_experiments(filter_string=f"name='{EXPERIMENT_NAME}'")
    if not exps:
        logging.error(f"Experiment '{EXPERIMENT_NAME}' not found.")
        return
    exp_id = exps[0].experiment_id

    # 按时间倒序获取最新的 NUM_WINDOWS 个 run
    runs = client.search_runs(
        experiment_ids=[exp_id],
        filter_string="attributes.status = 'FINISHED'",
        order_by=["attribute.start_time DESC"],
        max_results=NUM_WINDOWS
    )
    
    if len(runs) < NUM_WINDOWS:
        logging.warning(f"Only found {len(runs)} runs, expected {NUM_WINDOWS}.")
        
    # 反转为正序，保证 w1 在最前面
    runs.reverse()

    records = []
    for i, r in enumerate(runs):
        metrics = r.data.metrics
        run_id = r.info.run_id
        
        ic = metrics.get("IC", 0.0)
        rank_ic = metrics.get("Rank IC", 0.0)
        ex_ret = metrics.get("excess_return_with_cost.annualized_return", 0.0)
        if ex_ret == 0.0:
            ex_ret = metrics.get("1day.excess_return_with_cost.annualized_return", 0.0)
        max_dd = metrics.get("1day.excess_return_with_cost.max_drawdown", 0.0)

        # 按照顺序生成 w1, w2... w9
        window_id = f"w{i+1}"
        
        records.append({
            "window_id": window_id,
            "run_id": run_id,
            "ic": ic,
            "rank_ic": rank_ic,
            "excess_return_cost": ex_ret,
            "max_drawdown": max_dd
        })
        logging.info(f"  {window_id}: run_id={run_id[:8]}, IC={ic:.4f}, ExRet={ex_ret:.4f}")

    df = pd.DataFrame(records)
    
    logging.info("Overwriting walkforward_metrics table in PostgreSQL...")
    df.to_sql("walkforward_metrics", engine, if_exists="replace", index=False)
    logging.info("Batch sync completed! Refresh Grafana.")

if __name__ == "__main__":
    main()