"""LLM 可调用的工具集。

这是 agent 化的第一步：给 LLM 一个"工具箱"，让它自己决定要不要用。
现在先 mock 实现 search_web，真实接 Tavily 放到扩展点 #12。

┌──────────────────────────────────────────────────────┐
│  @tool 装饰器干了什么？                              │
│  1. 把普通函数包装成 LangChain 的 BaseTool 对象     │
│  2. 从函数签名 + docstring 自动提取工具的 schema     │
│     → LLM 看到的就是"有个工具叫 search_web，        │
│       接受一个 query 参数，功能是搜索互联网"         │
│  3. LLM 决定调用时，你的代码拿到 tool_call，         │
│     执行这个函数，把结果喂回 LLM                     │
└──────────────────────────────────────────────────────┘

三道保险：
1. 限流：asyncio.Semaphore 限制并发（防 LLM 反复调刷爆额度）
2. 超时：asyncio.wait_for 限制单次搜索时长
3. 降级：异常回退 mock（搜索挂了，简历生成照样跑）
┌─────────────────────────────────────────────────────────────┐
│  search_web(query)                                         │
│                                                             │
│  ┌───────────────────────────────────────────────────┐    │
│  │ 第 1 道保险：限流（asyncio.Semaphore(2)）          │    │
│  │   全局最多 2 个搜索并发，排队等                     │    │
│  └────────────────────┬──────────────────────────────┘    │
│                       ↓                                     │
│  ┌───────────────────────────────────────────────────┐    │
│  │ 第 2 道保险：超时（asyncio.wait_for, 10s）        │    │
│  │   Tavily SDK 是同步的 → 用 asyncio.to_thread 包    │    │
│  │   成异步 → wait_for 加超时                         │    │
│  └────────────────────┬──────────────────────────────┘    │
│                       ↓                                     │
│  ┌───────────────────────────────────────────────────┐    │
│  │ try: 调真实 Tavily → 整理结果 → 返回 JSON         │    │
│  │ except Exception:                                   │    │
│  │   第 3 道保险：降级 → 回退 mock                    │    │
│  └───────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘


"""

from __future__ import annotations

import asyncio
import json

from langchain_core.tools import tool

from app.core.config import get_settings


__all__ = ["search_web"]

# ── 模块级懒加载对象 ──────────────────────────────────── 
# # 为什么懒加载而不在模块顶层直接创建？ 
# # 1. Tavily 客户端：没配 key 时模块导入不报错（key 在 .env，可能没填） 
# # 2. Semaphore：虽然 Python 3.10+ 不再绑定事件循环，懒加载更稳妥

_tavily_client = None
_search_semaphore:asyncio.Semaphore | None = None

def _get_tavily_client():
    """懒加载 Tavily 客户端，第一次调用时才初始化。"""
    global _tavily_client
    if _tavily_client is None:
        settings = get_settings()
        if not settings.tavily_api_key:
            raise RuntimeError( "TAVILY_API_KEY 未配置" )
        # 这里才 import，避免模块导入时就依赖 tavily 包
        from tavily import TavilyClient

        _tavily_client = TavilyClient(api_key=settings.tavily_api_key)
    return _tavily_client


def _get_semaphore()->asyncio.Semaphore:
    """懒加载限流器：全局最多 2 个并发搜索。"""
    global _search_semaphore
    if _search_semaphore is None:
        _search_semaphore = asyncio.Semaphore(2)
    return _search_semaphore

def _mock_results(query:str)->str:
    """mock 假数据——搜索挂了就回退到这个，LLM 无感照样能生成。"""
    mock = [
        {
            "title": f"搜索结果：{query}",
            "snippet": (
                "当前该岗位热门技能包括：Python、FastAPI、PostgreSQL、"
                "Docker、Redis、消息队列。要求 2-3 年经验，"
                "熟悉微服务架构和异步编程。"
            ),
            "url": "https://example.com/job-trends-2025",
        },
        {
            "title": f"行业趋势：{query}",
            "snippet": (
                "AI + 后端融合是当前趋势，掌握 LLM 应用开发"
                "（LangChain、RAG）是加分项。云原生和 DevOps "
                "实践越来越被看重。"
            ),
            "url": "https://example.com/industry-trend",
        },
    ]
    # 返回 JSON 字符串——LLM 能读懂这个结构
    return json.dumps(mock_results, ensure_ascii=False)

@tool 
async def search_web ( query: str ) -> str : 
    """搜索互联网，获取目标岗位的技能要求、行业趋势等最新信息。

    当需要了解外部信息来丰富简历内容时调用此工具。
    例如：目标岗位的最新技能要求、某项技术的市场需求等。

    Args:
        query: 搜索关键词，如"Python 后端工程师 技能要求 2025"

    Returns:
        JSON 字符串，包含搜索结果列表
    """ 
    settings = get_settings() # 三道保险：限流 → 超时 → 降级 async with _get_semaphore(): 
    try :
        client = _get_tavily_client() # Tavily SDK 是同步的，用 asyncio.to_thread 包成异步 # asyncio.wait_for 加超时，超时抛 TimeoutError 
        raw = await asyncio.wait_for(
        asyncio.to_thread(
                    client.search,
                    query=query,
                    max_results= 5 ,
                    search_depth= "basic" ,
                ),
            timeout=settings.tavily_timeout_seconds,
        ) # 把 Tavily 原始返回整理成简化格式喂给 LLM 
        results = [
                { "title" : r.get( "title" , "" ), "snippet" : r.get( "content" , "" ), "url" : r.get( "url" , "" ),
                } 
                for r in raw.get( "results" , [])
            ] 
        return json.dumps(results, ensure_ascii= False ) 
    except Exception as error: # 降级：超时 / 网络错 / API 错 / key 没配 → 回退 mock 
        if settings.web_search_fallback_to_mock:
            logger.warning( f"search_web 失败，回退 mock： { type (error).__name__} : {error} " ) 
            return _mock_results(query) 
        raise
