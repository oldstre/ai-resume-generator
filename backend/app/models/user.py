


from datetime import datetime 
import uuid 
from sqlalchemy import String, DateTime, Boolean, func 
from sqlalchemy.orm import Mapped, mapped_column, relationship # ← 加 relationshipfrom sqlalchemy.dialects.postgresql import UUID 
from sqlalchemy.dialects.postgresql import UUID
from app.core.db import Base


class User(Base):
    """用户表:存放账号信息。密码只存哈希,绝不存明文。"""
    __tablename__ = "users"

    id:Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid = True),primary_key=True,default=uuid.uuid4
    )
# email 作登录账号,必须唯一且建索引(登录时按 email 查,索引加速)
    email:Mapped[str] = mapped_column(
        String(255),unique=True,index=True,nullable=False
    )

# 密码哈希。bcrypt 输出固定 60 字符,但留 255 余量方便未来换 argon2(输出更长)
    hashed_password:Mapped[str] = mapped_column(String(255),nullable=False)
    full_name :Mapped[str] = mapped_column(String(100),nullable=False)
# 软禁用开关:管理员可禁用账号,token 仍有效但 get_current_user 会拒绝
    is_active:Mapped[bool] = mapped_column(Boolean,default=True,nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone= True ), server_default=func.now(), nullable= False )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone= True ), server_default=func.now(),
        onupdate=func.now(), nullable= False )

    # 反向关系:这个用户的所有 refresh token(登出全部设备时用) 
    refresh_tokens: Mapped[ list [ "RefreshToken" ]] = relationship(
        back_populates= "user" ,
        cascade= "all, delete-orphan" , lazy = "selectin" ,
    )