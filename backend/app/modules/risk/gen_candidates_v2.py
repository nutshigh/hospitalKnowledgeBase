"""一次性: 用 MedGo 基于真实报告指标清单生成 指标→病种 候选映射(单指标+组合, 含级别/方向)。

产物: backend/app/modules/risk/candidates/llm-candidates-2026-08-15.json
仅供人工审核, 不直接落库。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.llm import get_chat_model, _guarded

PROMPT_TEMPLATE = """你是体检报告解读专家。以下是两家真实体检机构报告中出现过的检验指标、查体指标与影像/总检结论条目清单(已归一化为标准名, 标注了类型)。

请为其中"异常时可能指向慢性病(CHRONIC)或重大疾病(MAJOR)"的条目生成候选映射;对需要多个条目同时异常才能指向的病种, 生成严格组合规则(全部成员须同时异常)。

要求:
1. 疾病命名用标准疾病名(如"高血压""糖尿病""血脂异常""恶性肿瘤(疑似)""脑卒中");重大疾病用筛查疑似口径(疾病名后缀"(疑似)")。
2. disease_category 取值: CHRONIC(慢性病)/MAJOR(重大疾病)/OTHER(危险因素或亚健康, 如超重、肥胖)。
3. disease_class 用系统分类: 心血管系统/内分泌代谢/消化系统/呼吸系统/泌尿系统/肿瘤/神经系统/其他。
4. 级别与方向条件(重要):
   - match_level: YELLOW=黄色及以上判定即命中; RED=仅红色判定命中(用于程度区分, 如肥胖症仅当 BMI 红色判定)。
   - match_deviation: "偏高"/"偏低"/"异常"(如"阳性")或 null=不约束方向。
   - 方向性指标必须约束方向: 如收缩压偏高→高血压, 收缩压偏低不应命中高血压。
5. 组合规则仅在确有医学依据时生成(如 收缩压+舒张压 同高→高血压), rule_code 用 C- 前缀; 成员对象含 name/min_level/deviation 三字段。
6. 条目名必须用清单中的标准名, 不要改写。
7. 只输出 JSON, 不要其他文字。

清单:
{indicators}

输出 JSON 格式:
{{"single": [{{"item_name": "标准名", "disease_name": "疾病名", "disease_category": "CHRONIC", "disease_class": "分类", "match_level": "YELLOW", "match_deviation": "偏高"}}], "combos": [{{"rule_code": "C-XX", "disease_name": "疾病名", "disease_category": "CHRONIC", "disease_class": "分类", "member_items": [{{"name": "标准名", "min_level": "YELLOW", "deviation": "偏高"}}]}}]}}
"""


def build_indicators():
    p = Path(__file__).resolve().parent / "candidates" / "indicators-2026-08-15.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for it in data["indicators"]:
        name = it["name"]
        note = it.get("note", "")
        out.append(f"- {name} ({it['type']}{', ' + note if note else ''})")
    return "\n".join(sorted(out))


async def generate():
    model = get_chat_model()
    prompt = PROMPT_TEMPLATE.format(indicators=build_indicators())
    resp = await _guarded(model.ainvoke([("user", prompt)], max_tokens=8192))
    content = resp.content
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"LLM 输出无 JSON: {content[:500]}")
    return json.loads(content[start:end + 1])


def main():
    result = asyncio.run(generate())
    out = Path(__file__).resolve().parent / "candidates" / "llm-candidates-2026-08-15.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")
    print(f"single={len(result.get('single', []))} combos={len(result.get('combos', []))}")


if __name__ == "__main__":
    main()
