"""LLM as a Judge — 报告解读质量审核 Agent。

独立 Agent，用 MedGo 但有自己的 system prompt 和角色。
审核生成 Agent 的输出：可追溯性、编造检测、确定性合理性。
不通过时给出 issues + suggestions，供生成 Agent 回滚重试。
"""
import logging
from typing import List

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field

from app.ai.llm import get_chat_model

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是体检报告解读质量审核员。你的职责是审查 AI 生成的解读报告，判断质量是否合格。

审核标准：
1. 可追溯性：explanation 和 suggestion 中的每个结论性陈述是否有对应的 [n] 标记，且该 [n] 在 citations 列表中有对应条目
2. 编造检测：是否存在没有引用支撑的结论性陈述（有结论但无 [n] 标记，或 [n] 在 citations 中找不到对应条目）
3. 确定性合理性：certainty 级别是否与结论性质匹配
   - definite 应仅用于基于明确指标数值与参考范围直接对比的判断
   - probable 用于基于知识库推理的结论
   - refused 用于信息不足的情况

判断结果：
- passed=true：所有结论可追溯，无编造，确定性合理
- passed=false：列出具体问题（哪条指标的哪个结论缺少引用/编造/确定性不合理），给出改进建议

注意：只审核有 explanation 内容的指标，空 explanation 的指标跳过。"""


class JudgeResult(BaseModel):
    """Judge 审核结果"""
    passed: bool = Field(description="审核是否通过")
    issues: list[str] = Field(default_factory=list, description="具体问题列表，如 '指标ID:5 的 explanation 中血压偏高缺少引用标注'")
    suggestions: str = Field(default="", description="改进建议，回传给生成 Agent")


def build_judge_agent():
    """构造 Judge Agent（无工具，纯文本审查 + 结构化输出）"""
    model = get_chat_model(streaming=False)
    model.max_tokens = 2048
    return create_agent(
        model=model,
        tools=[],
        system_prompt=JUDGE_SYSTEM_PROMPT,
        response_format=ToolStrategy(JudgeResult),
    )


def _format_for_review(state: dict) -> str:
    """把 agent_batch 的输出格式化为审查输入文本。"""
    lines = ["请审核以下体检报告解读结果：\n"]
    explanations = state.get("agent_explanations", {})
    refs = state.get("knowledge_refs", {})
    abnormal = state.get("abnormal_indicators", [])

    for ind in abnormal:
        iid = ind["indicator_id"]
        exp_data = explanations.get(iid, {})
        explanation = exp_data.get("explanation", "")
        suggestion = exp_data.get("suggestion", "")
        certainty = exp_data.get("certainty", "")
        certainty_reason = exp_data.get("certainty_reason", "")
        item_refs = refs.get(iid, [])

        if not explanation:
            continue

        lines.append(f"## 指标 ID: {iid} ({ind['item_name']})")
        lines.append(f"值: {ind['result_value']}{ind.get('unit', '')}, "
                     f"参考区间: {ind.get('ref_range_low', '-')}-{ind.get('ref_range_high', '-')}, "
                     f"{ind['deviation']}, {ind['color_level']}区")
        lines.append(f"certainty: {certainty}")
        lines.append(f"certainty_reason: {certainty_reason}")
        lines.append(f"explanation: {explanation}")
        lines.append(f"suggestion: {suggestion}")
        lines.append("citations:")
        for ref in item_refs:
            lines.append(f"  [{ref.get('ref_id', '?')}] entry_id={ref.get('entry_id')}, "
                         f"title={ref.get('title', '')}, source={ref.get('source', '')}")
        lines.append("")

    return "\n".join(lines)


def run_judge(state: dict) -> dict:
    """执行 Judge 审核，返回 {passed, issues, suggestions} dict。

    Judge 调用失败时视为通过（不阻塞流程）。
    """
    review_text = _format_for_review(state)
    if not review_text.strip():
        return {"passed": True, "issues": [], "suggestions": ""}

    try:
        agent = build_judge_agent()
        result = agent.invoke(
            {"messages": [HumanMessage(content=review_text)]},
        )
        judge_result = result.get("structured_response")
        if judge_result is None:
            logger.warning("Judge returned no structured_response, assuming passed")
            return {"passed": True, "issues": [], "suggestions": ""}
        return judge_result.dict()
    except Exception as e:
        logger.warning("Judge agent failed: %s, assuming passed", e)
        return {"passed": True, "issues": [], "suggestions": ""}
