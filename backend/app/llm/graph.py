"""LangGraph 状态图：多智能体并发生成内容的核心编排。

包含 3 层结构：
1. 单段子图 build_section_subgraph：撰写 → 校对 → 自纠环
2. 主图 build_multi_section_graph：主编 → fan_out 5 段并发子图 → 跨段校验 → all_done
3. 包装节点 section_subgraph_node：把子图返回值格式转成主图状态合并的形状

SSE 进度事件通过 on_event 回调推送，由调用方（content.py 的
generate_all_contents_streaming）转成 SSE 流给前端。
"""

from langgraph.types import Send
from typing import Callable, Coroutine, Optional
from langgraph.graph import StateGraph, START, END
from app.llm.deepseek import (
    ChiefEditorAgent,
    DeepSeekProofReaderAgent,
    DeepSeekWriterAgent,
)
from app.schemas.sse_event import CheckData, CheckEvent, StatusData, StatusEvent
from app.llm.base import ContentGenerationInput
from app.llm.multi_section_state import MultiSectionState, SectionGenState

# 回调函数类型注解：async 函数，参数是 SSEEvent（这里简化只标 StatusEvent/CheckEvent 之一）
#Callable[[object], ...]	接收一个参数（事件对象）的可调用对象
#
OnEventCallback = Optional [ Callable [[ object ], Coroutine [ None , None , None ]]]


def build_section_subgraph(
    writer:DeepSeekWriterAgent,
    proofreader:DeepSeekProofReaderAgent,
    on_event:OnEventCallback= None,
):
    """构建单段子图：撰写→校对→自纠环（C 多智能体的核心）。

    流程：
        START → writer → proofreader → [should_continue]
                                            │
                              ┌─────────────┴─────────────┐
                              │                           │
                          "end"                        "revise"
                       (通过/用完次数)               (没过，还有次数)
                              │                           │
                              ↓                           ↓
                             END                       writer（带 issues）

    - state 用 SectionGenState（含 section_index + plan 字段）
    - writer 接收主编 plan 指引（强化 system prompt）
    - proofreader 调 LLM（DeepSeekProofReaderAgent）做语义校对
    - max_retries 由 state 决定（不写死），Send 派发时塞 2 控制预算
    - 推事件时带 section_index 字段，前端按段号聚合

    参数：
        writer: 撰写 agent 实例（DeepSeekWriterAgent）
        proofreader: 校对 agent 实例（DeepSeekProofReaderAgent）
        on_event: 可选 async 回调，节点在关键点调用推事件

    返回：编译后的子图，可作为主图的一个节点（graph.add_node("section_subgraph", compiled)）
    """

    async def writer_node(state:SectionGenState)->dict:
        """调撰写 agent 生成内容（带工具调用 + RAG）。

        每轮把最新 issues 塞进 payload（让 LLM 针对问题定向修复），
        把主编 plan 塞进 system prompt（让 LLM 按指引写）。
        """
        section_index = state["section_index"]
        if on_event is not None:
            stage = "regenerating" if state["attempts"]>0 else "generating"
            await on_event(StatusEvent(
                event="status",
                data = StatusData(
                    stage=stage,
                    message=f"段 {section_index} 第 {state[ 'attempts' ] + 1 } 次尝试撰写" ,
                    attempt= state["attempts"] +1,
                    section_index=section_index
                )
            ))
        # model_copy(update=...) 是 Pydantic v2 写法：复制 payload 只改 issues 字段
        payload = state["payload"].model_copy(update={"issues":state["issues"]})
    # state.get("plan", {}) 防御：Send 派发时若没塞 plan，退化为无指引
        draft = await writer.generate(payload,state.get("plan",{}))
        return {
            "draft":draft,
            "attempts":state["attempts"]+1
        }

# ── 节点 2：校对 agent ──
    async def proofreader_node(state:SectionGenState)->dict:
        """调校对 agent 检查 draft，返回 ProofReport。

        通过了 → 清空 issues（条件路由看到空列表就走向 END）
        没过 → 格式化问题，记录自纠历史
        """
        section_index = state[ "section_index" ] # 从 state 拿
        report = await proofreader.review(state["draft"],state["payload"])
        if on_event is not None:
            await on_event(CheckEvent(
                event="check",
                data = CheckData(
                    passed=report.passed,
                    issues = report.issues,
                    section_index=section_index
                )
            ))
        if report.passed:
            return { "issues" : []} # 清空，表示通过，不用再纠错


        history = state["issues_history"]+[{
            "attempts": state["attempts"],
            "issues":report.issues
        }] 
        return { "issues" : report.issues, "issues_history" : history}

    def should_continue(state:SectionGenState)->str:
        """根据 issues 和尝试次数决定走哪条路。

        返回 "revise" → 回 writer 节点重写
        返回 "end" → 到 END，结束
        """
        if not state["issues"]:
           # issues 为空 = 通过了 → 结束 
           return "end"  
        if state["attempts"] >= state["max_retries"]:
            return "end"
        return "revise"

    subgraph = StateGraph(state_schema=SectionGenState)
    subgraph.add_node("writer",writer_node)
    subgraph.add_node("proofreader",proofreader_node)
