"""确定性知识检索 planner/executor 单测(2026-09-13)。

背景: 解读知识检索原为模型自驱 ReAct agent(build_interp_agent), report 31 曾因
MedGo 单步生成退化循环卡死约 30 分钟, 且 agent 实际只是把指标名原样照抄为 query。
改造为确定性 planner + executor。本测试锁定: 去重/计划字段/fallback/空结果容错/
不调用 LLM/refs 形状与现状一致。
"""
from unittest.mock import patch

from app.ai.agents.knowledge_search import (
    SearchCall,
    execute_search_plan,
    plan_knowledge_search,
    run_knowledge_search,
    _refs_dict_from_search_results,
)
from app.modules.knowledge.schemas import SearchResult


def _abnormal(*names):
    return [{"item_name": n} for n in names]


def test_plan_dedups_and_uses_indicator_name_as_query():
    """重复指标名去重, 每条 query = 指标名(与 indicator_judgment.item_name 一致)。"""
    plan = plan_knowledge_search(_abnormal("FPSA/TPSA", "尿酸", "总胆固醇", "血糖", "尿酸"))
    assert len(plan) == 4
    assert [c.indicator for c in plan] == ["FPSA/TPSA", "尿酸", "总胆固醇", "血糖"]
    assert [c.query for c in plan] == ["FPSA/TPSA", "尿酸", "总胆固醇", "血糖"]


def test_plan_skips_blank_names():
    plan = plan_knowledge_search(_abnormal("", "尿酸", None))
    assert [c.indicator for c in plan] == ["尿酸"]


def test_plan_fallback_splits_slash():
    """FPSA/TPSA 原名检索 0 条(实测), fallback 应含斜杠拆分子项。"""
    plan = plan_knowledge_search(_abnormal("FPSA/TPSA"))
    fallback = plan[0].fallback_queries
    assert len(fallback) <= 3
    assert "FPSA" in fallback
    assert "TPSA" in fallback
    assert "FPSA/TPSA" not in fallback


def test_plan_fallback_contains_normalized_name():
    """口语别名 -> 归一化名/标准名 作为 fallback 候选。"""
    plan = plan_knowledge_search(_abnormal("谷丙转氨酶"))
    fallback = plan[0].fallback_queries
    assert fallback, "应产出归一化 fallback"
    assert any("丙氨酸氨基转移酶" in q for q in fallback)
    assert "谷丙转氨酶" not in fallback


def test_plan_fallback_strips_parenthetical_code():
    plan = plan_knowledge_search(_abnormal("血红蛋白（HGB）"))
    fallback = plan[0].fallback_queries
    assert "血红蛋白" in fallback


def test_execute_uses_fallback_when_primary_empty():
    """首选 0 结果时依次尝试 fallback, 命中即合并。"""
    calls = []

    def fake_search(hospital_id, query, category_ids=None, top_k=None):
        calls.append((hospital_id, query))
        if query == "游离前列腺特异性抗原":
            return [SearchResult(entry_id=7, title="FPSA 知识", content="x", score=0.9)]
        return []

    plan = [SearchCall(indicator="FPSA/TPSA", query="FPSA/TPSA",
                       fallback_queries=["游离前列腺特异性抗原", "FPSA"])]
    merged = execute_search_plan("H004", plan, search_fn=fake_search)

    assert 7 in merged
    assert merged[7]["title"] == "FPSA 知识"
    assert calls == [("H004", "FPSA/TPSA"), ("H004", "游离前列腺特异性抗原")]


def test_execute_stops_at_first_hit_without_further_fallback():
    calls = []

    def fake_search(hospital_id, query, category_ids=None, top_k=None):
        calls.append(query)
        return [SearchResult(entry_id=1, title="t", content="c", score=0.5)]

    plan = [SearchCall(indicator="尿酸", query="尿酸", fallback_queries=["UA", "x"])]
    execute_search_plan("H001", plan, search_fn=fake_search)
    assert calls == ["尿酸"]


def test_execute_empty_results_skips_without_error():
    """全部空结果: 不抛异常, 返回空 dict。"""
    plan = [SearchCall(indicator="X", query="X", fallback_queries=["Y", "Z"])]
    merged = execute_search_plan("H004", plan, search_fn=lambda *a, **k: [])
    assert merged == {}


def test_execute_swallows_search_exception():
    """单次检索抛异常不应中断整批(确定性 executor 必须容错)。"""

    def boom(*a, **k):
        raise RuntimeError("milvus down")

    plan = [SearchCall(indicator="X", query="X", fallback_queries=[])]
    assert execute_search_plan("H004", plan, search_fn=boom) == {}


def test_refs_dict_shape_document_and_kg():
    """文档 key=int entry_id; KG key=`kg:<title>`; 字段集与现状一致。"""
    results = [
        SearchResult(entry_id=101, title="知识A", content="c1", score=0.9),
        SearchResult(entry_id=None, title="KG节点", content="c2", score=0.8,
                     source="knowledge_graph"),
        SearchResult(entry_id=None, title="KG节点", content="c2-dup", score=0.7,
                     source="knowledge_graph"),
    ]
    refs = _refs_dict_from_search_results(results)
    assert refs[101] == {"entry_id": 101, "title": "知识A",
                         "source": "document", "content": "c1"}
    assert refs["kg:KG节点"]["entry_id"] is None
    assert refs["kg:KG节点"]["source"] == "knowledge_graph"
    assert refs["kg:KG节点"]["content"] == "c2"


def test_run_knowledge_search_never_calls_llm():
    """根治点: 该步确定性地只调 ai_rag.search, 绝不触达 MedGo/LLM。"""
    with patch("app.ai.llm.get_chat_model", side_effect=AssertionError("LLM 被调用")) as m, \
         patch("app.ai.agents.knowledge_search.ai_rag.search",
               return_value=[SearchResult(entry_id=9, title="尿酸知识", content="x", score=0.9)]):
        out = run_knowledge_search("H004", _abnormal("FPSA/TPSA", "尿酸", "总胆固醇", "血糖"))
    assert 9 in out
    m.assert_not_called()


def test_run_knowledge_search_empty_indicators_short_circuits():
    with patch("app.ai.agents.knowledge_search.ai_rag.search") as s:
        assert run_knowledge_search("H004", []) == {}
    s.assert_not_called()
