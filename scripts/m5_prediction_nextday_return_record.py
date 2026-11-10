#!/usr/bin/env python
"""获取 API 预测结果，并原子写入 PostgreSQL。"""

import argparse
import logging
import math
from datetime import datetime
from db_config import DB_CONN_INFO
import psycopg
import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


API_URL = "http://localhost:8000/predict"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="预测日期 YYYY-MM-DD")
    args = parser.parse_args()

    target_date = args.date

    try:
        datetime.strptime(target_date, "%Y-%m-%d")

        logging.info("Fetching predictions for %s", target_date)

        response = requests.get(
            API_URL,
            params={"date": target_date},
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        records = data.get("predictions")

        if not isinstance(records, list) or not records:
            raise ValueError("API 未返回有效预测记录，保留数据库原有数据。")

        # 先在内存中完成数据检查，再操作数据库
        values = []
        seen = set()

        for record in records:
            instrument = record["instrument"]
            score = float(record["score"])

            if not instrument or not math.isfinite(score):
                raise ValueError(f"无效预测记录：{record}")

            if instrument in seen:
                continue

            seen.add(instrument)
            values.append((target_date, instrument, score))

        if not values:
            raise ValueError("没有可写入的有效预测记录。")

        logging.info("Received %d valid predictions.", len(values))

        # DELETE 和 COPY 在同一个事务中
        # 任一步失败，整个事务回滚，旧数据不会丢失
        with psycopg.connect(DB_CONN_INFO) as conn:
            with conn.cursor() as curr:
                curr.execute(
                    "DELETE FROM predictions WHERE predict_date = %s",
                    (target_date,)
                )

                with curr.copy(
                    """
                    COPY predictions (predict_date, instrument, score)
                    FROM STDIN
                    """
                ) as copy:
                    for value in values:
                        copy.write_row(value)

        logging.info(
            "Successfully committed %d predictions for %s.",
            len(values),
            target_date
        )

        return 0

    except Exception:
        logging.exception("Prediction recording failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