# 连线：入口 → writer → proofreader → 条件路由
    subgraph.add_edge(START,"writer")
    subgraph.add_edge("writer","proofreader")
    subgraph.add_conditional_edges(
        "proofreader",
        should_continue,
        {
            "revise":"writer", # 没过，还有次数 → 回 writer
            "end":END # 过了 / 用完次数 → 结束
        }
    )
    return subgraph.compile()

def  make_chief_editor_node(
    chief_editor:ChiefEditorAgent,
    on_event :OnEventCallback = None
):
    """工厂：返回 chief_editor_node 节点函数（闭包注入 chief_editor）。

    LangGraph 节点函数签名必须是 (state) -> dict，
    所以用闭包注入 chief_editor 实例（参照 build_section_subgraph 模式）。

    节点职责：
        读大纲 outline_sections + 简历基本信息 resume_basics → 调主编 agent →
        返回 {"chief_plan": {...}, "round": state["round"] + 1}

    fan_out 节点（阶段 4）会读 chief_plan 派发 5 段并发子图。

    参数：
        chief_editor: 主编 agent 实例
        on_event: 可选 async 回调，推 SSE 事件
    """
    async def chief_editor_node(state:MultiSectionState)->dict:
        """主编节点：分析大纲，产出分稿计划。"""
        if on_event is not None:
            await on_event(StatusEvent(
                event="status",
                data = StatusData(
                    stage="generating",
                    message="主编分析大纲，指定分稿计划",
                )
            ))
        
        plan = await chief_editor.generate(
            state["outline_sections"],
            state["resume_basics"]
        )

        return {
            "chief_plan":plan.model_dump()["sections"],
            "round":state["round"]+1
        }

    return chief_editor_node 


def build_multi_section_graph(
    chief_editor: ChiefEditorAgent,
    writer:DeepSeekWriterAgent,
    proofreader:DeepSeekProofReaderAgent,
    *,
    max_retries:int =2,
    on_event :OnEventCallback = None
):
    """构建多段内容生成主图（C+G 融合核心）。

    结构（阶段 4 版，cross_check 和 all_done 在阶段 5 加）：
        START → chief_editor → fan_out → section_subgraph → END
                                ↓ (派发 N 个并发 Send)
                                ↓ 每个跑 writer→proofreader 自纠环
                                ↓ 5 段汇合后 reducer 合并到 state["sections"]

    fan_out 用 Send API 把 chief_plan + 各段 payload 派发成 N 个 section_subgraph
    并发实例，每个实例跑自纠环，跑完把 SectionGenState 塞回主 state.sections
    （靠 merge_sections reducer 按 section_index_str 合并）。
    """
    # ── 主图节点：chief_editor ──
    chief_node = make_chief_editor_node(chief_editor,on_event)

    # ── 子图：section_subgraph（5 个并发实例共用同一个编译后的子图）──
    section_subgraph = build_section_subgraph(writer,proofreader,on_event)
    # 包装节点：调子图后把结果转成主图 sections 字段格式
    # 直接把子图当节点用时，子图 final state 的字段（draft/attempts/...）
    # 和主图字段（sections）对不上，LangGraph 会忽略——所以需要包装一层
    async def section_subgraph_node(state: SectionGenState) -> dict:
        """包装节点：调子图，把子图 final state 塞进主图 sections 字段。

        Send 派发到这个节点，节点内部调子图跑 writer→proofreader 自纠环，
        跑完后把整份子图 state 塞进 {"sections": {str(section_index): state}}，
        LangGraph 调 merge_sections reducer 合并 5 个 Send 的结果。
        """
        sub_result = await section_subgraph.ainvoke(state)
        section_index = sub_result["section_index"]
        return {
            "sections": {str(section_index): sub_result}
        }

    def fan_out(state:MultiSectionState)->list[Send]:
        """根据 chief_plan 派发 N 个并发 Send 到 section_subgraph 节点。

        每个 Send 的 state 是该段的初始 SectionGenState（payload + plan + section_index + 默认值）。
        Send 告诉 LangGraph："拿这份 state 去跑 section_subgraph 节点一次"。
        返回 list[Send] 让 LangGraph 并发执行所有 Send。
        """
        sends = []
        for i in range(state["section_count"]):
            # 从大纲拿段 i 的信息
            section = state["outline_sections"][i]
            # 构造段 i 的 payload（复用 ContentGenerationInput schema）
            resume_basics = state["resume_basics"]
            payload = ContentGenerationInput(
                title=resume_basics["title"],
                applicant_name=resume_basics["applicant_name"],
                target_position=resume_basics["target_position"],
                tone = resume_basics["tone"],
                content_density=resume_basics["content_density"],
                section_index = i,
                section_count = state["section_count"],
                section_title=section["title"],
                section_objective=section["content"],
                issues = []
            )
            # 从 chief_plan 拿段 i 的指引（key 是字符串 "0"/"1"/...）
            plan_i = state["chief_plan"].get(str(i),{})
            # 构造该段的初始 SectionGenState
            sub_state : SectionGenState = {
                "section_index" :i,
                "payload":payload,
                "plan":plan_i,
                "draft":None,
                "issues":[],
                "issues_history":[],
                "attempts":0,
                "max_retries":max_retries            
            }
            sends.append(Send("section_subgraph",sub_state))
        return sends

    graph = StateGraph(state_schema=MultiSectionState)
    graph.add_node("chief_editor",chief_node)
    graph.add_node("section_subgraph",section_subgraph_node)# 子图整体当主图节点
    graph.add_node("cross_check",make_cross_check_node(on_event))
    graph.add_node("all_done",make_all_done_node(on_event))

    graph.add_edge(START,"chief_editor")
