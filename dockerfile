# syntax=docker/dockerfile:1

# 使用官方 Python 3.11 瘦身版作为基础镜像
#FROM docker.m.daocloud.io/library/python:3.11-slim

# 安装系统依赖（Qlib 编译/运行需要）
#RUN apt-get update && apt-get install -y --no-install-recommends \
#    build-essential \
#    curl \
#    && rm -rf /var/lib/apt/lists/*

# 安装 uv（极速 Python 包管理器）
#COPY --from=ghcr.nju.edu.cn/astral-sh/uv:latest /uv /uvx /bin/

# 设置工作目录
#WORKDIR /app

# 先复制依赖定义文件，利用 Docker 缓存层
#COPY pyproject.toml uv.lock ./

# 使用 uv 安装依赖（只安装主依赖，不装 dev）
# 把 uv 下载的依赖包缓存到宿主机上，下次构建时直接复用，不用重新下载，但这份缓存不会被打进最终镜像里。
#RUN --mount=type=cache,target=/root/.cache/uv \
#      uv sync --frozen --no-dev --no-install-project

# 把项目代码复制进去
#COPY . .

# 暴露 FastAPI 端口
#EXPOSE 8000

# 直接使用 .venv 里的 uvicorn 启动，跳过 uv 的运行前检查
#ENV PATH="/app/.venv/bin:$PATH"
#CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# syntax=docker/dockerfile:1

# ========== 阶段 1：构建依赖 ==========
FROM docker.m.daocloud.io/library/python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.nju.edu.cn/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./

# 关键：加 --no-install-package 屏蔽 jupyter 全家桶
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" \
    uv sync --frozen --no-dev --no-install-project \
    --no-install-package jupyterlab \
    --no-install-package jupyter-server \
    --no-install-package ipykernel \
    --no-install-package nbconvert \
    --no-install-package nbformat \
    --no-install-package notebook \
    --no-install-package jupyter

# ========== 阶段 2：极简运行环境 ==========
FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

# 清理推理服务用不到的“死重”包
RUN rm -rf /app/.venv/lib/python3.11/site-packages/llvmlite \
           /app/.venv/lib/python3.11/site-packages/numba \
           /app/.venv/lib/python3.11/site-packages/jedi \
           /app/.venv/lib/python3.11/site-packages/babel \
           /app/.venv/lib/python3.11/site-packages/curl_cffi \
           /app/.venv/lib/python3.11/site-packages/yfinance

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]