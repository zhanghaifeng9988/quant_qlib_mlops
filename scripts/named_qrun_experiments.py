#!/usr/bin/env python
# 这是对mlflow中的实验的名字命名
# 先保留这个脚本，后续如果需要对实验进行命名，可以参考这个脚本
import qlib
from qlib.workflow import R
from qlib.model.trainer import task_train
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "configs" / "workflow_config_lightgbm_Alpha158_us.yaml"
PROVIDER_URI = ROOT / "data" / "qlib_bin" / "us_data"

qlib.init(
    provider_uri=str(PROVIDER_URI),
    region="us",
    exp_manager={
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {
            "uri": "sqlite:///" + str(ROOT / "mlflow.db"),
            "default_exp_name": "workflow1",
        },
    },
)

# 2. 读取 YAML 为字典
with open(TEMPLATE, "r") as f:
    config = yaml.safe_load(f)

# 3. 执行yaml配置中的任务，并给与实验名称
task_train(config["task"], experiment_name="workflow1")
