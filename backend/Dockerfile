FROM python:3.12-slim

WORKDIR /app

# 装系统依赖：libpq-dev 给 psycopg 用（slim 镜像默认没有）
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# 装 uv（用 pip 装，避免从 ghcr.io 拉镜像被墙）
RUN pip install uv -i https://mirrors.aliyun.com/pypi/simple/ --no-cache-dir

# 先拷依赖清单（利用 Docker 层缓存：改代码不重装依赖）
COPY pyproject.toml uv.lock* ./

# 安装依赖到系统 Python（官方 PyPI + 阿里云镜像备用）
RUN uv pip install --system --no-cache . \
    --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
    --index-strategy unsafe-best-match

# 再拷业务代码
COPY . .

EXPOSE 39801

# 启动命令：先跑迁移再启服务
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 39801"]
