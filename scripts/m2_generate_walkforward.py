#!/usr/bin/env python
"""M2b-1: 从 configs/workflow_config_lightgbm_Alpha158_us.yaml 模板生成 7 个 walk-forward 窗口 YAML。"""
from pathlib import Path
import copy
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "configs" / "workflow_config_lightgbm_Alpha158_us.yaml"
OUT_DIR = ROOT / "configs" / "walkforward"

# 7 窗口规则（已在 architecture.md 附录 B.6 锁定）
WINDOWS = [
    # (id, train_end, valid_start, valid_end, test_start, test_end)
    ("w1", "2017-12-31", "2018-01-01", "2018-12-31", "2019-01-01", "2019-12-31"),
    ("w2", "2018-12-31", "2019-01-01", "2019-12-31", "2020-01-01", "2020-12-31"),
    ("w3", "2019-12-31", "2020-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
    ("w4", "2020-12-31", "2021-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
    ("w5", "2021-12-31", "2022-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
    ("w6", "2022-12-31", "2023-01-01", "2023-12-31", "2024-01-01", "2024-12-31"),
    ("w7", "2023-12-31", "2024-01-01", "2024-12-31", "2025-01-01", "2026-10-16"),
    # 2026年11月4日，新增 W8（注意 test_end 是最新美股数据时间 2026-11-03）
    ("w8", "2024-12-31", "2025-01-01", "2025-12-31", "2026-01-01", "2026-11-02"),
]

TRAIN_START = "2011-10-24"
DATA_END = "2026-11-03"

# 
def build_window(template: dict, wid: str, train_end, valid_start, valid_end, test_start, test_end):
    cfg = copy.deepcopy(template)
    init = cfg["qlib_init"]
    init["exp_manager"]["kwargs"]["default_exp_name"] = "quant_qlib_mlops"

    # 1. data_handler_config：fit 段 = train 段
    dh = cfg["data_handler_config"]
    dh["start_time"] = TRAIN_START
    dh["end_time"] = DATA_END
    dh["fit_start_time"] = TRAIN_START
    dh["fit_end_time"] = train_end

    # 2. dataset.segments
    seg = cfg["task"]["dataset"]["kwargs"]["segments"]
    seg["train"] = [TRAIN_START, train_end]
    seg["valid"] = [valid_start, valid_end]
    seg["test"] = [test_start, test_end]

    # 3. 回测区间 = test 区间
    bt = cfg["port_analysis_config"]["backtest"]
    bt["start_time"] = test_start
    bt["end_time"] = test_end

    return cfg


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    template = yaml.safe_load(TEMPLATE.read_text())

    for wid, train_end, vs, ve, ts, te in WINDOWS:
        cfg = build_window(template, wid, train_end, vs, ve, ts, te)
        out = OUT_DIR / f"{wid}.yaml"
        out.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        print(f"[gen] {out.name}: train {TRAIN_START}->{train_end}, "
              f"valid {vs}->{ve}, test {ts}->{te}")

    print(f"\n[done] {len(WINDOWS)} configs in {OUT_DIR}")


if __name__ == "__main__":
    main()