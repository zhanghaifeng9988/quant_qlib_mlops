#!/usr/bin/env python
"""将 Qlib 模型封装为 MLflow PyFunc，注册并设置 Alias。"""

import argparse
import importlib.metadata
import pickle
import platform
from datetime import datetime, timezone
from pathlib import Path

import mlflow
import mlflow.pyfunc
from mlflow import artifacts as mlflow_artifacts
from mlflow.tracking import MlflowClient


# --------------------------------------------------
# 1. 基础配置
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TRACKING_URI = f"sqlite:///{(PROJECT_ROOT / 'mlflow.db').as_posix()}"

REGISTRY_MODEL_NAME = "us-stock-lgbm-1d"
ALIAS_NAME = "candidate"

# 模型推理所需的主要依赖。
# 注册时读取当前开发环境中的实际安装版本。
MODEL_DEPENDENCIES = [
    "mlflow",
    "pyqlib",
    "lightgbm",
    "numpy",
    "pandas",
]


# --------------------------------------------------
# 2. Qlib 模型封装
# --------------------------------------------------

class QlibLGBWrapper(mlflow.pyfunc.PythonModel):

    def load_context(self, context):
        model_path = context.artifacts["qlib_model"]

        with open(model_path, "rb") as f:
            self.model = pickle.load(f)

    def predict(self, context, model_input):

        # Qlib LightGBM 模型通过底层 Booster 预测。
        if (
            hasattr(self.model, "model")
            and hasattr(self.model.model, "predict")
        ):
            return self.model.model.predict(model_input)

        return self.model.predict(model_input)


def validate_source_run(client, source_run_id):
    """校验待注册的源训练 Run，并下载其模型文件。"""

    # 1. 确认 Run 存在
    try:
        source_run = client.get_run(source_run_id)
    except Exception as exc:
        raise ValueError(
            f"MLflow 中不存在源训练 Run：{source_run_id}"
        ) from exc

    # 2. 确认 Run 属于指定训练实验
    experiment = client.get_experiment(
        source_run.info.experiment_id
    )

    if experiment is None or experiment.name != "america_stock_strategy":
        actual_name = experiment.name if experiment else "未知实验"
        raise ValueError(
            f"源 Run 不属于 america_stock_strategy 实验，"
            f"实际所属实验：{actual_name}"
        )

    # 3. 确认训练已经成功完成
    if source_run.info.status != "FINISHED":
        raise ValueError(
            f"源 Run 状态不是 FINISHED："
            f"{source_run.info.status}"
        )

    # 4. 检查是否已经注册过
    versions = client.search_model_versions(
        f"name='{REGISTRY_MODEL_NAME}'"
    )

    for version in versions:
        registered_source_run_id = version.tags.get("source_run_id")
        description = version.description or ""

        # 同时兼容已有版本的 Tag 和 Description
        if (
            registered_source_run_id == source_run_id
            or f"source_run_id={source_run_id}" in description
        ):
            raise ValueError(
                f"该训练 Run 已经注册过，禁止重复注册。\n"
                f"源训练 Run ID：{source_run_id}\n"
                f"已有模型版本：v{version.version}"
            )

    # 5. 确认源 Run 包含 params.pkl
    artifacts = client.list_artifacts(source_run_id)

    has_model = any(
        artifact.path == "params.pkl" and not artifact.is_dir
        for artifact in artifacts
    )

    if not has_model:
        raise FileNotFoundError(
            f"源训练 Run 中不存在 params.pkl：{source_run_id}"
        )

    # 6. 下载模型文件，并确认文件真实存在
    model_path = mlflow_artifacts.download_artifacts(
        run_id=source_run_id,
        artifact_path="params.pkl",
        tracking_uri=TRACKING_URI,
    )

    model_path = Path(model_path)

    if not model_path.is_file():
        raise FileNotFoundError(
            f"模型文件下载失败或文件不存在：{model_path}"
        )

    print("[validation] 源训练 Run 校验通过")
    print(f"[validation] Run ID：{source_run_id}")
    print(f"[validation] 实验名称：{experiment.name}")
    print(f"[validation] 模型文件：{model_path}")

    return source_run, model_path


# --------------------------------------------------
# 3. 获取模型环境依赖版本
# --------------------------------------------------

def get_model_requirements():

    requirements = []

    for package in MODEL_DEPENDENCIES:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                f"模型依赖未安装，无法注册模型：{package}"
            ) from exc

        requirements.append(f"{package}=={version}")

    return requirements


