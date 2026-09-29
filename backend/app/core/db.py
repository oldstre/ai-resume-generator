from collections.abc import AsyncGenerator 
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine 
from sqlalchemy.orm import DeclarativeBase 
from app.core.config import Settings, get_settings

#整个方法基本上是操作数据库的惯用写法

settings  = get_settings()
#创建引擎，一个进程维护这一个引擎，内部维护连接池
engine = create_async_engine(settings.database_url,connect_args = {"timeout":2})
#创建会话工厂，实际上python这种写法创建是个类，但不是对象加了（）就算创建实例了
async_session_factory = async_sessionmaker(engine,expire_on_commit=False)


#这是 所有数据库表的祖先 。后面写`models/resume.py` 时，每个表类都要继承`Base` ：
class Base(DeclarativeBase):
    pass

#with语法学习：with：它是用来管理“必须有收尾动作”的资源，确保“无论是否报错，都能安全退场”的语法糖。
async def get_session()->AsyncGenerator[AsyncSession,None]:
    async with async_session_factory() as session:
        yield session