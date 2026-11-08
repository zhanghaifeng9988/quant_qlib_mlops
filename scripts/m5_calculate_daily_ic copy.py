#!/usr/bin/env python
"""
【用途说明】
每日计算实盘滚动 IC（True Alpha 验证）。
此脚本专门用于每日流水线。它会读取 predictions 表中已经产生的预测分数，
并从 Qlib 中获取 T+1 到 T+2 的真实收益，计算截面 IC 和 Rank IC。
结果写入 daily_ic_metrics 表，用于监控实盘模型的真实表现（而非历史回测表现）。
"""
import logging
import pandas as pd
import psycopg
import qlib
from qlib.data import D
from qlib.constant import REG_US

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
DB_CONN_INFO = "host=localhost port=5432 dbname=metrics user=mlops password=mlops123"

def main():
    # 初始化 Qlib
    qlib.init(provider_uri="/home/mlops/quant_qlib_mlops/data/qlib_bin/us_data", region=REG_US)
    
    logging.info("Fetching prediction records from DB...")
    
    with psycopg.connect(DB_CONN_INFO) as conn:
        with conn.cursor() as cur:
            # 创建新表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_ic_metrics (
                    calc_date DATE PRIMARY KEY,
                    ic FLOAT,
                    rank_ic FLOAT
                )
            """)
            # 获取所有历史预测日期
            cur.execute("SELECT DISTINCT predict_date FROM predictions ORDER BY predict_date")
            dates = [row[0] for row in cur.fetchall()]

    if not dates:
        logging.warning("No predictions found.")
        return

    logging.info(f"Calculating IC for {len(dates)} prediction dates...")
    
    records = []
    for date in dates:
        # 1. 从 PG 读取当天的预测
        with psycopg.connect(DB_CONN_INFO) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT instrument, score FROM predictions WHERE predict_date = %s", (date,))
                preds = cur.fetchall()

        if not preds:
            continue

        df_pred = pd.DataFrame(preds, columns=["instrument", "score"]).set_index("instrument")
        
        # 2. 从 Qlib 读取真实收益 (T+1 到 T+2)
        start_date = pd.Timestamp(date).strftime("%Y-%m-%d")
        end_date = (pd.Timestamp(date) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        
        try:
            # 查询一段区间的数据
            df_all = D.features(
                instruments=df_pred.index.tolist(),
                fields=["Ref($close, -2)/Ref($close, -1) - 1"],
                start_time=start_date,
                end_time=end_date
            )
            
            # 转换为 DataFrame 并提取目标日期
            df_all = pd.DataFrame(df_all)
            if df_all.empty:
                logging.info(f"Skipping {date} - no feature data returned.")
                continue
                
            df_ret = df_all.xs(pd.Timestamp(date), level="datetime")
            df_ret.columns = ["ret"]

            # 3. 判断 T+2 数据是否真的出来了（如果全是 NaN 说明还没到 T+2）
            if df_ret["ret"].isna().all():
                logging.info(f"Skipping {date} - T+2 return not available yet.")
                continue

            # 4. 合并预测和真实收益，计算 IC
            df = df_pred.join(df_ret, how="inner").dropna()
            if len(df) < 30: 
                logging.warning(f"Skipping {date} - only {len(df)} valid samples, too few to calculate IC.")
                continue
                
            ic = df["score"].corr(df["ret"])
            rank_ic = df["score"].corr(df["ret"], method="spearman")
            
            records.append((date, ic, rank_ic))
            logging.info(f"[{date}] IC={ic:.4f}, RankIC={rank_ic:.4f}")
            
        except Exception as e:
            logging.error(f"Error calculating IC for {date}: {e}")

    # 5. 写入数据库
    if records:
        logging.info(f"Writing {len(records)} daily ICs to DB...")
        with psycopg.connect(DB_CONN_INFO) as conn:
            with conn.cursor() as cur:
                for r in records:
                    cur.execute("""
                        INSERT INTO daily_ic_metrics (calc_date, ic, rank_ic) 
                        VALUES (%s, %s, %s)
                        ON CONFLICT (calc_date) DO UPDATE SET ic=EXCLUDED.ic, rank_ic=EXCLUDED.rank_ic
                    """, r)
        logging.info("Daily IC calculation done.")
    else:
        logging.info("No new ICs to write.")

if __name__ == "__main__":
    main()