# --------------------------------------------------
# 5. 主流程：注册模型
# --------------------------------------------------

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--run-id",
        required=True,
        help="需要注册的MLflow源训练Run ID",
    )

    args = parser.parse_args()

    # Tracking 和 Registry 使用同一个 SQLite 数据库。
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_registry_uri(TRACKING_URI)

    client = MlflowClient(
        tracking_uri=TRACKING_URI,
        registry_uri=TRACKING_URI,
    )

    rid = args.run_id

    print(f"[info] 源 Run ID: {rid}")

    # --------------------------------------------------
    # 读取源 Run 信息
    # --------------------------------------------------

    source_run, model_path = validate_source_run(client, rid)

    ic = source_run.data.metrics.get("IC", 0.0)
    rank_ic = source_run.data.metrics.get("Rank IC", 0.0)

    source_experiment_id = source_run.info.experiment_id
    source_run_name = source_run.data.tags.get(
        "mlflow.runName", ""
    )

    print(f"[info] IC: {ic:.4f}")
    print(f"[info] Rank IC: {rank_ic:.4f}")

    # --------------------------------------------------
    # 固定模型依赖版本
    # --------------------------------------------------

    requirements = get_model_requirements()

    print("[info] 模型依赖环境：")

    for requirement in requirements:
        print(f"  - {requirement}")

    # --------------------------------------------------
    # 创建注册实验
    # --------------------------------------------------

    mlflow.set_experiment("model_registry")

    with mlflow.start_run(
        run_name=f"register_from_{rid[:8]}"
    ) as run:

        # 注册任务本身的记录。
        mlflow.set_tag("source_run_id", rid)
        mlflow.set_tag(
            "source_experiment_id",
            source_experiment_id,
        )
        mlflow.set_tag(
            "source_run_name",
            source_run_name,
        )
        mlflow.set_tag(
            "model_type",
            "Qlib LightGBM",
        )

        mlflow.set_tag(
            "python_version",
            platform.python_version(),
        )

        mlflow.log_metric("source_IC", ic)
        mlflow.log_metric("source_Rank_IC", rank_ic)

        # --------------------------------------------------
        # 注册模型
        # --------------------------------------------------

        print("[register] 封装为 PyFunc 并注册...")

        model_info = mlflow.pyfunc.log_model(
            name="model",
            python_model=QlibLGBWrapper(),
            artifacts={
                "qlib_model": str(model_path),
            },
            registered_model_name=REGISTRY_MODEL_NAME,
            pip_requirements=requirements,
        )

        registration_run_id = run.info.run_id

        # 直接获取本次注册返回的版本号。
        registered_version = model_info.registered_model_version

        if registered_version is None:
            raise RuntimeError(
                "MLflow 未返回注册版本号，无法安全设置模型 Alias。"
            )

    # --------------------------------------------------
    # 更新模型版本信息
    # --------------------------------------------------

    version = str(registered_version)

    version_description = (
        f"source_run_id={rid}, "
        f"source_experiment_id={source_experiment_id}, "
        f"IC={ic:.6f}, "
        f"Rank_IC={rank_ic:.6f}, "
        f"model_type=Qlib LightGBM"
    )

    client.update_model_version(
        name=REGISTRY_MODEL_NAME,
        version=version,
        description=version_description,
    )

    # 补充模型版本标签，便于后续追溯。
    version_tags = {
        "source_run_id": rid,
        "source_experiment_id": source_experiment_id,
        "source_run_name": source_run_name,
        "registration_run_id": registration_run_id,
        "model_type": "Qlib LightGBM",
        "source_IC": str(ic),
        "source_Rank_IC": str(rank_ic),
        "python_version": platform.python_version(),
        "registered_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "model_requirements": ";".join(requirements),
    }

    for key, value in version_tags.items():
        client.set_model_version_tag(
            name=REGISTRY_MODEL_NAME,
            version=version,
            key=key,
            value=str(value),
        )

    # --------------------------------------------------
    # 设置 Alias
    # --------------------------------------------------

    client.set_registered_model_alias(
        name=REGISTRY_MODEL_NAME,
        alias=ALIAS_NAME,
        version=version,
    )

    print("================================================")
    print("[done] 模型注册成功")
    print(f"[done] 模型名称：{REGISTRY_MODEL_NAME}")
    print(f"[done] 模型版本：{version}")
    print(f"[done] 模型别名：@{ALIAS_NAME}")
    print(f"[done] 来源 Run ID：{rid}")
    print(f"[done] IC：{ic:.6f}")
    print(f"[done] Rank IC：{rank_ic:.6f}")
    print("================================================")


if __name__ == "__main__":
    main()
