from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.paths import REPO_ROOT
from dotenv import load_dotenv
load_dotenv()  # 把 .env 导出到 os.environ，给 LangChain/LangSmith 读

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env",extra="ignore")

    app_env :str = "development"
    database_url: str = "postgresql+asyncpg://resume:resume@localhost:39432/resume"    
    redis_url :str = "redis://localhost:39379/0"
    cors_origins:list[str] = [ "http://localhost:39174" ]

    # ── JWT / 鉴权配置 ── 
    # # HS256 签名密钥。开发用默认值,生产必须通过 .env 覆盖! 
    # # 生成命令:openssl rand -hex 32(会输出 64 字符 hex 字符串) 
    jwt_secret_key: str = "dev-only-please-change-in-production-min-32-chars" 
    jwt_algorithm: str = "HS256" 
    access_token_expire_minutes: int = 15 
    refresh_token_expire_days: int = 7

    llm_api_key:str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_thinking_enabled: bool = False
    llm_timeout_seconds: float = 60

    # ── A 步骤：真实搜索 API（Tavily） ──
    tavily_api_key: str = "" # Tavily API 密钥（去 tavily.com 注册拿）
    tavily_timeout_seconds: float = 10.0 # 单次搜索超时 10 秒（比 LLM 短，搜索是辅助不能拖垮主流程）
    web_search_fallback_to_mock: bool = True  # 搜索挂了是否回退 mock（True=挂了用假数据，保证简历生成不中断）

    resume_concurrency: int =3 #并发数

    storage_local_dir: str = str (REPO_ROOT / "backend" / "var" / "storage" )
    
    max_upload_mb: int = 10#上传大小限制

    # ── B 步骤：RAG 向量检索（pgvector + 阿里通义 embedding） ──
    pgvector_url: str = "postgresql+asyncpg://resume:resume@localhost:39433/resume"
    dashscope_api_key: str = "" # 阿里通义 API key（sk- 开头）
    embedding_model: str = "text-embedding-v2" # 通义 embedding 模型名
    embedding_dimensions: int = 1536 # text-embedding-v2 产出 1536 维向量
    rag_top_k: int = 3  # 检索时返回最相似的几条样本
    
    @property
    def max_upload_bytes(self)->int:
        return self.max_upload_mb*1024*1024


@lru_cache
def get_settings()-> Settings:
    return Settings()# 缓存避免每个请求重复解析环境变量




    