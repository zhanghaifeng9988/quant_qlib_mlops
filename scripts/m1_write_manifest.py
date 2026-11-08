#!/usr/bin/env python
"""生成 Qlib 数据集 Manifest，并保存不可覆盖的历史血缘记录。"""

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ============================================================
# 1. 路径配置
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT / "data" / "raw" / "us_daily"
QLIB_DIR = ROOT / "data" / "qlib_bin" / "us_data"

UNIVERSE = ROOT / "data" / "universe" / "universes.yaml"
IND_MAP = ROOT / "data" / "universe" / "industry_mapping.csv"

MANIFEST = QLIB_DIR / "manifest.json"
HISTORY_DIR = QLIB_DIR / "lineage" / "runs"


# ============================================================
# 2. 哈希计算
# ============================================================

def sha256_file(path: Path) -> str:
    """计算单个文件的 SHA-256。"""

    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def sha256_dir(path: Path) -> str:
    """按相对路径排序，计算目录内所有文件的整体哈希。"""

    if not path.is_dir():
        raise FileNotFoundError(f"目录不存在：{path}")

    h = hashlib.sha256()

    files = sorted(p for p in path.rglob("*") if p.is_file())

    for file_path in files:
        relative_path = file_path.relative_to(path).as_posix()
        h.update(relative_path.encode("utf-8"))

        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)

    return h.hexdigest()


# ============================================================
# 3. 原子写入 JSON
# ============================================================