# chief_editor 跑完走 fan_out 条件边——fan_out 返回 list[Send] 派发并发
    graph.add_conditional_edges(
        "chief_editor",
        fan_out,
        ["section_subgraph"]# 告诉 LangGraph fan_out 派发的目标节点列表
    )
# 5 段全部跑完汇合后到 END（阶段 5 加 cross_check 和 all_done 替换这条边）
    graph.add_edge("section_subgraph","cross_check")
    graph.add_edge("cross_check","all_done")
    graph.add_edge("all_done",END)

    return graph.compile()


def make_cross_check_node(on_event: OnEventCallback = None):
    """工厂：返回 cross_check 节点函数。

    节点职责：5 段全部汇合后，检查段间是否有重复公司/学校/技能。
    第一版用纯代码规则（不调 LLM），抓"完全相同的字段值在多段出现"。
    """
    def _extract_keywords(content, found):
        """递归从 content dict 里找 company/school/skill 字段值，加进 found set。"""
        if isinstance(content, dict):
            for key, val in content.items():
                if key in ("company", "school", "skill") and isinstance(val, str) and len(val) > 2:
                    found.add(val)
                else:
                    _extract_keywords(val, found)
        elif isinstance(content, list):
            for item in content:
                _extract_keywords(item, found)

    async def cross_check_node(state: MultiSectionState) -> dict:
        """跨段校验：检查段间重复的公司/学校/技能名。"""
        # 收集每段的关键字段值
        section_keywords = {}
        for k, sub_state in state["sections"].items():
            draft = sub_state.get("draft")
            if draft is None:
                section_keywords[k] = set()
                continue
            found = set()
            _extract_keywords(draft.content, found)
            section_keywords[k] = found

        # 找跨段重复
        issues = []
        keys = sorted(section_keywords.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                common = section_keywords[keys[i]] & section_keywords[keys[j]]
                if common:
                    issues.append(
                        f"段{keys[i]}与段{keys[j]}重复内容: {sorted(common)}"
                    )

        if on_event is not None:
            await on_event(CheckEvent(
                event="check",
                data=CheckData(
                    passed=not issues,
                    issues=issues,
                    # section_index 不传 = 整体级事件
                )
            ))
        return {"cross_issues": issues}

    return cross_check_node           

def make_all_done_node(on_event: OnEventCallback = None):
    """工厂：返回 all_done 节点函数。

    节点职责：推"全部完成"事件。落库交给 workflow 层（generate_all_contents_streaming）。
    保留这个节点是为了后续扩展（跨段校验失败 → 回 chief_editor 重排）。
    """
    async def all_done_node(state: MultiSectionState) -> dict:
        """完成节点：推事件，不改 state。"""
        if on_event is not None:
            # 统计每段最终 attempts
            attempts_summary = {
                k: sub_state.get("attempts", 0)
                for k, sub_state in state["sections"].items()
            }
            await on_event(StatusEvent(
                event="status",
                data=StatusData(
                    stage="done",
                    message="5 段内容生成完成",
                )
            ))
        return {}  # 不改 state

    return all_done_node