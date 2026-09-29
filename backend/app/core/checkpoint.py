"""LangGraph Checkpointer 单例(AsyncPostgresSaver + 连接池)。

生命周期:
- get_checkpointer():  模块级同步拿单例(构造 saver + pool,但不连库)
- setup_checkpointer(): 启动时调一次,open pool + 建表
- close_checkpointer(): 退出时关 pool

为什么不用 from_conn_string:它是 @asynccontextmanager,只能临时用,
不适合做长生命周期单例。手动构造 pool + saver 才能跨请求复用。
"""
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from app.core.config import get_settings
_saver: AsyncPostgresSaver |None = None
_pool: AsyncPostgresSaver |None = None
from psycopg.rows import dict_row # ← 顶部 import 区加这行

def _to_psycopg_url(database_url:str)->str:
    """把 SQLAlchemy 风格的 database_url 转成 psycopg3 风格。

    项目里是 postgresql+asyncpg://...,psycopg3 不认 +asyncpg 驱动后缀,
    去掉即可。其他部分(host/port/user/pass/dbname)两边都兼容。
    """ 
    if "+asyncpg" in database_url: 
        return database_url.replace( "+asyncpg" , "" ) 
    return database_url


def get_checkpointer()->AsyncPostgresSaver:
    """同步拿 saver 单例。

    只构造对象,不开连接——连接由 setup_checkpointer() 显式 open。
    这样 get_checkpointer() 可以在任何上下文(含同步代码)安全调用。
    """
    global _saver,_pool
    if _saver is None:
        settings = get_settings()
        psycopg_url = _to_psycopg_url(settings.database_url)
        # open=False:池对象创建但不连库,等 setup_checkpointer() 显式 open 
        # # 让我们控制启动顺序(先连上 DB 再启服务)
        _pool = AsyncConnectionPool(conninfo = psycopg_url,open=False,kwargs={
            "autocommit":True,# 关键:CREATE INDEX CONCURRENTLY 要求 autocommit
            "prepare_threshold":0, # 和 from_conn_string 默认行为对齐
            "row_factory":dict_row# 让 cursor 默认返回 dict(saver 内部按 dict 访问)
        })
        _saver = AsyncPostgresSaver(conn=_pool)

    return _saver

async def setup_checkpointer()->None:
    """启动时调用:开连接池 + 建 checkpoint 表。

    必须在 FastAPI lifespan 的 startup 阶段调,且在接收请求之前。
    saver.setup() 会执行 migration,创建 checkpoints / writes 等表。
    """

    global _pool
    if _pool is None :
            # 没调过 get_checkpointer() 就先初始化(防御性)
        get_checkpointer()
    await _pool.open() # 真正打开池、建立 min_size 连接
    await get_checkpointer().setup() # 建表 + migration


async def close_checkpointer()->None:
    """退出时关闭连接池,避免连接泄漏。"""
    global _saver,_pool
    if _pool is not None:
        await _pool.close()
    _saver = None
    _pool = None