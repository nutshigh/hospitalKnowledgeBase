"""医院报告模板档案 —— 新模板适配从这里"填配置", 不写进主逻辑。

主逻辑(app.modules.report.service 的切段/重排/提取)维护一套通用默认规则,
本文件按"医院关键词"提供模板级覆盖。新增医院模板流程:
1. 样本 PDF 放入 体检报告样例/ 并加入 tests/modules/report/conclusion_samples.py
2. 在本文件 PROFILES 登记一条(keywords 为该机构在报告页眉/首页出现的名称变体)
3. 若模板有布局/词表特殊性, 填 visual_sort / extra_break / extra_skip
4. 跑 pytest tests/modules/report/test_conclusion_regression.py 验收

字段说明:
- keywords: 命中即应用本档案(与报告全文做子串匹配, 任一命中)
- visual_sort: PDF 页内为多栏混排, 默认文本流乱序, 需按视觉坐标(y,x)重排
  (防城港市中医医院"目录表+详情"同页混排即此类)
- extra_break: 追加的结论段尾断点行(词/正则片段, 整行命中即该段到此为止)
- extra_skip: 追加的页眉/装饰行(词/正则片段, 整行命中仅跳过该行)
- extra_anchor: 追加的"结论段标题行"(正则片段, 整行 match 即作锚点) ——
  模板标题用词特殊时(厦门弘爱"自测问卷发现的主要疾病及健康危险因素:")
  通用锚点词表(建议/结论/汇总/分析…)拼不出整行, 在此声明。


示例: 某新医院结论标题特殊且页脚有固定广告:
    dict(keywords=["XX市人民医院"], extra_skip=["XX健康伴您行", r"^\d{4}-\d{2}-\d{2}$"])
"""
import re

DEFAULT = {
    "visual_sort": False,
    "extra_break": [],
    "extra_skip": [],
    "extra_anchor": [],
}

PROFILES = [
    # 防城港市中医医院: 总检页"本次体检总结(目录)+健康指导建议(详情)"多栏混排,
    # 默认 get_text 文本流把详情块排到目录前 → 需视觉坐标重排
    dict(keywords=["防城港市中医医院"], visual_sort=True),
    # 德宏州人民医院: 每条结论下重复"检查所见:"/"总检建议:"字段标签,
    # 通用规则把它们当细节段起点/锚点 → 正文被吞; 改为跳过标签行(内容保留)
    dict(keywords=["德宏州人民医院"],
         extra_skip=[r"^(?:检查所见|总检建议|处理建议|诊断建议)[:：]?$"]),
    # === 2026-09-08: USER6 结论段(anchor_only = 只取专用锚点段) ===
    dict(keywords=["出入镜"],
         anchor_only=True,
         extra_anchor=[r"^1[.、]\s*高甘油三酯血症及高密度脂蛋白降低"],
         extra_break=["检查项目", r"^[（(]?1?\s*[）)]?\s*吸烟", r"^[（(]?2?\s*[）)]?\s*饮酒"]),  # 21 华西: 列表尾到 9.超重, 吸烟/饮酒=个人史不入段
    dict(keywords=["出入境边防检查总站"],
         anchor_only=True, table_conclusion=True,
         extra_anchor=["三、体检异常结果及医学建议"]),  # 22 厦门弘爱(表格结论, 需行重组)
    dict(keywords=["日照"],
         anchor_only=True, extra_anchor=["医学建议"]),  # 24 日照人民
    dict(keywords=["医 生 建 议"],
         anchor_only=True,
         extra_anchor=[r"医\s*生\s*建\s*议"],
         review_block=True),  # 25 茂名人民(分散字标题; 切段后剥离中段
         # "检 查 综 述"★枚举页 —— 综述条目与医生建议重复, 且该页插在建议区中间
    dict(keywords=["莆田九十五医院"],
         anchor_only=True, visual_sort=True,
         extra_anchor=["体检结论分析"]),  # 26 莆田九十五: 文本流逐cell乱序,
         # 汇总/分析两列表单元格交错, 需版面(y)排序后定位(真实首条=(1)超重)
    dict(keywords=["齐鲁医院(青岛)", "齐鲁医院（青岛）"],
         anchor_only=True,
         extra_anchor=["您本次体检的建议如下"],
         extra_break=["项目检查结果汇总", "检查项目"]),  # 27 齐鲁青岛(结论在报告开头)
    # 池州市人民医院(USER5): 报告含"本次体检结论"(表格, 覆盖面小)/"体检综述"
    # (编号小结标题 1-14, 真发现全)/"健康指导建议"(★ 逐条+建议)/"温馨提醒"(模板,
    # 无临床内容)。只取 体检综述+健康指导建议 两段, 断在"温馨提醒"前。
    dict(keywords=["池州市人民医院"],
         anchor_only=True,
         extra_anchor=[r"^体\s*检\s*综\s*述\s*$", r"^健康指导建议"],
         extra_break=["温馨提醒"]),

]


def match_profile(text: str) -> dict:
    """按报告全文匹配医院档案; **合并所有命中档案**(2026-09-08) —— 同一报告可能
    命中多个档案(莆田报告同时含"莆田九十五医院"与"出入境边防检查站"字样), 首个命中
    返回会让专用锚点被通用档案顶掉; 合并时 extra_break/skip/anchor 拼接, anchor_only
    取或。未命中返回 DEFAULT。"""
    merged = dict(DEFAULT)
    hit = False
    for prof in PROFILES:
        if not any(kw in text for kw in prof.get("keywords", [])):
            continue
        hit = True
        if prof.get("anchor_only"):
            merged["anchor_only"] = True
        if prof.get("visual_sort"):
            merged["visual_sort"] = True
        if prof.get("table_conclusion"):
            merged["table_conclusion"] = True
        if prof.get("review_block"):
            merged["review_block"] = True
        for f in ("extra_break", "extra_skip", "extra_anchor"):
            if prof.get(f):
                merged.setdefault(f, []).extend(list(prof[f]))
    return merged if hit else merged


def compile_profile(profile: dict) -> dict:
    """把 profile 的 extra_* 词列表编译为正则(供 locate 追加判定)。"""
    out = dict(profile)
    out["extra_break_re"] = (
        re.compile("|".join(profile["extra_break"])) if profile.get("extra_break") else None
    )
    out["extra_skip_re"] = (
        re.compile("|".join(profile["extra_skip"])) if profile.get("extra_skip") else None
    )
    out["extra_anchor_re"] = (
        re.compile("|".join(profile["extra_anchor"])) if profile.get("extra_anchor") else None
    )
    return out
