"""think_filter 单测:strip_think_tags 对非字符串 LLM 字段的防御。

背景(2026-09-03):MedGo 结构化输出偶尔不守 schema,把报告 JSON 的某字段
(如 abnormal_focus)输出为数组/嵌套对象而非字符串。interp_graph._generate_report
把字段原样传给 strip_think_tags → re.sub 收到 list 抛
TypeError: expected string or bytes-like object,整份解读失败走重试(~2 次、各耗
~20-40s)。修复:strip_think_tags 入口对非字符串做序列化,确保永不抛 TypeError。
"""
import json

from app.ai.agents.think_filter import strip_think_tags


def test_strip_think_tags_removes_blocks_and_whitespace():
    text = "<think>推理</think>\n\n 结果内容  \n"
    assert strip_think_tags(text) == "结果内容"


def test_strip_think_tags_handles_empty_and_none():
    assert strip_think_tags("") == ""
    assert strip_think_tags(None) is None


def test_strip_think_tags_handles_leading_open_think_tag():
    text = "<think>\n没有闭合标签的内容"
    assert strip_think_tags(text) == "没有闭合标签的内容"


def test_strip_think_tags_serializes_list_field():
    """abnormal_focus 等字段被 MedGo 输出成数组时不抛 TypeError,序列化为 JSON 文本。"""
    raw = ["血糖偏高", "血压 150/95"]
    out = strip_think_tags(raw)
    assert isinstance(out, str)
    assert out == json.dumps(raw, ensure_ascii=False)


def test_strip_think_tags_serializes_dict_field():
    raw = {"level": "red", "note": "建议复查"}
    out = strip_think_tags(raw)
    assert isinstance(out, str)
    assert json.loads(out) == raw


def test_strip_think_tags_serializes_number_field():
    assert strip_think_tags(123) == "123"
