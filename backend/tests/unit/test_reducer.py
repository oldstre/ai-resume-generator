"""merge_sections reducer 单元测试。

reducer 是 LangGraph 自定义状态合并的核心——5 段并发子图跑完后，
LangGraph 调它把 5 份结果合并到 state["sections"]。

面试点：reducer 必须用浅拷贝（{**left}）而非 in-place（left.update(right)），
否则 LangGraph 内部缓存与实际值不一致。
"""
from app.llm.multi_section_state import merge_sections


def test_merge_disjoint_keys():
    """left 和 right 的 key 不重叠 → 合并后有所有 key。"""
    left = {"0": {"draft": "A"}, "1": {"draft": "B"}}
    right = {"2": {"draft": "C"}}
    result = merge_sections(left, right)
    assert set(result.keys()) == {"0", "1", "2"}
    assert result["2"]["draft"] == "C"


def test_merge_same_key_overwrites():
    """同 key → 右覆盖左（最后写赢）。"""
    left = {"0": {"draft": "old", "attempts": 1}}
    right = {"0": {"draft": "new", "attempts": 2}}
    result = merge_sections(left, right)
    assert result["0"]["draft"] == "new"
    assert result["0"]["attempts"] == 2


def test_merge_does_not_mutate_left():
    """reducer 不能 in-place 修改 left（LangGraph 内部缓存约束）。"""
    left = {"0": {"draft": "A"}}
    left_copy = {"0": {"draft": "A"}}
    right = {"1": {"draft": "B"}}
    merge_sections(left, right)
    # left 原字典不应被修改
    assert left == left_copy


def test_merge_empty_left():
    """left 为空 → 结果 = right。"""
    right = {"0": {"draft": "A"}}
    result = merge_sections({}, right)
    assert result == right


def test_merge_empty_right():
    """right 为空 → 结果 = left。"""
    left = {"0": {"draft": "A"}}
    result = merge_sections(left, {})
    assert result == left


def test_merge_multiple_sends():
    """模拟 5 段并发 Send：连续 5 次 merge，每次 right 只有 1 个 key。"""
    sections = {}
    for i in range(5):
        sections = merge_sections(sections, {str(i): {"draft": f"段{i}"}})
    assert len(sections) == 5
    for i in range(5):
        assert sections[str(i)]["draft"] == f"段{i}"
