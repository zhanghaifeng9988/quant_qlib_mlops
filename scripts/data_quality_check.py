#!/usr/bin/env python
"""原始日线数据质量检查与清洗"""

import logging
from pathlib import Path

import pandas as pd


# 项目路径
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
        logging.StreamHandler()
    ]
)

# 必须存在的字段
REQUIRED_COLUMNS = [
    "date", "open", "high", "low", "close", "volume"
]


def main():
    if not RAW_DIR.is_dir():
        logging.error("原始数据目录不存在：%s", RAW_DIR)
        return 1

    csv_files = sorted(RAW_DIR.glob("*.csv"))

    if not csv_files:
        logging.error("原始数据目录中没有 CSV 文件。")
        return 1

    logging.info(
        "==== 开始数据质检，共发现 %d 个 CSV 文件 ====",
        len(csv_files)
    )

    cleaned_count = 0
    dropped_rows = 0
    failed_files = []

    # 先检查和读取全部文件，避免发现文件格式错误后才开始清洗
    cleaned_data = {}

    for file in csv_files:
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

            original_len = len(df)

            # 数值字段转换，无法转换的内容视为无效值
            numeric_columns = [
                "open", "high", "low", "close", "volume"
            ]

            for col in numeric_columns:
                df[col] = pd.to_numeric(
                    df[col], errors="coerce"
                )

            # 保留原有核心质检规则
            df = df.dropna(
                subset=[
                    "date", "open", "high", "low",
                    "close", "volume"
                ]
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
                    file.name, dropped
                )

        except Exception as e:
            failed_files.append(file.name)
            logging.exception(
                "[质检失败] %s：%s", file.name, e
            )

    # 任何文件质检失败，都不开始写入
    if failed_files:
        logging.error(
            "数据质检失败，涉及文件：%s",
            ", ".join(failed_files)
        )
        return 1

    # 所有文件检查通过后，写入清洗结果
    for file, df in cleaned_data.items():
        original = pd.read_csv(file)

        if len(df) == len(original):
            continue

        temp_file = file.with_suffix(file.suffix + ".tmp")

        try:
            df.to_csv(temp_file, index=False)
            temp_file.replace(file)

        except Exception:
            if temp_file.exists():
                temp_file.unlink()
            raise

    logging.info(
        "==== 数据质检完成：检查 %d 个文件，"
        "清洗 %d 个文件，剔除 %d 行数据 ====",
        len(csv_files), cleaned_count, dropped_rows
    )

    print(
        f"[success] checked {len(csv_files)} CSV files, "
        f"cleaned {cleaned_count} files, "
        f"removed {dropped_rows} rows."
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("数据质量检查任务发生异常。")
        raise SystemExit(1)
