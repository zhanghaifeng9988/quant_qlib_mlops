#!/usr/bin/env python
"""M1 关闭，验收脚本：数据可读、manifest 一致、内存安全。
读取 manifest，核对日历天数是否匹配、instruments 数量对不对、抽三只股票拉取数据看有没有缺失，并检查内存峰值。
"""

import json
import resource
import sys
from pathlib import Path

import qlib
from qlib.constant import REG_US
from qlib.data import D

ROOT = Path(__file__).resolve().parent.parent
QLIB_DIR = ROOT / "data" / "qlib_bin" / "us_data"
MANIFEST = QLIB_DIR / "manifest.json"

# 监控程序自身内存占用
def max_rss_gb() -> float:
    # Linux 下 ru_maxrss 单位是 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def main():
    m = json.load(open(MANIFEST))
    expected_count = m["qlib_data"]["instrument_count"]
    expected_start = m["qlib_data"]["calendar_start"]
    expected_end = m["qlib_data"]["calendar_end"]

    qlib.init(provider_uri=str(QLIB_DIR), region=REG_US)

    # 股票总交易日天数，断言方式 对比，否则提示错误信息
    # D.calendar 向 Qlib 引擎发起请求：请把 expected_start 到 expected_end 这段时间内的所有交易日都给我列出来。
    cal = D.calendar(start_time=expected_start, end_time=expected_end)
    assert len(cal) == m["qlib_data"]["calendar_days"], \
        f"calendar mismatch: {len(cal)} vs {m['qlib_data']['calendar_days']}"

    # 股票数量 断言方式对比
    # splitlines() 按照换行符分割
    inst_file = QLIB_DIR / "instruments" / "all.txt"
    inst = [ln.split("\t")[0] for ln in inst_file.read_text().splitlines() if ln.strip()]
    assert len(inst) == expected_count, f"instrument mismatch: {len(inst)}"

    # 3. 抽三只股票，查验数据完整性
    sample = ["AAPL", "MSFT", "NVDA"]
    df = D.features(
        sample,
        ["$close", "$volume", "$factor"],
        start_time="2020-01-01",
        end_time="2021-01-01",
    )
    assert not df.empty, "features empty"
    for sym in sample:
        # 每只股票 2020 年非空交易日数量
        n = df.xs(sym, level="instrument")["$close"].notna().sum()
        assert n > 240, f"{sym} only {n} valid close in 2020"
        print(f"[ok] {sym}: {n} valid trading days in 2020")

    # 4. 内存峰值
    peak = max_rss_gb()
    print(f"[mem] peak RSS: {peak:.2f} GB")
    assert peak < 3.0, f"peak RSS {peak:.2f} GB exceeds 3 GB safety line"

    print("\n=== M1 ACCEPTANCE PASSED ===")
    print(f"  universe: {m['universe_snapshot_id']}")
    print(f"  instruments: {expected_count}")
    print(f"  calendar: {expected_start} -> {expected_end} ({len(cal)} days)")
    print(f"  peak RSS: {peak:.2f} GB")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n=== M1 ACCEPTANCE FAILED ===\n{e}", file=sys.stderr)
        sys.exit(1)