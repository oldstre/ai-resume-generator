"""根 conftest：所有测试共享的全局配置。

主要职责：
1. 确保 LLM API Key 为空——单元测试绝不能意外调真实 LLM
2. mock 掉 RAG 检索（retrieve_samples）——不依赖 pgvector
"""
import os

# 在导入 app 模块前设环境变量，防止意外调真实 LLM / DB
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("TAVILY_API_KEY", "")
os.environ.setdefault("DASHSCOPE_API_KEY", "")

import pytest


@pytest.fixture(autouse=True)
def mock_retrieve_samples(monkeypatch):
    """全局 mock RAG 检索——单元测试不依赖 pgvector。

    retrieve_samples 在 DeepSeekWriterAgent.generate() 里被调，
    返回空列表 = 不给 LLM 参考材料，但 agent 逻辑正常跑。
    """
    async def _empty_retrieve(*args, **kwargs):
        return []

    # 对所有可能 import retrieve_samples 的模块做 patch
    monkeypatch.setattr("app.llm.deepseek.retrieve_samples", _empty_retrieve)
