r"""一次性脚本：把优秀简历样本向量化后存入 pgvector。

跑法（在 backend 目录）：
    .\.venv\Scripts\python.exe scripts\seed_resume_samples.py

跑完 resume_sample 表里会有 5 条带向量的样本。
重跑会先 TRUNCATE 再插，不重复。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

# 把 backend 根加进 sys.path，让脚本能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.config import get_settings
from app.llm.embedding import embed_texts

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 优秀简历样本 ──────────────────────────────────────────
# 每条 4 个字段：
#   target_position  目标岗位（检索时先按这个过滤）
#   section_title    段落标题
#   content          段落内容（JSONB，和 ContentDraft.content 格式一致，直接喂 LLM）
#   content_text     纯文本（embedding 输入，把 title+content 拍平）
SAMPLES = [
    {
        "target_position": "Python 后端工程师",
        "section_title": "专业技能",
        "content": {
            "items": [
                {"skill": "Python", "years": 4, "level": "熟练"},
                {"skill": "FastAPI", "years": 2, "level": "掌握"},
                {"skill": "PostgreSQL", "years": 3, "level": "熟练"},
                {"skill": "Redis", "years": 2, "level": "掌握"},
                {"skill": "Docker", "years": 2, "level": "掌握"},
            ]
        },
        "content_text": (
            "专业技能。Python 4年熟练，"
            "FastAPI 2年掌握，PostgreSQL 3年熟练，"
            "Redis 2年掌握，Docker 2年掌握。"
            "熟悉异步编程、微服务架构、RESTful API 设计。"
        ),
    },
    {
        "target_position": "Python 后端工程师",
        "section_title": "项目经历",
        "content": {
            "items": [
                {
                    "name": "订单服务系统",
                    "role": "后端开发",
                    "tech": ["Python", "FastAPI", "PostgreSQL", "Redis"],
                    "desc": "负责订单核心流程，日均订单 50 万，"
                    "用 Redis 缓存热点数据，接口 P99 低于 200ms。",
                }
            ]
        },
        "content_text": (
            "项目经历：订单服务系统，后端开发。"
            "技术栈 Python FastAPI PostgreSQL Redis。"
            "负责订单核心流程，日均订单 50 万，"
            "用 Redis 缓存热点数据，接口 P99 低于 200ms。"
        ),
    },
    {
        "target_position": "Python 后端工程师",
        "section_title": "工作经历",
        "content": {
            "items": [
                {
                    "company": "XX 互联网公司",
                    "role": "Python 后端工程师",
                    "period": "2022-至今",
                    "desc": "负责交易核心服务，参与系统从单体到微服务拆分，"
                    "主导订单模块重构，吞吐量提升 3 倍。",
                }
            ]
        },
        "content_text": (
            "工作经历：XX 互联网公司，Python 后端工程师，2022 至今。"
            "负责交易核心服务，参与系统从单体到微服务拆分，"
            "主导订单模块重构，吞吐量提升 3 倍。"
        ),
    },
    {
        "target_position": "前端工程师",
        "section_title": "专业技能",
        "content": {
            "items": [
                {"skill": "JavaScript", "years": 3, "level": "熟练"},
                {"skill": "React", "years": 2, "level": "掌握"},
                {"skill": "TypeScript", "years": 2, "level": "掌握"},
                {"skill": "Vite", "years": 1, "level": "了解"},
                {"skill": "TailwindCSS", "years": 1, "level": "掌握"},
            ]
        },
        "content_text": (
            "专业技能。JavaScript 3年熟练，"
            "React 2年掌握，TypeScript 2年掌握，"
            "Vite 1年了解，TailwindCSS 1年掌握。"
            "熟悉组件化开发、状态管理、性能优化。"
        ),
    },
    {
        "target_position": "前端工程师",
        "section_title": "项目经历",
        "content": {
            "items": [
                {
                    "name": "数据看板平台",
                    "role": "前端开发",
                    "tech": ["React", "TypeScript", "ECharts", "TanStack Query"],
                    "desc": "负责可视化模块，用 ECharts 渲染 10+ 图表，"
                    "TanStack Query 管理服务端状态，首屏加载优化到 1.2s。",
                }
            ]
        },
        "content_text": (
            "项目经历：数据看板平台，前端开发。"
            "技术栈 React TypeScript ECharts TanStack Query。"
            "负责可视化模块，用 ECharts 渲染 10+ 图表，"
            "TanStack Query 管理服务端状态，首屏加载优化到 1.2s。"
        ),
    },
        {
        "target_position": "Agent 开发工程师",
        "section_title": "个人信息与求职意向",
        "content": {
            "paragraphs": [
                "XX，Agent 开发工程师，专注大模型智能体、工具调用与多智能体协作方向，具备将 LLM 能力落地为可交付智能体系统的工程实践基础。",
                "求职意向：Agent 开发工程师（大模型智能体 / 工具调用 / 多智能体协作方向），期望参与企业级智能体平台或 AI 应用系统研发。",
                "基本信息：现居 XX 市，可接受远程/混合办公；联系方式：电话 XXX-XXXX-XXXX，邮箱 xxxx@example.com；到岗时间：XX 周内；期望薪资：面议。",
            ],
            "highlights": [
                "求职意向：Agent 开发工程师｜方向：大模型智能体、工具调用、多智能体协作",
                "核心能力：Python / LangChain / RAG / Function Call 与 MCP / Prompt Engineering",
                "基本信息：XX 市｜电话 XXX-XXXX-XXXX｜XX 周内到岗｜薪资面议",
            ],
        },
        "content_text": (
            "个人信息与求职意向。求职者专注大模型智能体、工具调用与多智能体协作方向，"
            "具备将 LLM 能力落地为可交付智能体系统的工程实践基础。"
            "熟悉 Python 及 LangChain、LlamaIndex 等 Agent 开发框架，"
            "理解 Prompt Engineering、RAG、Function Call 与 MCP 工具协议。"
            "求职意向：Agent 开发工程师，期望参与企业级智能体平台或 AI 应用系统研发。"
        ),
    },
    {
        "target_position": "Agent 开发工程师",
        "section_title": "专业技能",
        "content": {
            "items": [
                {"skill": "Python", "years": 3, "level": "熟练"},
                {"skill": "LangChain", "years": 2, "level": "掌握"},
                {"skill": "RAG", "years": 2, "level": "掌握"},
                {"skill": "Function Call / MCP", "years": 1, "level": "掌握"},
                {"skill": "Prompt Engineering", "years": 2, "level": "熟练"},
            ]
        },
        "content_text": (
            "专业技能。Python 3年熟练，"
            "LangChain 2年掌握，RAG 2年掌握，"
            "Function Call 与 MCP 1年掌握，Prompt Engineering 2年熟练。"
            "熟悉大模型智能体设计、工具调用编排、多智能体协作。"
        ),
    },
    {
        "target_position": "Agent 开发工程师",
        "section_title": "项目经历",
        "content": {
            "items": [
                {
                    "name": "企业智能客服 Agent 系统",
                    "role": "Agent 开发",
                    "tech": ["Python", "LangChain", "RAG", "Function Call"],
                    "desc": "负责智能客服 Agent 设计，用 RAG 检索知识库，"
                    "Function Call 对接工单系统，意图识别准确率 92%，"
                    "日均处理咨询 3000+ 条。",
                }
            ]
        },
        "content_text": (
            "项目经历：企业智能客服 Agent 系统，Agent 开发。"
            "技术栈 Python LangChain RAG Function Call。"
            "负责智能客服 Agent 设计，用 RAG 检索知识库，"
            "Function Call 对接工单系统，意图识别准确率 92%，"
            "日均处理咨询 3000+ 条。"
        ),
    },
]

async def main() -> None:
    settings = get_settings()
    # asyncpg 的 URL 不带 +asyncpg，剥掉
    dsn = settings.pgvector_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)

    # 注册 pgvector 类型适配器，之后插 list[float] 自动转 vector
    await register_vector(conn)

    # 重跑先清空，避免重复
    await conn.execute("TRUNCATE resume_sample")

    # 批量向量化所有 content_text
    texts = [s["content_text"] for s in SAMPLES]
    logger.info("开始向量化 %d 条样本...", len(texts))
    embeddings = await embed_texts(texts)
    logger.info("向量化完成，维度 %d", len(embeddings[0]))

    # 逐条插入（content 用 JSONB，传 json 字符串）
    for sample, emb in zip(SAMPLES, embeddings):
        await conn.execute(
            """
            INSERT INTO resume_sample
                (id, target_position, section_title, content, content_text, embedding)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            str(uuid.uuid4()),
            sample["target_position"],
            sample["section_title"],
            json.dumps(sample["content"], ensure_ascii=False),
            sample["content_text"],
            emb,  # 注册了 adapter，直接传 list[float]
        )
        logger.info("插入: %s / %s", sample["target_position"], sample["section_title"])

    # 验证
    count = await conn.fetchval("SELECT count(*) FROM resume_sample")
    logger.info("入库完成，共 %d 条样本", count)
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())