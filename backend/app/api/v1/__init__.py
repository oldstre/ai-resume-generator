from fastapi import APIRouter

from app.api.v1 import auth, chat, contents, health, outlines, resumes

api_router = APIRouter()

# health.router 不自带 prefix,但端点层面已写 /health 路径,所以这里不加 prefix
api_router.include_router(health.router)

# 以下 router 都自带 prefix,include 时不要再加!
api_router.include_router(auth.router)        # 自带 /auth
api_router.include_router(resumes.router)     # 自带 /resumes
api_router.include_router(outlines.router)    # 自带 /resumes/{resume_id}/outline
api_router.include_router(contents.router)    # 自带 /resumes/{resume_id}/contents
api_router.include_router(chat.router)        # 自带 /resumes/{resume_id}/chat

__all__ = ["api_router"]