"""WorkerSettings：ARQ worker 进程的"入职手册"。

arq 命令行启动 worker 时读这个配置，知道：
- 连哪个 Redis（传菜口在哪）
- 会执行哪些函数（会做哪些菜）
- 同时最多跑几个任务（最多同时做几道菜）
"""


from arq.connections import RedisSettings
from app.core.config import get_settings
from app.worker.tasks import generate_outline_task

class WorkerSettings:
    """arq worker 启动配置。"""
    # 告诉 ARQ 这个 worker 能执行哪些函数
    # 注意：是函数对象本身，不是字符串
    # 内容生成已迁到 SSE 流式端点（POST /contents/regenerate/stream），不再走 ARQ
    functions = [generate_outline_task]
    # 连哪个 Redis（从 settings 读 redis_url）
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
# 同时最多跑几个任务（并发上限）
    max_jobs = 3
    # 单任务超时秒数（简历大纲生成几十秒，给宽点 5 分钟）
    job_timeout = 5*60
    # 失败重试次数（简化版先不重试，失败了就失败）
    max_tries =1
    