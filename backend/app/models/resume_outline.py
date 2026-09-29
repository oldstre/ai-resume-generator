import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

class ResumeOutline(Base):
    """简历大纲：一份简历最多一条大纲记录（一对一）。"""

    __tablename__ = "resume_outlines"
    __table_args__ = (UniqueConstraint("resume_id", name="uq_resume_outlines_resume_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # generating=生成中；confirmed=用户已确认
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="generating")
    # 大纲本体：JSONB 数组，每个元素是一段（教育/工作/技能/项目经历/自我评价）的草稿
    sections: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    # 乐观锁版本号：每次更新 +1，防止并发覆盖
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 关联 ARQ 任务 id，用于查任务状态/取消任务
    job_id: Mapped[str | None] = mapped_column(String(100))
    # 输入指纹：相同输入不重复生成（幂等）
    input_signature: Mapped[str | None] = mapped_column(String(64))
    # 生成失败时存错误信息
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    """
    按理说这个里面本来不是直接写Resume就行了嘛，这里为什么写成了字符串？
    说是常用技巧来着，意思是python执行到这行的时候，不会报错，可能原因是Resume还没创建好呢，所以先放过，等文件全部执行完，所有类都存在后，再拿着这个字符串去全局找类
    """
    resume: Mapped["Resume"] = relationship(back_populates="outline")