



from typing import Literal
from datetime import datetime
import uuid

from sqlalchemy import Integer, String, DateTime, func, ForeignKey  # ← 加 ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB,UUID
from app.core.db import Base

ResumeStatus = Literal["draft","outline_ready","generating","ready"]

"""
    SQLAlchemy 2.0 的新写法：`Mapped[...]` +`mapped_column(...)`
    - `Mapped[str]` 是 Python 类型注解，IDE 能识别字段类型，写代码有提示
    - `mapped_column(...)` 是详细的列定义，参数更丰富
    - Pydantic 集成时能直接读`Mapped[str]` 自动生成 schema
"""
class Resume(Base):
    __tablename__ = "resumes"
    """简历主表：一份简历对应一行。"""
    id:Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)

    # 归属用户。外键 + 级联删除:用户删了简历全删。 # 多租户隔离的核心字段:所有查询都要 WHERE user_id = :current_user.id 
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid= True ),
        ForeignKey( "users.id" , ondelete= "CASCADE" ),
        index= True ,
        nullable= False ,
    )
    title:Mapped[str] = mapped_column(String(200),nullable=False)
    # 求职者姓名；先做成必填，简化教学
    applicant_name:Mapped[str] = mapped_column(String(100),nullable=False)
    # 目标职位，比如"Python 后端工程师"
    target_position:Mapped[str] = mapped_column(String(100),nullable=False)
    tone:Mapped[str] = mapped_column(String(32),nullable=False)
    # 简历按段落组织（教育/工作/技能/项目经历），section_count 是目标段落数
    section_count :Mapped[int] = mapped_column(Integer,nullable=False)
    ## 模板 ID，对应 PPT 项目的 theme_id
    template_id :Mapped[str] = mapped_column(String(50),nullable=False,default="modern")
    #为什么用 JSONB 不用 JSON？ PostgreSQL 的 JSONB 是二进制存储，查询更快，能建索引。
    template_overrides:Mapped[dict] = mapped_column(JSONB,nullable=False,default=dict)
    # 整份文字量：concise / medium / detailed
    content_density :Mapped[str] = mapped_column(String(16),nullable=False,default="medium")
    status: Mapped[ str ] = mapped_column(String( 32 ), nullable= False , default= "draft" )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone= True ), server_default=func.now(), nullable= False )
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone= True ), server_default=func.now(), onupdate=func.now(), nullable= False )
    
    #和outline绑定一个双向关系，这里也用引号，因为搞了双向绑定，总有一边的对方还没定义。
    outline: Mapped[ "ResumeOutline | None" ] = relationship(
        back_populates= "resume" ,#双向关系另一边
        cascade= "all, delete-orphan" , #级联删除，简历删了大纲也删了
        lazy = "selectin" ,#查简历时候，自动加载大纲
        uselist= False ,#限定是一对一关系，而不是一对多关系
    )

    #简历多段内容：1 对多（section_count 段）
    contents: Mapped[list["ResumeContent"]] = relationship(
        back_populates="resume",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ResumeContent.position",  # 按顺序加载
    )


        