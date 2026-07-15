"""LLM as a Judge — 综合解读报告质量审核。

审核 5 节结构化报告,放宽标准:主要结论有引用、无明显编造、结构完整即通过。
最多重试 1 次(实际重试逻辑在 interp_graph 的 after_judge 控制)。

注意:不使用 `response_format=ToolStrategy(JudgeResult)` —— 实测 MedGo(Qwen3-32B
reasoning 微调)在 vLLM guided JSON 数组模式下,模型"想思考"但不能输出 thinking
文本(违反 JSON 语法),会退化成在 `[` 之后反复吐 `\n\n  ` 空白死循环直至撞
max_tokens 截断,然后 hermes parser 报 "EOF while parsing a list at line
32767"(参见 errorRecord.md 2026-07-14 第二次事故)。改用纯文本模式 + 显式
strip_think_tags + json/repair_json 解析,thinking 就能正常吐出再被剥离。
"""
import asyncio
import json
import logging
import re

from json_repair import repair_json
from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.ai.agents.think_filter import strip_think_tags
from app.ai.llm import get_chat_model, _guarded

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是体检报告解读质量审核员。审查的是一份综合性的 AI 解读报告(5 节 markdown),不是逐指标的解释。

审核标准(放宽):
1. 结构完整:5 节均非空(trend_note 允许为空,仅在首份报告无历史时)。
2. 主要结论可追溯:abnormal_focus / suggestions / risk_alert 中的关键结论应能对应到 references 列表中的 [n] 标记。
3. 无明显编造:不要凭空断言数值或疾病;如某节陈述与上传的异常指标无关,视为问题。
4. 引用合理性:references 中每条 entry_id/title 应能在 abnormal_focus/suggestions 等节被 [n] 提及。

判断结果(返回纯 JSON,不要 markdown 代码块,不要 thinking):
{
  "passed": true 或 false,
  "issues": ["具体问题1", "具体问题2"],
  "suggestions": "改进建议"
}
- passed=true:结构完整且无上述问题,issues 为空数组、suggestions 留空字符串。
- passed=false:列出具体问题(哪节哪句缺引用/编造),并给改进建议。

注意:放宽评判,10 次里有 8 次应该通过,避免过度否定。"""


class JudgeResult(BaseModel):
    passed: bool = Field(description="审核是否通过")
    issues: list[str] = Field(default_factory=list, description="具体问题列表")
    suggestions: str = Field(default="", description="改进建议")


def build_judge_model():
    """构造 judge 用的 chat model(纯文本模式,不再用 ToolStrategy/guided JSON)。

    max_tokens=8192:JudgeResult schema 极简,正常 200 token 以内足够;
    留宽裕度是为了应对 MedGo thinking 内容(会被 strip_think_tags 剥离)。
    """
    model = get_chat_model(streaming=False)
    model.max_tokens = 8192
    return model


_JSON_OBJECT_RE = re.compile(r'\{[\s\S]*\}')


def _parse_judge_response(raw: str) -> JudgeResult | None:
    """从模型纯文本响应里提取 JudgeResult JSON。

    思路:先剥 thinking 标签 → 找首个 `{...}` → json.loads → 不行就 repair_json →
    再不行返回 None(上游会回退到 passed=true,不阻塞用户)。
    """
    if not raw:
        return None
    cleaned = strip_think_tags(raw)
    match = _JSON_OBJECT_RE.search(cleaned)
    if not match:
        logger.warning("Judge response has no JSON object: %r", cleaned[:200])
        return None
    raw_json = match.group()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        try:
            data = json.loads(repair_json(raw_json))
        except Exception as e:
            logger.warning("Judge JSON repair failed: %s; raw=%r", e, raw_json[:200])
            return None
    try:
        return JudgeResult.model_validate(data)
    except Exception as e:
        logger.warning("JudgeResult validation failed: %s; data=%r", e, data)
        return None


def _format_for_review(state: dict) -> str:
    report = state.get("report")
    references = state.get("references", []) or []
    abnormal = state.get("abnormal_indicators", []) or []

    lines = ["请审核以下综合解读报告:\n"]
    if abnormal:
        lines.append("## 异常指标(输入)")
        for ind in abnormal:
            lines.append(f"- {ind.get('item_name')}: 值 {ind.get('result_value')}{ind.get('unit','')}, "
                         f"参考 {ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}, "
                         f"{ind.get('deviation')}, {ind.get('color_level')}区")
        lines.append("")

    if report is not None:
        lines.append("## 综合报告(5 节)")
        lines.append(f"### 整体评估\n{getattr(report, 'overall_summary', '')}")
        lines.append(f"### 重点异常解读\n{getattr(report, 'abnormal_focus', '')}")
        lines.append(f"### 历年趋势\n{getattr(report, 'trend_note', '')}")
        lines.append(f"### 健康建议\n{getattr(report, 'suggestions', '')}")
        lines.append(f"### 风险提示\n{getattr(report, 'risk_alert', '')}")
        lines.append("")

    if references:
        lines.append("## 参考来源列表")
        for ref in references:
            lines.append(f"- [{ref.get('ref_id')}] entry_id={ref.get('entry_id')}, "
                         f"title={ref.get('title','')}, source={ref.get('source','')}")
        lines.append("")

    return "\n".join(lines)


def run_judge(state: dict) -> dict:
    review_text = _format_for_review(state)
    if not review_text.strip():
        return {"passed": True, "issues": [], "suggestions": ""}

    try:
        model = build_judge_model()
        resp = asyncio.run(_guarded(model.ainvoke([
            ("system", JUDGE_SYSTEM_PROMPT),
            ("user", review_text),
        ]))).content
        judge_result = _parse_judge_response(resp)
        if judge_result is None:
            logger.warning("Judge returned unparseable response, assuming passed. raw=%r",
                           (resp or "")[:200])
            return {"passed": True, "issues": [], "suggestions": ""}
        return judge_result.model_dump()
    except Exception as e:
        logger.warning("Judge model failed: %s, assuming passed", e)
        return {"passed": True, "issues": [], "suggestions": ""}