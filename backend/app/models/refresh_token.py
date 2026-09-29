from datetime import datetime
import uuid

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.core.db import Base

class RefreshToken(Base):
    """refresh token 记录表:多设备并存,每次登录插一行,登出软删除。"""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 所属用户。外键 + 级联删除:用户删了 token 全删
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    # token 的 SHA-256 哈希(不存明文!)。索引加速 refresh 时的查询
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    # 设备/UA 信息,用于"已登录设备"列表展示
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 过期时间,带时区
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 软删除:登出时填这个时间,不删行(保留审计)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # 反向关系:User.refresh_tokens
    user: Mapped["User"] = relationship(back_populates="refresh_tokens")