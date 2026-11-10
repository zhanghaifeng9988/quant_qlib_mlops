# 公共配置模块负责四件事：
# 1. 自动读取项目根目录的 .env。
# 2. 获取 PostgreSQL 主机、端口、数据库名、用户名和密码。
# 3. 提供 psycopg 使用的 DB_CONN_INFO。
# 4. 提供 SQLAlchemy 使用的 DB_URI。
"""统一管理项目 PostgreSQL 数据库连接配置。"""
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载项目根目录 .env。
# override=False：如果系统环境变量已经设置，优先使用系统环境变量。
load_dotenv(PROJECT_ROOT / ".env", override=False)


# 数据库基础配置
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "metrics")
DB_USER = os.getenv("POSTGRES_USER", "mlops")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")

if not DB_PASSWORD:
    raise RuntimeError(
        "缺少 POSTGRES_PASSWORD，请检查项目根目录的 .env 文件。"
    )


# psycopg 使用的连接信息
DB_CONN_INFO = make_conninfo(
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
)


# SQLAlchemy 使用的连接 URI。
# 对密码进行 URL 编码，避免特殊字符破坏连接字符串。
DB_URI = (
    f"postgresql+psycopg://{quote(DB_USER, safe='')}:"
    f"{quote(DB_PASSWORD, safe='')}@"
    f"{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

