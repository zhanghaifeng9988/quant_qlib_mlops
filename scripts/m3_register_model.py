#!/usr/bin/env python
"""M3a: 将 Qlib 模型封装为 MLflow PyFunc，注册并设置 Alias。"""
import argparse
import pickle
from pathlib import Path

import mlflow
import mlflow.pyfunc
from mlflow import artifacts as mlflow_artifacts
from mlflow.tracking import MlflowClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRACKING_URI = f"sqlite:///{PROJECT_ROOT}/mlflow.db"
REGISTRY_MODEL_NAME = "us-stock-lgbm-1d"  
ALIAS_NAME = "candidate"                  # 先给 candidate 别名

# 把“黑盒”模型包装成了一个 MLflow 认识的标准化对象。
class QlibLGBWrapper(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        with open(context.artifacts["qlib_model"], "rb") as f:
            self.model = pickle.load(f)

    def predict(self, context, model_input):
        # 直接调用底层 LightGBM booster 进行预测
        if hasattr(self.model, "model") and hasattr(self.model.model, "predict"):
            return self.model.model.predict(model_input)
        return self.model.predict(model_input)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="源 MLflow run id 或 window (如 w1)")
    args = parser.parse_args()

    mlflow.set_tracking_uri(TRACKING_URI)
    client = MlflowClient()

    # 如果传入的是窗口名 (w1-w7)，去文件里查真正的 run_id；否则当做 run_id 直接用
    rid = args.run_id
    if rid.startswith("w"):
        run_ids_path = PROJECT_ROOT / "logs" / "walkforward" / "run_ids.txt"
        for line in run_ids_path.read_text().splitlines():
            if line.strip() and line.startswith(rid):
                rid = line.split()[1]
                break
        else:
            raise ValueError(f"找不到 {args.run_id} 对应的 run_id")

    print(f"[info] 源 Run ID: {rid}")

    # 读取源 run 的指标
    source_run = client.get_run(rid)
    ic = source_run.data.metrics.get("IC", 0.0)
    rank_ic = source_run.data.metrics.get("Rank IC", 0.0)
    print(f"[info] IC: {ic:.4f}, Rank IC: {rank_ic:.4f}")

    # 下载 params.pkl
    print("[download] 提取源模型 artifact...")
    model_path = mlflow_artifacts.download_artifacts(
        run_id=rid, artifact_path="params.pkl", tracking_uri=TRACKING_URI
    )

    # 封装并注册
    mlflow.set_experiment("model_registry")
    with mlflow.start_run(run_name=f"register_from_{rid[:8]}") as run:
        mlflow.set_tag("source_run_id", rid)
        mlflow.log_metric("source_IC", ic)
        
        print("[register] 封装为 PyFunc 并注册...")
        model_info = mlflow.pyfunc.log_model(
            name="model",
            python_model=QlibLGBWrapper(),
            artifacts={"qlib_model": model_path},
            registered_model_name=REGISTRY_MODEL_NAME,
            pip_requirements=["mlflow", "pandas", "numpy", "lightgbm"],
        )
        print(f"[success] 注册 Run ID: {run.info.run_id}")

    # 获取最新版本号
    versions = client.search_model_versions(f"name='{REGISTRY_MODEL_NAME}'")
    latest_version = max([int(v.version) for v in versions])
    
    # 设置 alias 和 description
    client.set_registered_model_alias(
        name=REGISTRY_MODEL_NAME,
        alias=ALIAS_NAME,
        version=latest_version,
    )
    
    client.update_model_version(
        name=REGISTRY_MODEL_NAME,
        version=latest_version,
        description=f"source_run_id={rid}, IC={ic:.4f}, Rank IC={rank_ic:.4f}"
    )

    print(f"[done] 模型已入库，版本 {latest_version}，别名: @{ALIAS_NAME}")


if __name__ == "__main__":
    main()