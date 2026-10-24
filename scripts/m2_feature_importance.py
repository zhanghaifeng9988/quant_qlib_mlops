#!/usr/bin/env python
"""M2b-3: 提取 7 窗口 LightGBM 特征重要性并对比。"""
import json
import pickle
from pathlib import Path

import pandas as pd
from mlflow import artifacts as mlflow_artifacts
from mlflow.tracking import MlflowClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_URI = f"sqlite:///{PROJECT_ROOT}/mlflow.db"
RUN_IDS = PROJECT_ROOT / "logs" / "walkforward" / "run_ids.txt"
OUT_DIR = PROJECT_ROOT / "data" / "monitoring" / "feature_importance"


def get_feature_names():
    from qlib.contrib.data.loader import Alpha158DL
    _, names = Alpha158DL.get_feature_config()
    return list(names)


def load_model(client, run_id):
    """加载 qrun 默认保存的 params.pkl（模型实例）。"""
    local = mlflow_artifacts.download_artifacts(
        run_id=run_id, artifact_path="params.pkl", tracking_uri=TRACKING_URI
    )
    with open(local, "rb") as f:
        return pickle.load(f)

# 从训练好的 LightGBM 模型里提取特征重要性（gain），并正确地把特征名对应上去，
# # 返回一个按重要性降序排列的 pandas Series。
def extract_importance(model, feature_names):
    booster = model.model  # 是它内部真正的 lightgbm.Booster（原生 LightGBM 模型）。
    raw_names = booster.feature_name() # 读取 booster 里记录的特征名
    if all(n.startswith("Column_") for n in raw_names): # 如果所有名字都是 Column_ 开头
        if len(raw_names) != len(feature_names):
            raise RuntimeError(
                f"feature count mismatch: booster={len(raw_names)} vs Alpha158={len(feature_names)}"
            )
        names = feature_names
    else:
        names = raw_names
    # 获取特征重要性，gain = 该特征在所有分裂中带来的平均信息增益
    imp = booster.feature_importance(importance_type="gain")
    #组装成带名字索引的 Series 并排序
    return pd.Series(imp, index=names).sort_values(ascending=False)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feature_names = get_feature_names()
    print(f"[features] {len(feature_names)} Alpha158 factors loaded")

    client = MlflowClient(tracking_uri=TRACKING_URI)
    all_imp = {}

    for line in RUN_IDS.read_text().splitlines():# 把整段文本按换行符切成一行一行的列表
        if not line.strip():# 去掉行首行尾的空白字符，并判断是否为空
            continue
        w, rid = line.split()
        print(f"[{w}] run_id={rid[:12]}")
        try:
            model = load_model(client, rid)
            imp = extract_importance(model, feature_names)
        except Exception as e:
            print(f"  [fail] {e}")
            continue
        imp.to_json(OUT_DIR / f"{w}_importance.json", orient="index", indent=2)
        all_imp[w] = imp
        print(f"  top5: {list(imp.head(5).index)}")

    if not all_imp:
        print("[abort] no model loaded")
        return

    # 汇总：行=因子，列=窗口，值=该窗口内的相对重要性占比
    df = pd.DataFrame(all_imp).fillna(0)
    # 按列做归一化：每一列的所有元素除以该列的总和，使每列之和变为 1。
    df_norm = df.div(df.sum(axis=0), axis=1)
    df_norm.to_csv(OUT_DIR / "importance_matrix.csv")
    # 按行做排名：越重要的特征排名越靠前
    ranks = df_norm.rank(ascending=False, axis=0)

    # 跨窗口稳定 Top20：每个窗口都进前 20 的因子
    stable_top20 = ranks[(ranks <= 20).all(axis=1)].index.tolist()

    # w1 top20 在失效窗口(w3/w5)掉到 100 名以后的因子
    dropped = []
    # 判断：ranks.columns 里是否同时包含 "w1"、"w3"、"w5" 这三列
    if {"w1", "w3", "w5"}.issubset(ranks.columns):
        for f in ranks[ranks["w1"] <= 20].index:
            r3, r5 = int(ranks.loc[f, "w3"]), int(ranks.loc[f, "w5"])
            if r3 > 100 or r5 > 100:
                dropped.append({
                    "factor": f,
                    "rank_w1": int(ranks.loc[f, "w1"]),
                    "rank_w3": r3,
                    "rank_w5": r5,
                })

    result = {
        "windows": list(all_imp.keys()),
        "n_features": len(feature_names),
        "stable_top20": stable_top20,
        "w1_top20_dropped_in_failure_windows": dropped,
        "top20_by_window": {w: list(all_imp[w].head(20).index) for w in all_imp},
    }
    (OUT_DIR / "analysis.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"\n[done] {OUT_DIR}")
    print(f"  stable_top20 ({len(stable_top20)}): {stable_top20}")
    print(f"  dropped ({len(dropped)}): {[d['factor'] for d in dropped]}")


if __name__ == "__main__":
    main()