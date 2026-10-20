#!/usr/bin/env python
"""M1 Step 5: 生成数据血缘 manifest。"""
""" 给刚刚转换好的 Qlib 二进制数据，拍一张“带有身份证号（哈希值）的数码照片”。 """
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QLIB_DIR = ROOT / "data" / "qlib_bin" / "us_data"
UNIVERSE = ROOT / "data" / "universe" / "universes.yaml"
IND_MAP = ROOT / "data" / "universe" / "industry_mapping.csv"
MANIFEST = QLIB_DIR / "manifest.json"


def sha256_dir(path: Path) -> str:
    """对目录内所有文件按相对路径排序后计算整体哈希。"""
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file())
    for f in files:
        rel = f.relative_to(path).as_posix()
        h.update(rel.encode())
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def main():
    u = yaml.safe_load(open(UNIVERSE))
    instruments_file = QLIB_DIR / "instruments" / "all.txt"
    with open(instruments_file) as f:
        instrument_lines = [ln for ln in f if ln.strip()]

    calendar = (QLIB_DIR / "calendars" / "day.txt").read_text().strip().splitlines()

# 构建超级详细的 manifest 字典（核心产物）
    manifest = {
        "snapshot_date": u["snapshot_date"],  # 股票池的日期
        "generated_at": datetime.now(timezone.utc).isoformat(), # 这份 manifest 生成的时间
        "universe_snapshot_id": f"us_{u['snapshot_date']}_dev{u['dev_universe']['count']}", # 给它起个唯一的名字
        "target_universe_count": u["target_universe"]["count"], # 原始大池子多少股票
        "dev_universe_count": u["dev_universe"]["count"], # 实际开发池多少股票（500）
        "dev_universe_rule": u["dev_universe"]["rule"],  ## 筛选规则（按市值前500）
        "industry_mapping": {   # 行业映射表的hash
            "file": str(IND_MAP.relative_to(ROOT)),  # 绝对路径转换为相对路径
            "sha256": hashlib.sha256(IND_MAP.read_bytes()).hexdigest(),
        },
        "qlib_data": {  # Qlib 数据的具体情况
            "provider_uri": str(QLIB_DIR.relative_to(ROOT)),
            "instrument_count": len(instrument_lines),
            "calendar_start": calendar[0],
            "calendar_end": calendar[-1],
            "calendar_days": len(calendar),
            "fields": ["open", "high", "low", "close", "volume", "factor"],
            "features_sha256": sha256_dir(QLIB_DIR / "features"),
            "instruments_sha256": sha256_dir(QLIB_DIR / "instruments"),
            "calendars_sha256": sha256_dir(QLIB_DIR / "calendars"),
        },
        "sources": u["sources"],
    }

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[manifest] {MANIFEST}")
    print(json.dumps(manifest, indent=2, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()