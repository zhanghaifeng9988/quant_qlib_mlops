# syntax=docker/dockerfile:1

# ========== 阶段 1：构建依赖 ==========
FROM docker.m.daocloud.io/library/python:3.11-slim AS builder

COPY --from=ghcr.nju.edu.cn/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./

# 仅安装模型服务所需依赖，不安装 research 和 dev 依赖组
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" \
    uv sync --frozen --only-group serving --no-install-project \
    --no-install-package jupyterlab --no-install-package jupyter-server \
    --no-install-package ipykernel --no-install-package nbconvert \
    --no-install-package nbformat --no-install-package notebook --no-install-package jupyter

# ========== 阶段 2：极简运行环境 ==========
FROM docker.m.daocloud.io/library/python:3.11-slim

# 🔥 核心修复：安装 LightGBM 必需的 OpenMP 运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制已瘦身的虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 只拷贝运行时代码
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


