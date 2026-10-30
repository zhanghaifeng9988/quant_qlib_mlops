# syntax=docker/dockerfile:1

# ========== 阶段 1：构建依赖 ==========
FROM docker.m.daocloud.io/library/python:3.11-slim AS builder

COPY --from=ghcr.nju.edu.cn/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" \
    uv sync --frozen --no-dev --no-install-project \
    --no-install-package jupyterlab --no-install-package jupyter-server \
    --no-install-package ipykernel --no-install-package nbconvert \
    --no-install-package nbformat --no-install-package notebook --no-install-package jupyter

# 🔥 关键点：在 builder 阶段就把死重删掉，这样 COPY 过去的才是真正的瘦身版
RUN rm -rf /app/.venv/lib/python3.11/site-packages/llvmlite \
           /app/.venv/lib/python3.11/site-packages/numba \
           /app/.venv/lib/python3.11/site-packages/jedi \
           /app/.venv/lib/python3.11/site-packages/babel \
           /app/.venv/lib/python3.11/site-packages/curl_cffi \
           /app/.venv/lib/python3.11/site-packages/yfinance

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


