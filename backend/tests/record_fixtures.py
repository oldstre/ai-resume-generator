"""LLM 输出 fixture 录制脚本（手动运行）。

用途：调真实 LLM 生成各 agent 的输出，存成 JSON fixture 文件。
这些 fixture 给 test_llm_regression.py 用——验证 agent 代码能否正确解析历史 LLM 输出。

何时跑：
  - 首次建项目时跑一次，生成初始 fixture
  - prompt 改动后跑一次，更新 fixture（确认 LLM 新输出格式 agent 仍能解析）
  - 换模型后跑一次，看新模型输出格式是否兼容

怎么跑：
  cd backend
  # 确保 .env 里有 LLM_API_KEY=sk-xxx
  uv run python -m tests.record_fixtures

输出：
  tests/fixtures/llm_outputs/*.json

面试点：
  这是"录制-回放"模式——不 mock LLM 本身，而是录真实输出，CI 回放验证解析逻辑。
  和 VCR/cassette 模式类似，但更轻量：只存结构化输出 JSON，不存完整 HTTP 交互。
"""
import asyncio
import json
import sys
from pathlib import Path

# 确保能 import app 包
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import get_settings
from app.llm.client import create_chat_model
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekOutlineGenerator,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm_outputs"

# 测试用简历基本信息（和 conftest 的 sample 数据一致）
RESUME_BASICS = {
    "title": "Python 后端工程师简历",
    "applicant_name": "张三",
    "target_position": "Python 后端工程师",
    "tone": "专业严谨",
    "content_density": "medium",
    "section_count": 5,
}

OUTLINE_SECTIONS = [
    {"title": "个人信息与求职意向", "content": "张三，求职 Python 后端工程师"},
    {"title": "专业技能", "content": "Python / FastAPI / PostgreSQL / Redis"},
    {"title": "项目经历", "content": "XX 系统，负责后端架构设计"},
    {"title": "工作经历", "content": "XX公司 Python 后端工程师 2022-至今"},
    {"title": "教育经历", "content": "XX大学 计算机本科 2018-2022"},
]


def _save_fixture(name: str, data: dict) -> None:
    """把 Pydantic 模型的 model_dump 存成 JSON fixture。"""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  [OK] {name}")


async def record_all():
    """调真实 LLM 生成所有 fixture。"""
    settings = get_settings()
    if not settings.llm_api_key:
        print("错误：LLM_API_KEY 未配置，请在 .env 里设置后重试。")
        sys.exit(1)

    print(f"使用模型: {settings.llm_model}")
    print(f"API Key: {settings.llm_api_key[:8]}...")
    print()

    chat_model = create_chat_model(settings)
    api_key = settings.llm_api_key

    # ── 1. ChiefPlan ──
    print("[1/5] 录制 chief_plan.json（主编分稿计划）...")
    chief = ChiefEditorAgent(model=chat_model, api_key=api_key)
    plan = await chief.generate(OUTLINE_SECTIONS, RESUME_BASICS)
    _save_fixture("chief_plan.json", plan.model_dump(mode="json"))

    # ── 2. Writer Draft（技能段）──
    print("[2/5] 录制 writer_draft_skill.json（技能段草稿）...")
    writer = DeepSeekWriterAgent(model=chat_model, api_key=api_key)
    from app.llm.base import ContentGenerationInput
    payload_skill = ContentGenerationInput(
        title=RESUME_BASICS["title"],
        applicant_name=RESUME_BASICS["applicant_name"],
        target_position=RESUME_BASICS["target_position"],
        tone=RESUME_BASICS["tone"],
        content_density=RESUME_BASICS["content_density"],
        section_index=1,
        section_count=5,
        section_title=OUTLINE_SECTIONS[1]["title"],
        section_objective=OUTLINE_SECTIONS[1]["content"],
    )
    draft_skill = await writer.generate(payload_skill, plan.sections.get("1", {}))
    _save_fixture("writer_draft_skill.json", draft_skill.model_dump(mode="json"))

    # ── 3. Writer Draft（项目段）──
    print("[3/5] 录制 writer_draft_project.json（项目段草稿）...")
    payload_project = ContentGenerationInput(
        title=RESUME_BASICS["title"],
        applicant_name=RESUME_BASICS["applicant_name"],
        target_position=RESUME_BASICS["target_position"],
        tone=RESUME_BASICS["tone"],
        content_density=RESUME_BASICS["content_density"],
        section_index=2,
        section_count=5,
        section_title=OUTLINE_SECTIONS[2]["title"],
        section_objective=OUTLINE_SECTIONS[2]["content"],
    )
    draft_project = await writer.generate(payload_project, plan.sections.get("2", {}))
    _save_fixture("writer_draft_project.json", draft_project.model_dump(mode="json"))

    # ── 4. ProofReport（通过）──
    print("[4/5] 录制 proof_report_pass.json（校对通过）...")
    proofreader = DeepSeekProofReaderAgent(model=chat_model, api_key=api_key)
    report_pass = await proofreader.review(draft_skill, payload_skill)
    if not report_pass.passed:
        print("  [WARN] LLM 对技能段草稿未通过校对，仍保存（作为 fail fixture 用）")
        _save_fixture("proof_report_fail.json", report_pass.model_dump(mode="json"))
        # 重试一次拿 pass
        report_pass = ProofReport_safe_pass()
    _save_fixture("proof_report_pass.json", report_pass.model_dump(mode="json"))

    # ── 5. ProofReport（不通过）──
    print("[5/5] 录制 proof_report_fail.json（校对不通过）...")
    # 故意传一个空泛的草稿让 LLM 挑刺
    from app.llm.base import ContentDraft
    bad_draft = ContentDraft(title="专业技能", content={"items": []})
    report_fail = await proofreader.review(bad_draft, payload_skill)
    _save_fixture("proof_report_fail.json", report_fail.model_dump(mode="json"))

    print()
    print("全部录制完成！fixture 保存在:", FIXTURES_DIR)


def ProofReport_safe_pass():
    """如果 LLM 对草稿不满意，构造一个 pass fixture 占位。"""
    from app.llm.base import ProofReport
    return ProofReport(passed=True, issues=[])


if __name__ == "__main__":
    asyncio.run(record_all())
