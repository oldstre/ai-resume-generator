"""add user_id to resumes with backfill

Revision ID: 9523da0d53e0
Revises: c510efa25efd
Create Date: 2026-09-28 17:30:32.872870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9523da0d53e0'
down_revision: Union[str, Sequence[str], None] = 'c510efa25efd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 系统用户固定 UUID,便于审计和回滚定位 
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
def upgrade () -> None : 
    # 1. 加 user_id 列,先 nullable=True(否则 NOT NULL 约束在已有数据上失败) 
    op.add_column( "resumes" ,
        sa.Column( "user_id" ,
            postgresql.UUID(as_uuid= True ),
            nullable= True ,
        ),
    ) # 2. 创建系统用户占位账号,拥有所有 legacy 简历 #    is_active=false:任何人都不能登录这个账号(纯数据归属用) #    ON CONFLICT DO NOTHING:迁移可重入,失败重跑不报错 
    op.execute( f"""
        INSERT INTO users (id, email, hashed_password, full_name, is_active, created_at, updated_at)
        VALUES (
            '{SYSTEM_USER_ID}',
            'system@local',
            '$2b$12$dummyhashforsystemuserxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
            'System User (legacy data owner)',
            false,
            now(),
            now()
        )
        ON CONFLICT (email) DO NOTHING
    """ ) 
    # 3. 回填:所有现有 resumes 归属系统用户 
    op.execute( f"""
        UPDATE resumes SET user_id = '{SYSTEM_USER_ID}' WHERE user_id IS NULL
    """ ) 
    # 4. 改成 NOT NULL(此时所有行都有值,不会失败) 
    op.alter_column( "resumes" , "user_id" , nullable= False ) 
    # 5. 加外键约束(单独一步,跟 add_column 分开,清晰) 
    op.create_foreign_key( "fk_resumes_user_id" , "resumes" , "users" ,
        [ "user_id" ],
        [ "id" ],
        ondelete= "CASCADE" ,
    ) 
    # 6. 加索引(模型里声明了 index=True,迁移里要显式建) 
    op.create_index( "ix_resumes_user_id" , "resumes" , [ "user_id" ]) 
    
    
def downgrade () -> None :
    op.drop_index( "ix_resumes_user_id" , table_name= "resumes" )
    op.drop_constraint( "fk_resumes_user_id" , "resumes" , type_= "foreignkey" )
    op.drop_column( "resumes" , "user_id" ) # 注意:downgrade 不删系统用户!保留它以免 legacy 数据再次无主