def write_json_atomic(path: Path, data: dict):
    """
    先写入同目录临时文件，再原子替换目标文件。
    避免程序中断时留下不完整的 JSON 文件。
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
    )

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, path)

    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


# ============================================================
# 4. 获取 Git 版本
# ============================================================

def get_git_commit():
    """获取当前代码对应的 Git commit。"""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# ============================================================
# 5. 建立历史版本链
# ============================================================

def get_parent_run_id(old_manifest):
    """
    获取上一个历史版本。

    第一次升级时，旧 Manifest 没有 run_id，
    将其保存为 baseline 历史记录。
    """

    if not old_manifest:
        return None

    old_lineage = old_manifest.get("lineage", {})
    parent_run_id = old_lineage.get("run_id")

    if parent_run_id:
        # 确保当前 Manifest 指向的历史记录存在
        history_file = HISTORY_DIR / f"{parent_run_id}.json"

        if not history_file.exists():
            write_json_atomic(history_file, old_manifest)

        return parent_run_id

    # 旧 Manifest 没有 run_id，创建初始历史版本
    old_content = json.dumps(
        old_manifest,
        sort_keys=True,
        ensure_ascii=False,
    )

    baseline_hash = hashlib.sha256(
        old_content.encode("utf-8")
    ).hexdigest()[:12]

    baseline_run_id = f"baseline_{baseline_hash}"

    baseline_file = HISTORY_DIR / f"{baseline_run_id}.json"

    if not baseline_file.exists():
        baseline_manifest = dict(old_manifest)

        baseline_manifest["lineage"] = {
            "run_id": baseline_run_id,
            "parent_run_id": None,
            "status": "baseline",
            "recorded_at": old_manifest.get("generated_at"),
            "history_file": (
                f"lineage/runs/{baseline_run_id}.json"
            ),
        }

        write_json_atomic(baseline_file, baseline_manifest)

        print(
            f"[lineage] 已保存原有 Manifest：{baseline_run_id}"
        )

    return baseline_run_id


# ============================================================
# 6. 生成 Manifest
# ============================================================

def main():

    # 检查必要输入
    for path in (RAW_DIR, QLIB_DIR, UNIVERSE, IND_MAP):
        if not path.exists():
            raise FileNotFoundError(f"必要文件或目录不存在：{path}")

    instruments_file = QLIB_DIR / "instruments" / "all.txt"
    calendar_file = QLIB_DIR / "calendars" / "day.txt"
    features_dir = QLIB_DIR / "features"
    instruments_dir = QLIB_DIR / "instruments"
    calendars_dir = QLIB_DIR / "calendars"

    for path in (
        instruments_file,
        calendar_file,
        features_dir,
        instruments_dir,
        calendars_dir,
    ):
        if not path.exists():
            raise FileNotFoundError(f"Qlib 数据文件或目录不存在：{path}")

    # 读取股票池配置
    with UNIVERSE.open("r", encoding="utf-8") as f:
        universe = yaml.safe_load(f)

    # 读取 Qlib 股票清单
    with instruments_file.open("r", encoding="utf-8") as f:
        instrument_lines = [
            line for line in f if line.strip()
        ]

    # 读取交易日历
    calendar = [
        line.strip()
        for line in calendar_file.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    if not calendar:
        raise ValueError("Qlib 交易日历为空")

    # 获取旧 Manifest
    old_manifest = None

    if MANIFEST.exists():
        with MANIFEST.open("r", encoding="utf-8") as f:
            old_manifest = json.load(f)

    # 确定父版本
    parent_run_id = get_parent_run_id(old_manifest)

    # 创建唯一运行编号
    now = datetime.now(timezone.utc)

    run_id = (
        now.strftime("%Y%m%dT%H%M%S%fZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    generated_at = now.isoformat()

    # 原始行情文件数量
    raw_csv_files = list(RAW_DIR.glob("*.csv"))

    if not raw_csv_files:
        raise ValueError("原始行情目录中没有 CSV 文件")

    # ========================================================
    # 构建当前 Manifest
    # ========================================================

    manifest = {
        "snapshot_date": universe["snapshot_date"],
        "generated_at": generated_at,

        "universe_snapshot_id": (
            f"us_{universe['snapshot_date']}"
            f"_dev{universe['dev_universe']['count']}"
        ),

        "target_universe_count": (
            universe["target_universe"]["count"]
        ),

        "dev_universe_count": (
            universe["dev_universe"]["count"]
        ),

        "dev_universe_rule": (
            universe["dev_universe"]["rule"]
        ),

        "industry_mapping": {
            "file": str(IND_MAP.relative_to(ROOT)),
            "sha256": sha256_file(IND_MAP),
        },

        "raw_data": {
            "directory": str(RAW_DIR.relative_to(ROOT)),
            "csv_file_count": len(raw_csv_files),
            "sha256": sha256_dir(RAW_DIR),
        },

        "qlib_data": {
            "provider_uri": str(QLIB_DIR.relative_to(ROOT)),
            "instrument_count": len(instrument_lines),
            "calendar_start": calendar[0],
            "calendar_end": calendar[-1],
            "calendar_days": len(calendar),
            "fields": [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "factor",
            ],
            "features_sha256": sha256_dir(features_dir),
            "instruments_sha256": sha256_dir(instruments_dir),
            "calendars_sha256": sha256_dir(calendars_dir),
        },

        "sources": universe["sources"],

        "lineage": {
            "run_id": run_id,
            "parent_run_id": parent_run_id,
            "status": "success",
            "recorded_at": generated_at,
            "history_file": (
                f"lineage/runs/{run_id}.json"
            ),
            "code_version": get_git_commit(),
        },
    }

    # ========================================================
    # 保存历史记录，再更新当前 Manifest
    # ========================================================

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    history_file = HISTORY_DIR / f"{run_id}.json"

    if history_file.exists():
        raise FileExistsError(
            f"历史记录已存在，拒绝覆盖：{history_file}"
        )

    # 第一步：保存独立历史记录
    write_json_atomic(history_file, manifest)

    # 第二步：更新当前 Manifest
    write_json_atomic(MANIFEST, manifest)

    print("=" * 55)
    print("[manifest] 更新成功")
    print(f"[manifest] 当前文件：{MANIFEST}")
    print(f"[lineage] 历史记录：{history_file}")
    print(f"[lineage] 当前 run_id：{run_id}")
    print(f"[lineage] 上一个 run_id：{parent_run_id}")
    print(f"[lineage] Qlib 股票数量：{len(instrument_lines)}")
    print(f"[lineage] 原始 CSV 数量：{len(raw_csv_files)}")
    print("=" * 55)


if __name__ == "__main__":
    main()
