
# 
# 专注每日增量跑批，调 API 拿最新预测并入库。
#!/usr/bin/env python
"""M5: 将最新 API 预测写入 PostgreSQL。"""
import argparse
import logging
import requests
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")

DB_CONN_INFO = "host=localhost port=5432 dbname=metrics user=mlops password=mlops123"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="预测的日期 (如 2026-10-30)")
    args = parser.parse_args()
    
    target_date = args.date
    api_url = f"http://localhost:8000/predict?date={target_date}"
    
    logging.info(f"Fetching predictions from API: {api_url}")
    try:
        resp = requests.get(api_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.error(f"API request failed: {e}")
        return

    records = data.get("predictions", [])
    logging.info(f"Got {len(records)} predictions. Inserting to DB...")
    
    values = [
        (target_date, r["instrument"], r["score"])
        for r in records
    ]
    
    with psycopg.connect(DB_CONN_INFO, autocommit=True) as conn:
        with conn.cursor() as curr:
            # 先清理同一天可能重复写入的数据
            curr.execute("DELETE FROM predictions WHERE predict_date = %s", (target_date,))
            # 批量插入
            with curr.copy("COPY predictions (predict_date, instrument, score) FROM STDIN") as copy:
                for v in values:
                    copy.write_row(v)
                    
    logging.info(f"Successfully inserted {len(records)} predictions for {target_date}.")

if __name__ == "__main__":
    main()