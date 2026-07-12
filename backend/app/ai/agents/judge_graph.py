"""LLM as a Judge — 综合解读报告质量审核 Agent。

审核 5 节结构化报告，放宽标准：主要结论有引用、无明显编造、结构完整即通过。
最多重试 1 次（实际重试逻辑在 interp_graph 的 after_judge 控制）。
"""
import logging

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field

from app.ai.llm import get_chat_model

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是体检报告解读质量审核员。审查的是一份综合性的 AI 解读报告（5 节 markdown），不是逐指标的解释。

审核标准（放宽）：
1. 结构完整：5 节均非空（trend_note 允许为空，仅在首份报告无历史时）。
2. 主要结论可追溯：abnormal_focus / suggestions / risk_alert 中的关键结论应能对应到 references 列表中的 [n] 标记。
3. 无明显编造：不要凭空断言数值或疾病；如某节陈述与上传的异常指标无关，视为问题。
4. 引用合理性：references 中每条 entry_id/title 应能在 abnormal_focus/suggestions 等节被 [n] 提及。

判断结果：
- passed=true：结构完整且无上述问题。
- passed=false：列出具体问题（哪节哪句缺引用/编造），并给改进建议。

注意：放宽评判，10 次里有 8 次应该通过，避免过度否定。"""


class JudgeResult(BaseModel):
    passed: bool = Field(description="审核是否通过")
    issues: list[str] = Field(default_factory=list, description="具体问题列表")
    suggestions: str = Field(default="", description="改进建议")


def build_judge_agent():
    model = get_chat_model(streaming=False)
    model.max_tokens = 16384
    return create_agent(
        model=model,
        tools=[],
        system_prompt=JUDGE_SYSTEM_PROMPT,
        response_format=ToolStrategy(JudgeResult),
    )


def _format_for_review(state: dict) -> str:
    report = state.get("report")
    references = state.get("references", []) or []
    abnormal = state.get("abnormal_indicators", []) or []

    lines = ["请审核以下综合解读报告：\n"]
    if abnormal:
        lines.append("## 异常指标（输入）")
        for ind in abnormal:
            lines.append(f"- {ind.get('item_name')}: 值 {ind.get('result_value')}{ind.get('unit','')}, "
                         f"参考 {ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}, "
                         f"{ind.get('deviation')}, {ind.get('color_level')}区")
        lines.append("")

    if report is not None:
        lines.append("## 综合报告（5 节）")
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
        agent = build_judge_agent()
        result = agent.invoke({"messages": [HumanMessage(content=review_text)]})
        judge_result = result.get("structured_response")
        if judge_result is None:
            logger.warning("Judge returned no structured_response, assuming passed")
            return {"passed": True, "issues": [], "suggestions": ""}
        return judge_result.dict()
    except Exception as e:
        logger.warning("Judge agent failed: %s, assuming passed", e)
        return {"passed": True, "issues": [], "suggestions": ""}
