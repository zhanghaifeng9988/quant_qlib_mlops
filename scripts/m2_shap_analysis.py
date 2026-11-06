#!/usr/bin/env python
"""M2b-4: SHAP 分析 W2 (2020 疫情窗口) - 通过 pred.pkl 索引按需拉取特征。"""

# 从 pred.pkl 随机抽 3000 个"股票-日"行标签 → 用这些标签涉及的股票列表和时间范围去 Qlib 拉特征（拉回的行数 > 3000）→ 按标签对齐筛出 3000 行 X → 保证列顺序与 feature_names 一致后算 SHAP。 
# 核心坑是：D.features 只支持"股票列表 + 时间范围"，不支持精确索引列表，所以中间多了"拉大块再筛"这一步。

import pickle
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from mlflow import artifacts as mlflow_artifacts
from mlflow.tracking import MlflowClient
import qlib
from qlib.constant import REG_US
from qlib.data import D

# 路径与配置
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_URI = f"sqlite:///{PROJECT_ROOT}/mlflow.db"
RUN_IDS = PROJECT_ROOT / "logs" / "walkforward" / "run_ids.txt"
OUT_DIR = PROJECT_ROOT / "data" / "monitoring" / "feature_importance"
QLIB_DATA_DIR = PROJECT_ROOT / "data" / "qlib_bin" / "us_data"

plt.switch_backend("Agg")

def get_feature_names():
    from qlib.contrib.data.loader import Alpha158DL
    _, names = Alpha158DL.get_feature_config()  # 丢弃 fields，只要 names。
    return list(names)

def main():
    # 1. 找 W2 的 run_id
    run_ids = {}
    for line in RUN_IDS.read_text().splitlines():
        if line.strip(): # 跳过空行
            w, rid = line.split()
            run_ids[w] = rid
    rid = run_ids["w2"]
    print(f"[w2] run_id={rid}")

    # client = MlflowClient(tracking_uri=TRACKING_URI)
    feature_names = get_feature_names()

    # 2. 加载模型
    print("[model] downloading params.pkl...")
    model_path = mlflow_artifacts.download_artifacts(
        run_id=rid, artifact_path="params.pkl", tracking_uri=TRACKING_URI
    )
    with open(model_path, "rb") as f:
        model = pickle.load(f)
    booster = model.model  # 取出内部原生 lgb.Booster
    print(f"[model] loaded, features={len(feature_names)}")

    # 3. 从 pred.pkl 提取测试集索引
    # 下载并还原预测结果 DataFrame。它的 index 是 (datetime, instrument) 或 (instrument, datetime) 的 MultiIndex，列是 score
    print("[data] downloading pred.pkl...")
    pred_path = mlflow_artifacts.download_artifacts(
        run_id=rid, artifact_path="pred.pkl", tracking_uri=TRACKING_URI
    )
    with open(pred_path, "rb") as f:
        pred_df = pickle.load(f)
    
    # 随机抽取 3000 个样本的位置索引
    # np.random.choice 对行位置均匀抽样，不会保证每只股票都被抽到，也不会保证每天都有样本。
    np.random.seed(42)
    sample_idx = np.random.choice(len(pred_df), min(3000, len(pred_df)), replace=False)
    target_index = pred_df.iloc[sample_idx].index  # .index 拿到这些行的 MultiIndex 标签（datetime + instrument）。
    print(f"[data] sampled {len(target_index)} instances from pred.pkl")


    # 4. 初始化 Qlib 并按需拉取特征
    print("[data] pulling features via Qlib D.features...")
    qlib.init(provider_uri=str(QLIB_DATA_DIR), region=REG_US)
    
    from qlib.contrib.data.loader import Alpha158DL
    fields, _ = Alpha158DL.get_feature_config()
    
    
    # 提取行索引
    idx = pred_df.iloc[sample_idx].index
    # 把第 0 层转成 Timestamp，第 1 层保留，重建 MultiIndex，命名 ['datetime', 'instrument']，排序。
    target_index = pd.MultiIndex.from_arrays(
        [pd.to_datetime(idx.get_level_values(0)), idx.get_level_values(1)],
        names=['datetime', 'instrument']
    ).sort_values()
    
    # 按抽样涉及的股票列表和时间范围拉特征。
    df = D.features(
        instruments=target_index.get_level_values('instrument').unique().tolist(),
        fields=fields,
        start_time=target_index.get_level_values('datetime').min().strftime('%Y-%m-%d'),
        end_time=target_index.get_level_values('datetime').max().strftime('%Y-%m-%d'),
    )
    
    # 确保 df 的索引也是 Timestamp
    # 2. 确保 Qlib 返回的 df 的索引层级正确对齐
    # 先交换层级顺序，强制变为 (datetime, instrument)
    if df.index.names == ['instrument', 'datetime']:
        df = df.swaplevel('instrument', 'datetime').sort_index()
    
    # 然后再根据名字转换层级类型
    df.index = df.index.set_levels(pd.to_datetime(df.index.levels[df.index.names.index('datetime')]), level='datetime')

    # 尝试对齐数据
    try:
        X = df.loc[target_index].values
    except KeyError as e:
        # 如果仍然报错，改用 merge 关联
        print(f"[debug] direct loc failed, trying merge...")
        # 把 df 重置索引，合并后再取
        df_reset = df.reset_index()
        target_df = pd.DataFrame(index=target_index).reset_index()
        merged = pd.merge(target_df, df_reset, on=['datetime', 'instrument'], how='left')
        X = merged[fields].values
        
    print(f"[data] X shape: {X.shape}")

    # 5. 计算 SHAP
    print("[shap] computing...")
    explainer = shap.TreeExplainer(booster)
    shap_values = explainer.shap_values(X)

    # 6. 画图并保存
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_path = OUT_DIR / "w2_shap_summary.png"
    
    plt.figure()
    shap.summary_plot(
        shap_values, X, feature_names=feature_names, 
        show=False, max_display=20
    )
    plt.tight_layout()
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    print(f"[saved] {plot_path}")

if __name__ == "__main__":
    main()