import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

ResumeContentStatus = Literal["pending", "generating", "ready", "failed"]

class ResumeContent(Base):
    """简历一段的最终内容：一条大纲段对应一条内容记录。

    以大纲段索引作为幂等键：重试、断点恢复和重复入队都落到同一行，
    不会因为再跑一次任务就多出一段。

    大纲 ResumeOutline 是 目录骨架 ——"5 段：个人信息/技能/项目/工作/教育，每段写什么"

    内容 ResumeContent 是 正文血肉 ——"技能段：列出 Python/SQL/Redis 等具体技能；项目段：写出项目细节描述"
    """

    __tablename__ = "resume_contents"
    """
        幂等键是一个唯一约束，就是告诉这一对组合不能重复
    
    
    """
    __table_args__ = (
        UniqueConstraint("resume_id", "section_index", name="uq_resume_contents_resume_section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 大纲段索引：定位这条内容对应大纲里的第几段（0-based）
    # 同时和 resume_id 组成幂等键——重试不会重复建行
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # 顺序：展示时排序用（一般和 section_index 一致，但保留独立字段方便人工调整）
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # 大纲标题先行落库：内容还没生成时进度列表也能显示这一段是什么
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # 简历每段的最终内容：JSONB（存储结构化内容，比如段落、技能列表等）
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 自纠环记录的问题：内容生成失败时存诊断信息
    issues: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)

    # 乐观锁：AI 局部修改与人工编辑提交时带上它，不一致即判冲突
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    resume: Mapped["Resume"] = relationship(back_populates="contents")