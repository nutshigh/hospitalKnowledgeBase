import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.async_run import run_async
from app.config import settings
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator
from app.core.vlm_client import vlm_client
from app.core.term_normalizer import normalize_indicators, normalize_item_name
from app.core.image_preprocess import preprocess
from app.core.rabbitmq import rabbitmq, TaskMessage

# 2026-09-03: 医院模板档案(见 report_profiles.py) —— lazy import 防循环
def _load_report_profiles():
    from app.modules.report.report_profiles import compile_profile, match_profile
    return match_profile, compile_profile


def _compile_profile_re(profile: Optional[dict]) -> dict:
    """把医院档案(可能未编译)转成含 extra_*_re 的 dict。"""
    if not profile:
        return {"extra_break_re": None, "extra_skip_re": None, "extra_anchor_re": None}
    if "extra_break_re" in profile:
        return profile
    _, compile_profile = _load_report_profiles()
    return compile_profile(profile)

_log = logging.getLogger("app.parse")


def create_task(db: Session, hospital_id: str, user_id: str, file_path: str,
                filename: str, file_type: str, file_size: int,
                name: Optional[str] = None,
                thumbnail_path: Optional[str] = None,
                priority: str = "normal",
                batch_id: Optional[str] = None,
                file_id: Optional[str] = None,
                batch_hospital_id: Optional[str] = None) -> ReportTask:
    # 向后兼容: legacy int priority(0=normal, 1=urgent)
    if isinstance(priority, int):
        priority = "urgent" if priority else "normal"
    # DB 列 priority 是 Integer(BIGINT),不能存字符串;按 str→int 映射落库
    priority_for_db = {"normal": 0, "urgent": 1, "bulk": 100}[priority]
    task = ReportTask(
        user_id=user_id, original_file_path=file_path, original_filename=filename,
        file_type=file_type, file_size=file_size, thumbnail_path=thumbnail_path,
        status="queued", priority=priority_for_db,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Create report_info immediately so it appears on home page
    report = ReportInfo(task_id=task.id, user_id=user_id, name=name)
    db.add(report)
    db.commit()

    payload = {"task_id": task.id, "hospital_id": hospital_id, "file_path": file_path}
    if batch_id is not None:
        payload["batch_id"] = batch_id
    if file_id is not None:
        payload["file_id"] = file_id
    if batch_hospital_id is not None:
        payload["batch_hospital_id"] = batch_hospital_id
    rabbitmq.publish(TaskMessage(
        task_type="parsing", hospital_id=hospital_id, priority=priority,
        payload=payload,
    ))
    return task


def get_task_status(db: Session, task_id: int) -> Optional[ReportTask]:
    return db.query(ReportTask).filter(ReportTask.id == task_id).first()


_CONCLUSION_PROMPT = """以下是一份体检报告的完整文本。请提取其中"总检建议与结论"段落的全部内容。

提示：该段落通常以"总检建议与结论""总检结论""医师建议""综合建议""健康指导"等标题开头，以"主检医师""主检医生""总检医师""总检医生""一般项目""一般检查""检查项目"等标识结束。

请只输出提取到的内容原文（包含章节标题），不要加任何说明。如果确实找不到，输出NONE。

报告文本：
{text}"""


def _clean_conclusion(content: str) -> Optional[str]:
    """清理 LLM 返回的结论文本，去除 think 标签、截断主检医生部分、空响应。"""
    import re
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    content = content.replace('</think>', '').replace('<think>', '')
    # 截断主检医生/总检医生之后的内容
    content = re.split(r'(?:主检医生|总检医生|主检医师|总检医师)\s*[：:]', content)[0]
    # 移除开头的章节标题重复
    content = re.sub(r'^(总检建议与结论|总检结论|医师建议|综合建议|健康指导)\s*\n+', '', content)
    content = content.strip()
    if not content or content.upper() in ("NONE", "(无)", "无", "NULL"):
        return None
    if len(content) < 10:
        return None
    return content


# === 方案4(2026-08-24): 发现段确定性锚点定位 ===
# 各医院"发现"所在标题不同: 体检结果综述/总检建议/主检报告/医生建议/结果分析与建议/异常指标 等。
# 切段规则: 收集**所有**锚点段(发现可能散在多处, 如多个"超声提示"), 每段到下一锚点/广告词;
# 跳过重复行(页眉页脚); 遇页脚广告词停止。
# 2026-08-28: 锚点从枚举改为"标题模式"泛化 —— 行整行由 医学词+结论词 组成且含强词
# (建议|结论|汇总|发现|指导|分析|终审)。覆盖: 医生建议/体检结论分析/体检结论与建议/
# 健康建议/健康指导建议/体检结果分析与建议/体检结论/本次体检结论及健康指导建议/
# 体检发现/异常指标/总检建议/检查汇总 等变体, 无需逐家枚举。
_FINDINGS_ANCHOR_RE = re.compile(
    r"^(?:体检|健康|本次体检|本次|医生|医师|总检|主检|结果|检查|临床|异常|终审|超声|B超|"
    r"结论|建议|分析|汇总|发现|指导建议|指导意见|提示|指标|与|及|、|以下|是|您|的|主要|部分|综述)+$"
)
# 2026-08-28: 强词补"总结/综述"(防城港"本次体检总结:"、崇左"体检结果综述:")
_FINDINGS_STRONG_RE = re.compile(r"(建议|结论|汇总|发现|指导|分析|终审|指标|总结|综述)")
# 2026-09-03: ANCHOR 词表补"综述" —— H004 桂林"体检综述"标题整行为锚点
# (其下 12 个编号小结才是真异常); 原 STRONG 含"综述"而 ANCHOR 词表没有,
# "体检综述" 整行不命中 → 结论段被后置的"健康指导建议"(纯科普)顶替 → 桂林全丢。
# 2026-08-28: "【血脂异常】"与"【1】胸部:微小结节"(编号标题)都是结论段锚点;
# 【】内含编号+标题(胡鹏/庞海锋/钦州)同样识别。
# 2026-08-31: 【】锚点须含发现特征词 —— "【内科】:未见异常""【全血细胞计数+五分类(静脉血)】"
# 等检查项名不是发现锚点(崇左报告被它们顶替, 混入检查清单噪声)。
_FINDINGS_BRACKET_RE = re.compile(r"^【[^】]{1,14}】")


def _is_findings_anchor(ln: str) -> bool:
    # 2026-09-04: 分散排版标题("医 生 建 议："/"检 查 综 述：", 茂名人民)
    # 压缩行内空白后按词表匹配(仅影响判定, 不改收集行文本)
    s = re.sub(r"\s+", "", ln.strip())
    if _FINDINGS_BRACKET_RE.match(s):
        # 【】锚点: 行去【】标题后的剩余内容须含发现特征词
        rest = re.sub(r"^【[^】]{1,14}】\s*", "", s)
        rest = re.sub(r"^\d+[\u3001,.:.\uff09)]?\s*", "", rest)
        # 2026-09-03: "【N】 xxx"数字编号行(防城港中/钦州第二总检条目)即使
        # 标题无特征词(如"【4】肌酸激酶(CK)升高")也是条目锚点 —— 否则目录
        # 摘要行(【1】…【16】纯标题)与详情标题混在段落内被当正文收走。
        if re.match(r"^【\d{1,3}】", s):
            return True
        return bool(_FINDING_TITLE_RE.search(rest))
    # 2026-08-28: 标题+内容同行(徐伟祥"总检建议与结论:尿酸偏高")—— 行首模式词+冒号
    # 2026-08-31: 剥冒号后整行按 ANCHOR 词表匹配(词表与 ANCHOR 一致,
    # 覆盖"以下是您本次异常结果的主要部分汇总：")
    if _FINDINGS_STRONG_RE.search(s) and _FINDINGS_ANCHOR_RE.match(s.rstrip("：:。；; ")):
        return True
    return bool(_FINDINGS_ANCHOR_RE.search(s) and _FINDINGS_STRONG_RE.search(s))
# 页脚广告/服务词: 遇之停止收集(该行及之后丢弃)
# 2026-08-31: 分为 BREAK(段尾签名/广告, 该行及之后丢弃)与 SKIP(页脚页眉,
# 仅跳过该行, 内容可能跨页继续 —— 百色报告"您的健康是我们最大的心愿"页脚
# 后还有 3/4 条异常, 原 BREAK 导致丢失)。
# 2026-09-03: "---\s*Page|Page x/y" 从 BREAK 移到 SKIP —— 页分隔/页码只跳行,
# 结论段跨页时内容须继续(柳州"…如有明显症状[页分隔]请到眼科验光矫正。"
# 曾被 BREAK 截断, 生产 _extract_pdf_text 带 "--- Page N ---" 标记)。
_FINDINGS_STOP_BREAK_RE = re.compile(
    r"(主检医生|主检医师|总检医生|总检医师|审核医生|审核医师|录入者|"
    r"初审医生|初审医师|初审日期|终审医生|终审医师|终审日期|主审医生|主审日期|"
    r"报告日期|总检日期|"
    r"扫描参数|影像所见|诊断意见|"
    r"检查科室)"
)
# 2026-09-03: 建议段后的签名/分检报告起点行 —— 庞海锋(钦州第二)建议段后跟
# "初检:/吴净瑛 2026-08-14 总检:/主检:" 签名与"体 格 检 查/检 验 报 告"
# 分检报告, 无断点会整段混入结论。整行命中即断(仅作用于当前锚点段)。
_FINDINGS_STOP_BREAK_RE3 = re.compile(
    r"^\s*初检[:：]?|^\s*主检[:：]|^\s*初审[:：]|^\s*终审[:：]|^\s*复检[:：]|"
    r"^\s*体\s*格\s*检\s*查\s*$|^\s*检\s*验\s*报\s*告|^\s*分科检查报告\s*$|"
    r"综合上述体检结果|体检小贴士|对检查结果有异议"
)
# 2026-09-03: 体检结论汇总(梧州/H004)只有各科罗列(身高体重/各科"未见异常")+
# 重复指标值, 真异常在其后的"体检结论分析"编号段; 汇总段喂 LLM 会提出
# "身高/内科/外科" 等垃圾条目。整行命中即断(该段丢弃, 后续锚点段不受影响)。
_FINDINGS_STOP_BREAK_RE2 = re.compile(r"^体检结论汇总$|^检查结果汇总$")
_FINDINGS_STOP_SKIP_RE = re.compile(
    r"(健康热线|咨询电话|扫码|问医生|图文咨询|臻心为您|仅供参考|保健参考|"
    r"此报告仅作保健参考|本体检报告仅供临床参考|此报告仅供健康检查|"
    r"您的健康|欢迎您来我院|体检报告送达温馨提示|温馨提示|关爱健康|从体检开始|"
    r"请关注您|祝您健康|呵护健康|请您仔细阅读体检报告|总检结论和健康建议仅建立在|"
    r"向专家咨询|相关检查和监测|没有潜在健康隐患|若出现身体不适|"
    r"---\s*Page|Page\s*\d+\s*/\s*\d+|共\s*\d+\s*页|"
    r"第\s*\d+\s*/\s*\d+\s*页|第\s*\d+\s*页|^\d{1,4}\s*/\s*\d{1,4}$|^\d{1,4}\s*页$|"
    r"体检编号[:：]|体检号[:：]|流水号[:：]|健康档案号[:：]|姓名[:：]|性别[:：]|年龄[:：]|单位[:：]|体检号码[:：]|"
    r"体检日期[:：]|检查日期[:：]|报告日期[:：]|打印日期[:：]|体检次数[:：]|次数[:：]|门诊号[:：]|"
    r"地址[:：]|邮编[:：]|传真[:：]|接收日期[:：]|报告时间[:：]|"
    # 2026-09-10: 无冒号人员页眉(滨州"董延广·男·30   T114205"跨页混入结论段)
    r"^\s*[\u4e00-\u9fa5]{2,8}\s*[·•・]\s*[男女]\s*[·•・]\s*\d{1,3}\s*(?:[A-Za-z]{1,3}\d{3,12})?\s*$)"
)
# 2026-09-03: 行尾页眉/广告片段剥离(崇左"…脂肪肝声像。崇左市人民医院请关注您与
# 家人的健康" —— 页脚与内容同 PDF 行/相邻行)。collect 后/reflow 前逐行剥除:
#  ①"医院名+祝福语"整串; ②句号后孤立的行尾机构名("。崇左市人民医院")。
_FINDINGS_LINE_AD_TAIL_RE = re.compile(
    r"(?:[\u4e00-\u9fa5A-Za-z]{2,20}?(?:医院|体检中心|健康管理中心))?"
    r"(?:请关注您|祝您健康|您的健康|呵护健康|关爱健康|从体检开始|欢迎您|为您的健康)[^。；;]*$|"
    r"(?<=[。；;])[\u4e00-\u9fa5]{2,16}?(?:人民医院|中医院|中心医院|中医医院|医院|体检中心|健康管理中心)$"
)
# 2026-09-03: 逐行页眉(柳州等每页顶部: 机构抬头/号码+姓名/性别男/年龄41岁单位/
# 单位名/体检号 —— fitz 文本流拆成多行且无冒号, 原 SKIP 匹配不到)。
_FINDINGS_STOP_SKIP_RE3 = re.compile(
    r"^\s*[^。；;，,:：]{2,30}?(?:健康)?体检报告\s*$|"
    r"^\s*[A-Za-z0-9]{6,16}\s*姓名|^\s*姓名\s*\S{1,12}\s*$|"
    r"^\s*性别\s*[男女]\s*$|^\s*年龄\s*\d+\s*岁|^\s*(?:岁|年龄)\s*$|"
    r"^\s*(?:体检编号|体检号|住院号|门诊号|病历号|流水号)\s*$|"
    r"^\s*[\u4e00-\u9fa5]{2,14}?(?:出入境边防检查站|边防站|检查站|海关|支队|大队|"
    r"学院|大学|学校|中学|集团|公司|人民武装部)\s*$"
)
# 2026-09-03: 段内页眉整行跳过(庞海锋建议段中夹"钦州市第二人民医院
# 健康管理中心 / 0777-2873333 / 607300555"三行)。"健康管理中心"仅作
# 机构抬头整行匹配 —— 正文"请到消化内科(健康管理中心专家门诊" 不是整行。
_FINDINGS_STOP_SKIP_RE2 = re.compile(
    r"^\s*[\u4e00-\u9fa5A-Za-z]{2,24}?(?:健康管理中心|体检中心)\s*$|"
    r"^\s*\d{3,4}[-—]?\d{6,8}\s*$|^\s*\d{1,10}\s*$"
)
# 2026-09-03: VLM/OCR 产出的 HTML 表格行(<table>/<tr>) 与图片标签整行跳过,
# 表格数值不是发现段内容(钦州中图片报告 OCR 混入各科表格)。
_FINDINGS_HTML_RE = re.compile(r"^<(?:table|tr|td|div|img|p|br)")
# 小结标题行(汉字序号"一、【血压…】"/数字"1.【彩超…】")是发现段正常结构;
# 判定 EXAM_DETAIL_START 前先豁免, 且 skip_detail 中遇到小结标题即恢复。
_SUMMARY_TITLE_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]{1,3}|[0-9]{1,3})\s*[\u3001,.:.．、)\uff09]?\s*【"
)
# 检查细节子段落起始(【检查所见】/【印 象】/签名/彩超室等): 段内跳过
# 到下一个"编号条目行"(如"9:CT ...")为止 —— 广西人民报告 8 条后夹着整段
# 超声/心电图检查报告, 9-13 条因此丢失。
_EXAM_DETAIL_START_RE = re.compile(
    r"^【\s*(?:检查所见|印\s*象|影像所见|超声提示)\s*】|"
    r"^(?:医生|医师|检查者|操作者|技师|初评医生|主检医师)\s*[:：]|"
    r"^(?:彩超室|心电图室|超声科|放射科|病理诊断|无痛胃镜|无痛肠镜|胃镜报告|肠镜报告|"
    r"检查结论|病理报告|影\s*像\s*报\s*告|检查部位)[\s:：]?|"
    r"全国HR|^大便常规(?:[\s（(]|$)|^血常规$|^尿常规[+＋]|^隐血试验$|^尿沉渣|"
    r"^(?:上皮细胞|白细胞团|管型|酵母菌|小圆上皮|蜡样管型|草酸钙结晶|颗粒管型|透明管型|滴虫|精子|尿酸结晶)|"
    r"^注[:：]|制片|染色检测|"
    r"^(?:颈动脉|甲状腺|腹部|心脏|泌尿系|肝胆胰脾)彩超|超声测值|检查所见[:：]"
)
_NUMBERED_LINE_RE = re.compile(r"^\d{1,3}[\u3001,.:.\uff09)]\s*(?:[\u4e00-\u9fa5*＊★【]|[A-Za-z])")
_NORMAL_ONLY_RE = re.compile(r"未见(明显)?(异常|分流|液性|暗区|肿块|占位|出血|钙化|囊肿|结节|结石)|无异常(发现)?|未见异常回声|阴性")
# 签名行: 层2 回溯锚点的起点
_FINDINGS_SIGN_RE = re.compile(
    r"^(主检医生|总检医生|主检医师|总检医师|检查医生|审核医生|报告医师|医师签名|主检:)"
)


def _locate_findings_sections(text: str, extra_break_re=None,
                              extra_skip_re=None, extra_anchor_re=None,
                              anchor_only: bool = False) -> Optional[str]:
    """从全文定位"发现段": 收集所有锚点段(锚点行到下一锚点/广告词), 页眉去重, 限长。未命中 None。

    层1: 标题模式正则(整行由医学词+结论词组成且含强词);
    层2(2026-08-28): 签名行回溯 —— 标题模式未命中时, 定位主检/总检医生签名行,
      向上回溯找"短标题行"(≤15字、非句尾、含强词)作为锚点。
    extra_break_re/extra_skip_re/extra_anchor_re: 医院档案(report_profiles)
    追加的段断点/页眉行/结论段标题行。
    """
    lines = [re.sub(r"^\s*#{1,6}\s*", "", ln) for ln in text.splitlines()]
    # 2026-09-03: 锚点判定对行首 markdown(#) / 列表符(-/•)不敏感 ——
    # VLM/OCR 产物(钦州中"## 本次体检结论及健康指导意见:")也命中。
    def _norm_anchor(ln: str) -> str:
        return re.sub(r"^[-*•·\s]+", "", ln.strip())

    # 2026-09-08: anchor_only(医院档案声明) —— 只取档案专用锚点段
    # (21 华西"健康指导"/24 日照"医学建议"/26 莆田"体检结论分析"/27 齐鲁
    # "您本次体检的建议如下"/22 弘爱"三、体检异常结果及医学建议"), 不混入
    # 通用锚点段(华西"体检综述"全科室罗列、莆田"体检结论汇总"多提问题)。
    idxs = [i for i, ln in enumerate(lines)
            if extra_anchor_re and extra_anchor_re.search(_norm_anchor(ln))]
    if not anchor_only:
        idxs = [i for i, ln in enumerate(lines)
                if _is_findings_anchor(_norm_anchor(ln))] + idxs
        idxs = sorted(set(idxs))
    if not idxs:
        for i, ln in enumerate(lines):
            if _FINDINGS_SIGN_RE.match(ln.strip()):
                for k in range(1, 10):
                    j = i - k
                    if j < 0:
                        break
                    cand = lines[j].strip()
                    if not cand or len(cand) > 15:
                        continue
                    if re.search(r"[。.!！?？:：；、]$", cand) or re.match(r"^\d", cand):
                        continue
                    if _FINDINGS_STRONG_RE.search(cand):
                        idxs = [j]
                        break
                if idxs:
                    break
    if not idxs:
        return None
    line_counts: dict = {}
    for _ln in lines:
        _k = re.sub(r"^\s*#{1,6}\s*", "", _ln).strip()
        line_counts[_k] = line_counts.get(_k, 0) + 1
    out: list[str] = []
    skip_detail = False
    for i in range(len(idxs)):
        start = idxs[i]
        end = idxs[i + 1] if i + 1 < len(idxs) else len(lines)
        seg_mark = len(out)
        seg_anchor = re.sub(r"^\s*#{1,6}\s*", "", lines[start]).strip()
        # 2026-09-12: 锚点前紧邻编号条目并入(仅第一段)——福建第二"2. 彩超提示
        # 甲状腺实质回声稍增粗…"印在"体检结论分析"标题**之前**, 被整段排除
        # (用户验收"结论段少了第二点")。只吞紧邻连续的"N. 名称(含特征/方向)"
        # 行, 最多 5 行, 遇其它内容立即停止。
        pre: list[str] = []
        if start > 0:
            _k = start - 1
            while _k >= 0 and len(pre) < 5:
                _cand = re.sub(r"^\s*#{1,6}\s*", "", lines[_k]).strip()
                if not _cand:
                    _k -= 1
                    continue
                _m = re.match(r"^\d+[.、]\s*(.+)$", _cand)
                if _m and (_FINDING_TITLE_RE.search(_m.group(1))
                           or re.search(r"(偏高|偏低|升高|降低|增高|减少|增多|阳性|异常)", _m.group(1))):
                    pre.insert(0, _cand)
                    _k -= 1
                    continue
                break
        seen: set = set()  # 2026-09-03: 去重按段隔离 —— 贵港"异常指标"表与
        # "健康建议"段标题行文本相同, 全局去重会把健康建议条目标题全吞
        for ln in pre + lines[start:end]:
            s = re.sub(r"^\s*#{1,6}\s*", "", ln).strip()
            if not s:
                continue
            if _FINDINGS_STOP_BREAK_RE.search(s):
                break
            if _FINDINGS_STOP_BREAK_RE2.match(s):
                break
            if _FINDINGS_STOP_BREAK_RE3.match(s):
                break
            if extra_break_re and extra_break_re.search(s):
                break  # 医院档案追加的段尾断点
            # 2026-08-31: 页脚页眉(广告/页码/体检号)仅跳过该行, 内容可跨页继续
            if _FINDINGS_STOP_SKIP_RE.search(s):
                continue
            # 2026-09-03: 段内页眉(健康管理中心抬头/电话/号码)整行跳过
            if _FINDINGS_STOP_SKIP_RE2.search(s):
                continue
            # 2026-09-03: 逐行页眉(柳州等: 机构抬头/号码+姓名/性别男/年龄/体检号)
            if _FINDINGS_STOP_SKIP_RE3.search(s):
                continue
            if extra_skip_re and extra_skip_re.search(s):
                continue  # 医院档案追加的页眉/装饰行
            # 2026-09-03: VLM/OCR HTML 表格/图片标签行整行跳过
            if _FINDINGS_HTML_RE.match(s):
                continue
            # 小结标题行(汉字序号"五、【甲状腺彩超】")是发现段正常结构:
            # 不是细节段起点, 且细节段内遇到它即恢复收集。
            if _SUMMARY_TITLE_RE.match(s):
                skip_detail = False
            # 2026-08-31: 检查细节子段落(超声/心电报告)跳过, 遇编号条目恢复
            # 2026-09-03: "九、超声科:" 等汉字序号小节行(莆田结论分析内分科分组)
            # 不是细节段起点, 不触发跳过
            elif re.match(r"^[一二三四五六七八九十]{1,3}\s*[、.．]", s):
                skip_detail = False
            elif _NUMBERED_LINE_RE.match(s) and not ln[:1].isspace():
                skip_detail = False  # 2026-09-08: 编号条目行("3. 放射科(CT)提示…")
                # 是结论条目本身, 不是检查细节段起点(福建第二 3/5 点曾被误跳)
            elif _EXAM_DETAIL_START_RE.search(s):
                skip_detail = True
                continue
            if skip_detail:
                # 2026-09-03: 恢复仅限"顶格编号行"(无前导空格)—— 分检报告
                # (无痛胃镜"检查结论:…2.内痔"缩进行)是细节段内容, 不得恢复。
                if (_NUMBERED_LINE_RE.match(s)
                        and not ln[:1].isspace()):
                    skip_detail = False
                else:
                    continue
            # 2026-08-31: 检查清单"未见异常"行跳过(崇左报告结论段混入噪声)
            if len(s) <= 40 and _NORMAL_ONLY_RE.search(s):
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        # 2026-09-03: 空锚点段丢弃 —— 段内只有标题行本身(柳州"异常体检结果汇总:"
        # 是汇总表标题, 其列表在文本流中位于标题前、未收集)时, 不显示空标题。
        # 例外: "【N】…"摘要行(钦州第二【1】-【6】快览)若全文唯一(详情标题
        # 文本不同)应保留; 仅在全文重复(防城港中目录与详情标题同文本)时删除。
        if len(out) == seg_mark + 1 and out[seg_mark] == seg_anchor:
            if re.match(r"^【\d{1,3}】", seg_anchor) and line_counts.get(seg_anchor, 0) <= 1:
                pass  # 唯一【N】摘要行保留
            else:
                out.pop()
        if sum(len(x) for x in out) > 12000:
            break
    return "\n".join(out) if out else None


def _reflow_conclusion_lines(section: str) -> str:
    """展示规整: 消除 PDF 行内折行, 保留条目标题行结构。

    段头行(【】标题/阿拉伯序号/星号圆点行)与小结标题(汉字序号"五、【甲状腺彩超】")
    独立成段; 编号段头后的正文行折叠拼接进当前段(消除"…可单/发或多发"句中断行);
    小结标题后的内容行保持逐行独立(桂林"肝内钙化点/肝囊肿"是分行条目)。
    仅用于展示/存储, 不参与提取。
    """
    summary_head = re.compile(r"^[一二三四五六七八九十]{1,3}\s*[\u3001,.:.．、]?\s*【")
    # 2026-09-03: OCR/文本里"数字+【"(钦州中"1. 【窦性心律不齐…】")同小结标题处理
    title_head = re.compile(r"^\d{1,3}\s*[、.．:：]?\s*【")
    item_head = re.compile(
        r"^【|^[*＊★▲△●○■]|^\d{1,3}\s*[\u3001,.:.．\uff09)]?\s*[\u4e00-\u9fa5A-Za-zα-ωΑ-Ωγ(（*＊★▲△\[]"
    )
    # 建议段条目标题行(【】/星号行)后跟解释句("…可见于/可能与/见于…")或
    # "(1)"子条编号时独立起段, 避免"…隐血弱阳性大便隐血弱阳性可见于…"黏连。
    advice_start = re.compile(
        r"^(?:见于|可见于|多见于|可能与|可能为|可能是|可能由|是指|常见|可导致|"
        r"日常|建议|请|考虑|属于|多饮水|若)"
    )
    subhead_re = re.compile(r"^[（(]\s*\d+\s*[）)]")
    out: list[str] = []
    buf: list[str] = []
    prev: str = ""

    def flush():
        if buf:
            out.append("".join(buf))
            buf.clear()

    for ln in section.splitlines():
        s = _FINDINGS_LINE_AD_TAIL_RE.sub("", ln).strip()
        if not s or re.fullmatch(r"[-—－–·•]{2,}", s):
            flush()
            prev = ""
            continue
        # 2026-09-10: 池州体检综述标题编号带空格("1 1 、【…】"跨页区 11-14)——
        # 数字内空格只影响标题结构判定, 展示文本保持原样。
        s_judge = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
        if summary_head.match(s_judge) or title_head.match(s_judge):
            flush()
            out.append(s)  # 小结标题行独立(其下内容行逐行独立)
            prev = "summary"
            continue
        # 2026-09-08: 无编号小节标题("科普说明" 德宏总检建议尾的科普区标题)独立成行
        if re.fullmatch(r"(科普说明|健康科普|科普知识|温馨提示|健康教育|注意事项)", s):
            flush()
            out.append(s)
            prev = "summary"
            continue
        if item_head.match(s_judge):
            flush()
            buf = [s]  # 编号/【】段头开段, 后续正文行折行拼入
            prev = "open"
            continue
        # 普通正文行
        if re.match(r"^[^。！？;；]{1,40}[:：]$", s):
            flush()
            # 提示行("针对您本次体检结果,建议如下:")/正文引导句("…原因有:")
            # 作为段首开新段, 后续折行续行拼回(不再单独输出, 防重复)
            buf = [s]
            prev = "open"
            continue
        # 2026-09-03: 短段标题(≤10字、含结论强词: "终审建议/健康指导建议/
        # 本次体检总结" 等)独立成行 —— 否则被拼进上一条正文末尾(贵港
        # "…减重门诊就诊。终审建议" 黏连)。
        if len(s) <= 10 and _FINDINGS_STRONG_RE.search(s):
            flush()
            buf = [s]
            prev = "open"
            continue
        if prev == "summary":
            out.append(s)  # 小结标题下的内容行逐行独立(分行条目)
            continue
        if prev == "open":
            is_title_row = len(buf) == 1 and buf[0].startswith(("【", "*", "＊", "●", "○", "■"))
            # 2026-09-03: 【N】数字编号标题 / 圆点(●)标题后正文首行恒独立成段
            # (防城港中/第一模板: "【4】 肌酸激酶(CK)升高" 下正文直接起句,
            # 无"可见于/建议"等触发词时旧规则黏连)。标题行短, 其后行必是新段。
            num_bullet_title = len(buf) == 1 and (
                re.match(r"^【\d{1,3}】", buf[0])
                or buf[0].startswith(("●", "○", "■")))
            # 编号标题行以"?"结尾("7.右肾肾盂旁囊肿?")时, 正文另行起段, 避免黏连
            question_title = len(buf) == 1 and re.search(r"[?？]$", buf[0])
            # 短编号标题行(柳州"2.轻度肥胖""5.血尿酸升高""4.血脂异常(总胆固醇升高…)"
            # —— 去分级括号后仍短、无句末标点)后正文是独立段落而非折行续行 → 拆行。
            title_probe = re.sub(r"[（(][^（()）]*[)）]", "", buf[0]).strip() if buf else ""
            short_title = (len(buf) == 1 and re.search(r"^\d{1,3}[\u3001,.:.．\uff09)]\s*\S", buf[0])
                           and len(title_probe) <= 16 and not re.search(r"[。！？;；:：]$", buf[0]))
            if num_bullet_title and not subhead_re.match(s):
                flush()
                buf = [s]
            # 2026-09-08: 编号主条目行以冒号结尾("5. …双髋关节骨质密度减少:")时,
            # 后续科普解释/建议行独立成段 —— 异常结论与解释黏连会让 LLM 把解释句
            # 拆成碎片条目(福建第二 骨密度 提取成"骨量减少/生物力学性能下降")。
            elif (len(buf) == 1 and re.search(r"[:：]$", buf[0])
                  and re.match(r"^\d{1,3}[\u3001,.:.．\uff09)]", buf[0])
                  and not subhead_re.match(s)):
                flush()
                buf = [s]
            elif short_title and not subhead_re.match(s):
                flush()
                buf = [s]  # 标题与解释段换行分隔(不插空行)
            elif (is_title_row or short_title) and subhead_re.match(s):
                flush()
                buf = [s]  # 标题与(1)子条换行分隔
            elif buf[0].startswith(("(", "（")) and subhead_re.match(s):
                flush()
                buf = [s]  # (1)(2)… 子条逐条独立
            elif is_title_row and re.search(
                    r"可见于|多见于|可能与|可能是|是指|见于|提示|考虑|建议|请|可能为", s[:20]):
                flush()
                buf = [s]  # 标题行后解释句独立成段
            elif question_title:
                flush()
                buf = [s]  # "?"标题后正文独立(梧州"7.右肾肾盂旁囊肿?")
            else:
                buf.append(s)  # 段头后的正文折行拼接
            continue
        out.append(s)  # 孤立正文行
    flush()
    # 2026-09-03: 段内 (1)(2)… 子条切分为独立行 —— PDF 常把"…(1)…(2)…"排同
    # 一行(崇左"【血脂四项】:(1)低密度脂蛋白增高4.73mmol/L (2)*高密度…"),
    # 拼接后按 (n) 完整括号切行(防"（门诊4楼）""(2026年)"等非子条)。
    rendered: list[str] = []
    for seg in out:
        for part in re.split(r"(?=[（(]\d{1,2}[）)])", seg):
            part = part.strip()
            if part:
                rendered.append(part)
    return "\n".join(rendered)


def _drop_review_block(text: str) -> str:
    """剥除段文本中段的"检 查 综 述"★枚举页(茂名人民版式, 医生建议区中夹
    综述页)。

    综述页特征: ①标题行"检 查 综 述:"; ②其下每行"★ 检查名:结果" ——
    冒号前为纯检查名(体格检查/彩色B超/超声/心电图/碳13 尿素呼气试验/肾功/心脑),
    不含方向/阳性/发现特征词(建议区的"★ 碳13尿素呼气试验阳性:"标题含"阳性"不删);
    ③编号续行((1)(2)…)与综述行连排。块止于下一个★ 病名标题行(冒号前含发现词,
    如"★ 电轴右偏:")。
    """
    lines = text.split("\n")
    out: list[str] = []
    skip = False
    _exam_tail_re = re.compile(
        r"(体格检查|血压测量|彩色?B?\s*超|超声|B\s*超|X\s*线|X\s*光|CT|心电图|脑电图|"
        r"碳13|肾功|肝功|心脑|胃镜|肠镜|试验|检查|测定)"
        r"(?:[（(][^）)]*[)）])?$")
    _found_word_re = re.compile(
        r"(偏高|偏低|升高|降低|增高|增多|减少|偏大|偏小|阳性|阴性|异常|正常|"
        r"结节|结石|囊肿|增生|肥大|反流|钙化|息肉|脂肪|肿瘤|狭窄|增厚|斑块|肌瘤|"
        r"超重|肥胖|动脉硬化|心律不齐|血症|沉着|感染|曲张|返流)")
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(ln)
            continue
        if not skip and re.match(r"^检\s*查\s*综\s*述[:：]?", s):
            skip = True
            continue
        if not skip:
            out.append(ln)
            continue
        m = re.match(r"^[*＊★]\s*(.*)$", s)
        if m:
            head0 = re.split(r"[：:]", m.group(1), maxsplit=1)[0]
            head0 = re.sub(r"[（(][^）)]*[)）]", "", head0).strip()
            if _exam_tail_re.search(head0) and not _found_word_re.search(head0):
                continue  # 综述枚举行(★ 纯检查名:结果)
            skip = False  # ★ 病名标题行 → 综述块结束, 恢复保留
            out.append(ln)
            continue
        if re.match(r"^[（(]?\s*\d+\s*[）)]?[\u3001、.．:]?\s*", s) or s.startswith((
                "建议", "请", "必要时", "提示")):
            continue  # 综述块的编号续行/建议尾(如"心脑 (1)(2)")随块丢弃
        skip = False  # 其它文本(段尾残余)恢复保留
        out.append(ln)
    return "\n".join(out)


async def _extract_conclusion_async(text: str, profile: Optional[dict] = None) -> Optional[str]:
    """提取报告结论/发现段。

    方案4(2026-08-24): 确定性锚点切段优先(免一次 LLM 调用); 锚点未命中
    才回退 LLM 找段落(no_think, 提取类任务禁用思考)。
    profile: 医院档案(report_profiles), 提供 extra_break/extra_skip 追加规则。
    """
    compiled = _compile_profile_re(profile)
    section = _locate_findings_sections(
        text, extra_break_re=compiled["extra_break_re"], extra_skip_re=compiled["extra_skip_re"],
        extra_anchor_re=compiled["extra_anchor_re"],
        anchor_only=bool(compiled.get("anchor_only")))
    if section and len(section) > 50:
        if profile and profile.get("table_conclusion"):
            # 2026-09-08: 表格型结论(弘爱)先重组(序号行独立), 不经过 reflow
            # (reflow 会把块尾序号行拼进上一段, 导致无法按行分块)
            return _reflow_table_conclusion(section)[:16000]
        section = _reflow_conclusion_lines(section)
        # 2026-09-09: 中段剥离"检 查 综 述"★枚举页(茂名 25 —— 该页插在医生建议
        # 区中间, 条目与建议区重复, 且"前列腺增大并局部钙化"等组合名会污染条目)。
        if profile and profile.get("review_block"):
            section = _drop_review_block(section)
        return section[:16000]
    from app.ai.llm import get_chat_model, _guarded

    prompt = _CONCLUSION_PROMPT.format(text=text[:16000])
    model = get_chat_model(no_think=True)

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=2048)

    try:
        resp = await _guarded(_call())
        conclusion = _clean_conclusion(resp.content)
        if conclusion:
            return conclusion
    except Exception as e:
        _log.warning("Failed to extract conclusion: %s", e)
    return None


_ABNORMALITY_PROMPT = """请从以下体检报告的"总检建议与结论"文本中，逐条提取所有异常发现条目。

【只提取异常发现】
- 只提取"疾病/异常/发现"类条目（如：甲状腺结节、肝功能损害、血脂异常、脂肪肝、龋齿、右肾囊肿）
- 【严禁】把以下内容当作条目：
  * 科普解释、病因说明、健康指导（如"可表现为出血""多见于...""建议定期复查""高密度脂蛋白是...基础物质"都不是异常条目）
  * 检查过程描述、建议性语句、数据对比叙述（如"与2024年数据对比变化不大"）
- 【严禁】从叙述性/定义性句子中提取疾病名词（如"常见于甲状腺炎、甲状腺腺瘤、甲状腺癌等""肾结石为上尿路结石""病情重者可出现肝损伤"所在句子中的疾病名一律不提取）
- 只允许提取以下结构中的明确疾病名：①【】标题（如【甲状腺结节】）②编号条目（如"4、胆囊结节"）③"诊断：""结论："后的短语 ④正文中"部位+明确病名"的独立短句（如"右肾囊肿。""右肾结石。"）
- ⑤星号/强调标题行（如"*超重""*变应性鼻炎"，标题行独立成条）⑥冒号摘要式"名称+方向"短语
  （如"尿酸(UA)偏高，谷丙转氨酶偏高"中的"尿酸(UA)偏高"是一条，方向词=偏高/偏低；"颈部淋巴结?"是可疑发现，算一条）

每条异常对应一个 JSON 对象：
- item_name: 异常发现的名称，**直接取自文本原文**，保留解剖部位（"甲状腺左叶混合性病灶"不能简化为"病灶"），
  去掉测量细节（"大者0.3cm×0.2cm""TI-RADS 3类""0.5cm"）和检查方法名
- deviation: 偏离方向，取值为"偏高""偏低""偏大""偏小""偏重""偏轻""异常"，解析不到则为null
- suggestion: 对应建议原文（保留完整措辞；没有则为空字符串）
- is_urgent: 如果建议/描述中含以下紧急关键词则为true，否则false：
  "立即就医""立即""即刻干预""即刻""尽快就诊""尽快就医""急诊""马上""紧急""随时就医""危险""危急"
  "务必""不容忽视""必须(就医|就诊|干预|检查)""请尽快""速"

注意事项：
- 每一条编号/【】标题对应一个异常条目，不要把多条合并，也不要遗漏
- 只输出 JSON 数组，不要加任何说明或 Markdown 代码块
- 如果没有任何异常项，输出空数组[]

文本：
{text}"""


# 危机字段(确定性兜底, 不依赖 LLM): 命中即判 is_urgent → 红色
_URGENT_RE = re.compile(
    r"(立即就医|立即|即刻干预|即刻|尽快就诊|尽快就医|急诊|马上|紧急|随时就医|"
    r"危险|危急|务必|不容忽视|必须(?:就医|就诊|干预|检查))"
)


def _mark_urgent(items: list[dict]) -> list[dict]:
    for it in items:
        if it.get("is_urgent"):
            continue
        text = f"{it.get('item_name', '')} {it.get('suggestion', '')}"
        if _URGENT_RE.search(text):
            it["is_urgent"] = True
    return items


def _merge_candidate_chain(source: str, item_list: list[dict]) -> list[dict]:
    """问号候选链合并(H004 钦州第二"右肾强光团,钙化灶?结石?" /
    钦州中"左肾内强回声团(钙化灶?)")。"""
    chain_re = re.compile(r"([\u4e00-\u9fa5]{2,8}[?？]){1,3}")
    drop_names: set = set()
    for m in chain_re.finditer(source):
        pre = source[m.start() - 1] if m.start() > 0 else ""
        if not pre or not re.match(r"[\u4e00-\u9fa5，,、（(]", pre):
            continue
        for mem in re.findall(r"([\u4e00-\u9fa5]{2,8})[?？]", m.group(0)):
            drop_names.add(mem)
    if drop_names:
        kept = []
        for it in item_list:
            nm = (it.get("item_name") or "").replace(" ", "")
            if nm in drop_names:
                _log.info("candidate-chain merge drop: %s", nm)
                continue
            kept.append(it)
        return kept
    return item_list


def _fill_symmetric_sides(source: str, item_list: list[dict]) -> list[dict]:
    """左右叶/双侧对称补全(桂林"甲状腺左叶结节/右叶结节"分行并列, LLM 偶只提一叶)。"""
    cleaned_lines = set()
    for ln in source.splitlines():
        c = re.sub(r"[（(][^）)]*RADS[^）)]*[）)]", "", ln.strip()).strip()
        c = c.lstrip("*＊★· ")  # 健康指导段星号标题("*甲状腺左叶结节…")
        if c and len(c) >= 4 and _FINDING_TITLE_RE.search(c):
            cleaned_lines.add(c)
    existing = {(it.get("item_name") or "").replace(" ", "") for it in item_list}
    for c in sorted(cleaned_lines):
        if "左" not in c and "右" not in c:
            continue
        twin = c.replace("左", "右", 1)
        if twin == c or twin not in cleaned_lines:
            continue
        covered_c = any(c in e or e in c for e in existing)
        covered_twin = any(twin in e or e in twin for e in existing)
        if covered_twin and not covered_c:
            _log.info("symmetric-side fill: %s (twin %s)", c, twin)
            item_list.append({"item_name": c, "suggestion": "", "deviation": None,
                              "is_urgent": False, "_safety_net": True})
            existing.add(c)
    return item_list


# 科室/检查项名/症状词垃圾条目 —— 2026-09-04 扩充(茂名症状词/潮州检查项)
_NONABNORM_NAME_RE = re.compile(
    r"^(身高|体重|心率|脉搏|呼吸|体温|血压|收缩压|舒张压|腰围|臀围|"
    r"内科|外科|眼科|五官科|口腔科|耳鼻咽喉科|耳鼻喉科|妇科|男科|检验科|"
    r"放射科|超声科|彩超室|心电图室|病理科|一般检查|肛检|舌象|脉象|"
    r"矫正视力(?:\(左\)|\(右\)|（左）|（右）)?|既往史|家族史|现病史|婚育史|过敏史|"
    r"体检|总检|报告|科室|项目|"
    r"窦性心律|"
    r"血常规|尿常规|便常规|大便常规|粪便常规|"
    r"尿频|尿急|尿痛|尿不尽|排尿不畅|排尿困难|夜尿增多|血尿|蛋白尿|"
    r"咳嗽|咳痰|头晕|头痛|胸闷|心悸|乏力|恶心|呕吐|腹泻|便秘|腹胀|"
    r"多汗|盗汗|消瘦|口干|口苦|耳鸣|眼干|视物模糊|失眠|"
    r"尿干化学|尿沉渣|尿白细胞|血脂|血糖|肝功|肾功|甲功|血常规分析)$"
)
# 以"项/常规/试验"结尾的检查项名(LLM 把"血脂4项/血常规"提为条目, 潮州)
_NONABNORM_SUFFIX_RE = re.compile(r"(项|常规|试验|功能)$")
# 2026-09-09: 名称以"功能"结尾 = 检查项/正常性短语("甲状腺功能" 齐鲁 27
# 【甲状腺结节；甲状腺功能正常】后半被 LLM 单挖), 真异常以 亢进/减退/异常 结尾
# 2026-09-09: 肺结节分级裸词 —— 无部位限定, 只作为科普分类名出现(日照 24
# "纯磨玻璃样结节/实性结节/混杂性结节"), 独立条目必然带部位(右肺上叶实性小结节)。
_FINDING_GRADE_ONLY = {"纯磨玻璃样结节", "实性结节", "混杂性结节"}


# 2026-09-12: 纯心内结构/大血管部位词单独成条 = LLM 碎名(弘爱"二尖瓣" vs
# 完整"二尖瓣、三尖瓣轻度反流"), 非独立异常。
_ANATOMY_ONLY_RE = re.compile(
    r"^(二尖瓣|三尖瓣|主动脉瓣|肺动脉瓣|主动脉|肺动脉|左心室|右心室|左心房|右心房|"
    r"房间隔|室间隔|瓣膜)$"
)


# 独立症状词表: "尿频/血尿" 等仅在作为主发现一部分时有意义(茂名前列腺增大科普被拆出)
_SYMPTOM_ONLY_RE = re.compile(
    r"^(尿频|尿急|尿痛|尿不尽|排尿不畅|排尿困难|夜尿增多|血尿|蛋白尿|"
    r"咳嗽|咳痰|头晕|头痛|胸闷|心悸|乏力|恶心|呕吐|腹泻|便秘|腹胀|"
    r"多汗|盗汗|消瘦|口干|口苦|耳鸣|眼干|视物模糊|失眠)$"
)


# 2026-09-10: 指标名称前缀参考清洗("0-1个/LP颗粒管型" → "颗粒管型";
# 钦州二尿沉渣表参考列与名称列在文本流中粘连)。
_REF_NAME_PREFIX_RE = re.compile(
    r"^[\d.]+\s*(?:[-~～]\s*[\d.]+)?\s*个?\s*/\s*(?:HP|LP|μL|ul|L)\s*")


def _clean_indicator_name(name: str) -> str:
    if not name:
        return name
    return _REF_NAME_PREFIX_RE.sub("", name).strip() or name


# 2026-09-10: 结论泛称去重(保留报告方原文的限定名):
# ①程度限定("轻度肥胖"在 → 去"肥胖"); ②"肺结节"泛称在存在其它结节条目时去
# (钦州二科普句"肺结节"与真发现"右肺中叶内侧段微小结节"并存)。
_DEGREE_PREFIX = ("轻度", "中度", "重度", "轻", "中", "重", "超", "偏")


_DENTAL_PREFIX_RE = re.compile(
    r"^\d{1,2}\s*(?:牙|齿)?\s*(?=龋|楔|残|根|牙|缺|磨损|隐裂|折裂|变色|氟|四环素)")


_GENERIC_FINDING_WORDS = {
    "结节", "钙化灶", "钙化", "囊肿", "息肉", "结石", "斑块", "占位", "肌瘤",
    "溃疡", "增生", "肥厚", "积液", "反流", "返流", "狭窄", "硬化",
    "糜烂", "萎缩", "肿大", "增大", "病变", "异常", "感染",
}
# 2026-09-11: 同义写法归并(比较键做词级替换, 展示名不变)
_DEDUP_SYNONYMS = (("潜血", "隐血"),)


def _dedup_generic_findings(items: list) -> list:
    names = {(i.get("item_name") or "").strip() for i in items}
    names.discard("")
    out = []
    for it in items:
        n = (it.get("item_name") or "").strip()
        if n and any(o != n and o.endswith(n) and o[:-len(n)] in _DEGREE_PREFIX
                     for o in names):
            continue
        # "肺结节"泛称: 同批存在**同部位(含"肺")**的更精确结节条目(非纯泛词)时
        # 删(钦州二"右肺中叶内侧段微小结节"科普挖词场景); 不能因其它部位结节
        # ("甲状腺结节")误删报告方真标题(马鞍山 25 "[CT 提示：肺结节]")。
        if n == "肺结节" and any(
                o != n and "结节" in o and "肺" in o
                and _write_norm(o) not in _GENERIC_FINDING_WORDS
                for o in names):
            continue
        out.append(it)
    # 2026-09-11: 泛词条目 —— 纯泛词("结节"/"钙化灶"/"钙化")不是独立异常, 无条件删
    # (2026-09-12 收紧: 日照"钙化"/池州"结节"等由 LLM 波动单独产出, 旧"同批存在
    # 更长条目才删"条件在无更长条目时漏网)。
    if any(_write_norm((i.get("item_name") or "")) in _GENERIC_FINDING_WORDS for i in out):
        kept = []
        for it in out:
            bare = _write_norm(it.get("item_name") or "")
            if bare in _GENERIC_FINDING_WORDS:
                _log.info("generic-finding filtered: %s", it.get("item_name"))
                continue
            kept.append(it)
        out = kept
    # 2026-09-11: 同义写法合并("尿潜血(BLD)+1" ≡ "尿隐血") —— 词根(_cmp_norm)做
    # 同义替换后归并, 保留更长(信息更全)者。
    syn_seen: dict = {}
    merged: list = []
    for it in out:
        root = _cmp_norm(it.get("item_name") or "")
        for a, b in _DEDUP_SYNONYMS:
            root = root.replace(a, b)
        if not root:
            merged.append(it)
            continue
        if root in syn_seen:
            prev = syn_seen[root]
            if len(_write_norm(it.get("item_name") or "")) > \
                    len(_write_norm(prev.get("item_name") or "")):
                prev.update(it)
            _log.info("synonym-dup merged: %s", it.get("item_name"))
            continue
        syn_seen[root] = it
        merged.append(it)
    out = merged
    # 2026-09-10: 名称归一 —— ①牙位前缀剥离("47龋齿"→"龋齿");②去空格/尾缀"可能"
    # 后同实体去重("ST 段轻度改变"vs"ST段轻度改变"; "肝内血管瘤"vs"肝内血管瘤可能")
    def _norm_key(x: str) -> str:
        x = re.sub(r"[（(]\s*[+＋\-—]?\s*[)）]$", "", x.strip())
        return re.sub(r"\s|\u3000", "", re.sub(r"(可能|待查)$", "", x))
    all_keys = {_norm_key((i.get("item_name") or "")) for i in out}
    seen = set()
    final = []
    for it in out:
        it = dict(it)
        it["item_name"] = _DENTAL_PREFIX_RE.sub("", it.get("item_name") or "").strip()
        it["item_name"] = re.sub(r"[（(]\s*[+＋]\s*[)）]$", "", it["item_name"]).strip()
        n = it["item_name"]
        # 组合整串(顿号/逗号分隔)且各段已独立成条 → 整串丢弃
        subs = [s_.strip() for s_ in re.split(r"[、,，]", n) if len(s_.strip()) >= 2]
        if len(subs) >= 2 and all(_norm_key(s_) in all_keys for s_ in subs):
            continue
        k = _norm_key(n)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        final.append(it)
    return final


# === 2026-09-10: 多方向行规则候选(结构收权) ===
# 一行枚举多条异常(池州"尿潜血(BLD)+1 尿比重偏高 酸碱度偏低 维生素C弱阳性
# 红细胞计数偏高"/"间接胆红素偏高 载脂蛋白E偏低 脂蛋白(a)偏高")由 LLM 提取时
# 每轮输出不稳(漏项/碎词), 规则按方向词切分确定性补齐候选, 与 LLM 结果在 fill
# 层做覆盖去重。仅处理含 ≥2 个方向词的正文行(单方向行 LLM 稳定且已有标题 fill)。
_DIRECTION_PHRASE_RE = re.compile(
    r"(弱阳性|阳性|增高|升高|偏高|降低|下降|偏低|偏大|偏小|偏重|偏轻|"
    r"增大|肥大|异常|增多|减少|右偏|左偏|[+＋]{1,3}\d?)")
_CAND_JUNK_RE = re.compile(
    r"(建议|请|应|需|注意|定期|复查|随访|随诊|就诊|治疗|预防|"
    r"饮食|运动|日常|保健|保持|避免|门诊|指导|控制|您)")
_EDGE_TRIM = " \u3000,，、;；。:：·-–—<>《》"  # 不含括号/方括号: 名内成对括号("脂蛋白(a)")不可拆


def _normalize_disc(name: str) -> str:
    """椎间盘名归一(与 filter expanded 同口径): 剥节段/方位前缀, 方向词保留。"""
    flat = _write_norm(name)
    if "椎间盘" not in flat:
        return name
    m = re.search(r"椎间盘(?:向后|向前|向侧|向后方|向侧方|中央|旁中央|外侧)?"
                  r"(膨出|突出|脱出)", flat)
    if m:
        return "椎间盘" + m.group(1)
    return flat[flat.find("椎间盘"):]


def _strip_direction(s: str) -> str:
    """剥方向词(用于候选/覆盖的词根比较)。"""
    return _DIRECTION_PHRASE_RE.sub("", _write_norm(s))


def _split_tail_obesity(name: str) -> list:
    """相邻标题连体拆分: "脂肪肝超重" → ["脂肪肝", "超重"](崇左【脂肪肝】【超重】)。

    仅当前缀含发现特征词时拆("向心性肥胖"等真名保持整条); 否则原样返回。
    reshape(LLM 名)与 fill(标题兜底)共用, 防一侧拆一侧补回。
    """
    nm = _write_norm(name)
    m = re.match(r"^(.{2,}?)(超重|肥胖)$", nm) if nm else None
    if m and _FINDING_TITLE_RE.search(m.group(1)):
        return [m.group(1), m.group(2)]
    return [name]


_CAND_GUIDE_RE = re.compile(
    r"(以下|按照|列出|如下|本次体检|诊断标准|诊断、|分级|等级|可分为|分为|包括|"
    r"定义|解释说明|指导建议|所发现的问题|"
    # 2026-09-12: 科普/建议句连接词(柳州"喝茶也可使血脂水平下降"被切出成条)
    r"可使|也可|还可|是一种|者到|但是|因此|特别|不主张|通过|如果|若)")


def _parse_direction_phrases(text: str) -> list:
    """多方向行 → (名+方向) 候选列表(秒级纯函数, 单测覆盖)。"""
    out: list[str] = []
    for raw in (text or "").split("\n"):
        s = _write_norm(raw.strip())
        if not s:
            continue
        # 版块装饰头(滨州"▍异常指标解读以下按照…")/序号圈头(柳州"①血脂异常…")/
        # 结构化标题行不在本层
        if s[0] in "▍▎■◆▶●①②③④⑤⑥⑦⑧⑨⑩":
            continue
        # 2026-09-12: 枚举行只含短短语与分隔(空格/顿号/逗号); 含句号/分号 = 连续
        # 叙述/科普句(柳州"运动可使…下降。喝茶也可使…"曾整句切出成条) → 不产
        if re.search(r"[。！？;；]", s):
            continue
        if re.match(r"^(\d+[\u3001,.:]|【|[●○■★*＊]|[（(]\s*\d+\s*[）)])", s):
            continue
        marks = list(_DIRECTION_PHRASE_RE.finditer(s))
        if len(marks) < 2:
            continue
        for i, m in enumerate(marks):
            start = marks[i - 1].end() if i > 0 else 0
            stem = s[start:m.start()].strip(_EDGE_TRIM)
            stem = re.sub(r"^\d+[、.)）]?", "", stem).strip(_EDGE_TRIM)
            if not (2 <= len(stem) <= 22):
                continue
            if not re.search(r"[\u4e00-\u9fff]", stem):
                continue
            if _CAND_JUNK_RE.search(stem) or _CAND_GUIDE_RE.search(stem):
                continue
            cand = stem + m.group()
            if cand not in out:
                out.append(cand)
    return out


# 2026-09-12: 编号/方法行多发现候选(广西人民验收: "1:胃镜 慢性萎缩性胃炎(C1)
# 十二指肠球部溃疡(S2 期)…" 与 "6:心电图 左心室高电压 心室早期复极 T 波改变…"
# —— LLM 只提行内首个/已知段, 其余漏掉)。触发条件保守: 行内至少一段已被现有条目
# 覆盖(词根), 才把该行其它有效段补为候选(结论段本身即异常汇总, 误产风险低)。
_NUM_METHOD_RE = re.compile(
    r"^\d+\s*[:：.、]\s*(?:胃镜|肠镜|心电图|CT|彩超|B超|超声|X\s*线|DR|MRI|核磁|"
    r"脑电图|肌电图|肺功能|骨密度|人体成分|动脉硬化|碳13|碳14|碳-?13|"
    r"口腔科?|眼科|耳鼻喉科?)?")
_MULTI_SEG_JUNK_RE = re.compile(r"(建议|请|您|门诊|就诊|诊治|复查|随访|治疗|咨询|资料|"
                                r"详情|注意|可到|带上|进一步)")


def _parse_numbered_multi_findings(text: str, existing_names: list) -> list:
    """编号/方法行内多发现切分 → 候选列表(已有条目同行为触发前提)。"""
    existing_roots = {_cmp_norm(_strip_direction(n)) for n in (existing_names or []) if n}
    out: list[str] = []
    for raw in (text or "").split("\n"):
        s = raw.strip()
        if not _NUM_METHOD_RE.match(s):
            continue
        body = _NUM_METHOD_RE.sub("", s)
        # 缩写词内空格合并("心室早期复极 T 波改变" 的 T 波)
        body = re.sub(r"(?<![A-Za-z])T\s+波", "T波", body)
        body = re.sub(r"(?<![A-Za-z])ST\s+段", "ST段", body)
        _parts = re.split(r"(\s{1,}|[，,])", body)
        segs = []
        for _j in range(0, len(_parts) - 1, 2):
            _x = _parts[_j].strip(" \u3000,，、;；.。()（）【】")
            if _x:
                segs.append((_x, _parts[_j + 1] if _j + 1 < len(_parts) else ""))
        if len(segs) < 2:
            continue
        # 方法词行(口腔科/心电图等)的发现枚举可信, 直接产; 普通编号行需
        # 至少一段已被现有条目覆盖(保守触发)。
        # 2026-09-12 修复: 方法词现为可选组, bool(match) 对任意编号行恒真 →
        # 科普/建议子行("3、调节饮食…"/"7.…仍异常时…")被大量切成碎片
        # (钦州中/福建第二/贵港验收)。改为**实际匹配到方法词**才算 hit。
        _mm = _NUM_METHOD_RE.match(s)
        hit = bool(_mm and re.search(r"[^\d\s:：.、]", _mm.group(0)))
        valid = []
        for seg, sep in segs:
            # 段合法化: 去分号后叙述与括号内容("十二指肠球部溃疡（S2 期)"→"十二指肠球部溃疡")
            seg = re.split(r"[；;]", seg)[0]
            seg = re.sub(r"[（(].*$", "", seg).strip(" \u3000,，、.。:：")
            # 超长或含建议词("…水平椎管狭窄请您到脊柱外科门诊就诊")先截到使结果
            # ≤22字的最靠后特征词尾("T波改变如有既往…心血管疾病"→"T波改变"),
            # 再进入 junk/长度过滤("颈3/4 水平椎管狭窄")
            if len(seg) > 22 or _MULTI_SEG_JUNK_RE.search(seg):
                _cut = None
                for _m in reversed(list(_FINDING_TITLE_RE.finditer(seg))):
                    _cand = seg[:_m.end()].strip()
                    if 2 <= len(_cand) <= 22:
                        _cut = _cand
                        break
                if not _cut:
                    continue
                seg = _cut
            if not (2 <= len(seg) <= 22):
                continue
            if _MULTI_SEG_JUNK_RE.search(seg):
                continue
            if re.search(r"(相仿|相若|随诊)$", seg):
                continue  # 影像描述尾("较前相仿"类)
            core = _cmp_norm(_strip_direction(seg))
            if not core:
                continue
            if any(core == r or (len(core) >= 3 and (core in r or r in core))
                   for r in existing_roots if r):
                hit = True
            # 有效段: 含发现特征词/方向词; "≥3 字短纯中文"兜底仅限空格分隔段
            # (逗号分隔的短句("仍异常时"/"进食高纤维饮食")多为叙述, 需特征/方向词)
            if _FINDING_TITLE_RE.search(seg) or _DIRECTION_PHRASE_RE.search(seg):
                valid.append(seg)
            elif not sep.startswith("，") and not sep.startswith(",") \
                    and 3 <= len(seg) <= 10 and re.fullmatch(r"[\u4e00-\u9fff]+", seg):
                valid.append(seg)
        if hit:
            # 相邻合并: 后段以发现特征词开头且前段为无方向短纯中文名
            # ("心室早期复极" + "T波改变" → "心室早期复极 T波改变")
            merged: list[str] = []
            for seg in valid:
                if merged and _FINDING_TITLE_RE.match(seg) \
                        and re.fullmatch(r"[\u4e00-\u9fff]{3,10}", merged[-1]):
                    merged[-1] = merged[-1] + " " + seg
                else:
                    merged.append(seg)
            for seg in merged:
                seg = _normalize_disc(seg)
                if seg not in out:
                    out.append(seg)
    return out


def _postprocess_extracted_items(items: list[dict], text: str, raw_text: Optional[str] = None,
                                 weak_candidates: bool = False) -> tuple:
    """LLM 抽取结果的后处理流水线(确定性, 无 LLM)。

    顺序: 紧急标记 → 部位补全 → 编号/小结标题兜底(计数 fill) → junk 过滤 →
    候选链合并 → 左右叶对称补全 → 科名黑名单。返回 (items, n_fill)。
     2026-09-03: 自 _extract_abnormalities_async 抽出, 供一致性对账二次抽取复用。
     2026-09-04: LLM 全空时也继续执行确定性兜底(编号/小结标题 fill),
     否则 LLM 偶发空输出会把整份报告打成 0 条(福建第二/莆田)。
     2026-09-09: 标题兜底改用 raw_text(未做 keep_body/冒号加工的切段原文)——
     加工文本按句号/分号切句会抹掉编号标题内的分号("…随诊；右肺…"→空格拼接),
     使 fill 名称连体; 且 (n)/★ 行结构在加工时丢失(莆田全漏)。
    """
    items = list(items)
    # 2026-09-09: LLM 名形状清洗: 分号连体/检查项前缀/尾建议短语/正常性后半
    # (山东"CT：…，必要时随诊；右肺…钙化灶" 两条连体+建议入名; 齐鲁
    # "甲状腺结节；甲状腺功能正常" 正常性后半) → 拆为干净段。
    # 2026-09-09(修正): 仅真正发生形状变化(段数与原名不同/段名被改/sug 抽取)
    # 才标记 _split —— 单段无变化名不标记, 否则 _split 的 junk-context 豁免
    # 会让全部 LLM 条目绕过科普挖词过滤(日照 24 垃圾条目全量放行)。
    reshaped: list[dict] = []
    for it in items:
        nm = it.get("item_name") or ""
        segs = _expand_title_segments(nm)
        if not segs:
            reshaped.append(it)
            continue
        if len(segs) == 1 and segs[0][0] == nm and not segs[0][1]:
            reshaped.append(it)
            continue
        for seg_name, seg_sug in segs:
            nw = dict(it)
            nw["item_name"] = seg_name
            nw["_split"] = True
            if seg_sug and not nw.get("suggestion"):
                nw["suggestion"] = seg_sug
            reshaped.append(nw)
    items = reshaped if reshaped else items
    # 2026-09-12: 相邻标题连体拆分("【脂肪肝】【超重】"被 LLM 拼成"脂肪肝超重" →
    # 拆"脂肪肝"+"超重"; 仅当前缀含发现特征词时拆, "向心性肥胖"等真名不动)
    _tail_split: list[dict] = []
    for it in items:
        parts = _split_tail_obesity(it.get("item_name") or "")
        if len(parts) == 2:
            _tail_split.append({**it, "item_name": parts[0], "_split": True})
            _tail_split.append({**it, "item_name": parts[1], "_split": True})
        else:
            _tail_split.append(it)
    items = _tail_split
    items = _mark_urgent(items)
    for item in items:
        item_name = item.get("item_name", "")
        enriched = _recover_anatomical_prefix(text, item_name)
        if enriched and enriched != item_name:
            item["item_name"] = enriched
            _log.info("prefix-recover: %s -> %s", item_name, enriched)
    items = _filter_junk_abnormalities(items, source_text=text,
                                        junk_source=raw_text,
                                       title_names=list(_parse_numbered_titles(raw_text or text)))
    # 编号条目标题兜底 + 小结标题内容行兜底(A 步): LLM 漏提时确定性补。
    # 2026-09-09: 兜底移到滤卡**之后** —— 旧顺序(fill 先于滤卡)下, LLM 偶发输出
    # "无分号连体变体"(如 27 齐鲁把"甲状腺结节；甲状腺功能正常"去分号拼成
    # "甲状腺结节甲状腺功能正常")会占位(covered)挡住标题 fill, 而该连体名随后被
    # junk 滤掉 → 真标题(甲状腺结节)丢失。滤后判覆盖: 连体垃圾不在净名单, fill 正常补。
    existing_names = [it.get("item_name", "") for it in items]
    n_fill = 0
    # 2026-09-12: 弱切分候选(多方向行/编号行多发现)按院声明启用(profile
    # multi_findings, 默认关) —— 默认净度优先, 已验证医院保留规则补漏。
    _weak_titles = (_parse_direction_phrases(raw_text or text)
                    + _parse_numbered_multi_findings(raw_text or text, existing_names)) \
        if weak_candidates else []
    for title in list(_parse_numbered_titles(raw_text or text)) \
            + _parse_summary_item_titles(raw_text or text) \
            + _weak_titles:
        if not title:
            continue
        # 2026-09-09: 整串标题按分号/建议短语展开为 1+ 条(fill 专用):
        # 山东"CT：左肺…，必要时年度随诊；右肺…钙化灶"(两发现+建议连体) →
        # "左肺下叶微小纤维结节灶"(sug=必要时年度随诊) + "右肺下叶散在小钙化灶";
        # 齐鲁"甲状腺结节；甲状腺功能正常" → 剥正常性后半。
        for seg_name, seg_sug in _expand_title_segments(title):
            # 2026-09-10: fill 名扁平化(字距空格型报告标题名带空格, 拒绝/覆盖
            # 判定与落库名需无空格形态; 普通报告无字距空格, 压后等价)。
            seg_name = re.sub(r"\s+", "", seg_name)
            # 2026-09-12: 连体尾词拆分("脂肪肝 超重"标题 → 两条分别判覆盖, 防补回连体名)
            if len(_split_tail_obesity(seg_name)) == 2:
                continue  # 由 reshape 拆分负责产出; 此处无需再补
            # 2026-09-12: 纯脊柱节段("颈3/4")不是异常, 与 filter 内 spine-position 同口径
            # (广西人民 fill 从"9:CT 颈3/4、颈5/6 椎间盘…"切出过"颈3/4")
            if re.fullmatch(
                    r"(?:(?:颈|腰|胸|[LTC])\d+(?:/\d+|-S?\d+)?)"
                    r"(?:[、及](?:颈|腰|胸|[LTC])\d+(?:/\d+|-S?\d+)?)*", seg_name):
                continue
            if not seg_name or _SAFETY_NET_JUNK_RE.search(seg_name):
                _log.info("safety-net skip junk title: %s", title)
                continue
            # 2026-09-10: fill 名正常性/碎片拒绝 —— 子编号行"2.双肾输尿管膀胱
            # 未见明显异常"被标题解析当标题补入(fill 在滤卡之后, 不再复查);
            # 纯标点/单字碎片(".胆")同样不补。
            # 2026-09-12: 补句子式("不一定有临床意义,如连续多次升高")与方法前缀
            # 残片("口腔科提示18"→剥前缀后纯数字)拒绝(端到端抽查暴露)。
            _seg_probe = _METHOD_PROMPT_RE.sub("", seg_name).strip()
            # 2026-09-12(钦州中/福建/贵港验收): fill 在滤卡之后不过 junk-context,
            # 编号子行(科普/建议/检查描述)被标题解析切出的碎片需在此自拒:
            # "调节饮食/限制含钙…食物/如高动物蛋白/高糖/高脂肪/动物内脏…/
            #  尿酸结石患者应当避免/预防尿路感染的方法/检查有无结石/大小约/
            #  左肾中盏见一强回声斑/仍异常时/动态了解结节变化"
            if re.search(r"(饮食|食物|蔬菜|水果|油腻|辛辣|嘌呤|动物内脏|多食|限制|"
                         r"避免|预防|了解|排出|大小约|仍异常|动态|结节变化|"
                         r"见一|中盏见|高糖|高脂肪|高蛋白|检查有无)", seg_name) \
                    or re.match(r"^(?:如|若|含)(?:高|低|多|少)", seg_name):
                continue
            if re.search(r"未见(明显)?(异常|分流|液性|暗区|肿块|占位|出血|钙化)"
                         r"|无异常|未见异常回声", seg_name) \
                    or re.search(r"提示\s*\d*$", seg_name) \
                    or _ABNORMALITY_SENTENCE_RE.match(seg_name) \
                    or not _seg_probe \
                    or re.fullmatch(r"[\d\s、,，.．:：()（）\-–—/]+", _seg_probe) \
                    or len(re.sub(r"[。；、,，.．:：()（）·\u3000*＊★\-]", "", seg_name)) < 2 \
                    or re.fullmatch(r"[A-Za-z0-9、,.\-–—/\s]{1,10}", seg_name):
                continue
            # 2026-09-09: 覆盖判定 —— 更短的既有名不阻挡标题 fill
            # (日照【肝内血管瘤可能】被 LLM 挖词"肝内血管瘤"(5字, 将遭 junk 滤)
            # 占位 → fill 跳过 → 真标题丢失); 仅当既有名不短于标题时才视为覆盖。
            _seg_cmp = _cmp_norm(seg_name)
            _seg_stem = _cmp_norm(_strip_direction(seg_name))
            covered = any(
                t and (
                    _write_norm(t) == seg_name
                    or seg_name in _write_norm(t)
                    or (_write_norm(t) in seg_name and len(_write_norm(t)) >= len(seg_name))
                    or (_seg_cmp and _cmp_norm(t) == _seg_cmp)
                    # 词根覆盖: LLM 给"间接胆红素"(dev=偏高) 时不再补"间接胆红素偏高"
                    or (_seg_stem and len(_seg_stem) >= 2
                        and _cmp_norm(_strip_direction(t)) == _seg_stem)
                )
                for t in existing_names
            )
            if not covered:
                _log.info("safety-net fill: %s", seg_name)
                items.append({
                    "item_name": seg_name,
                    "suggestion": seg_sug or "",
                    "deviation": None,
                    "is_urgent": False,
                    "_safety_net": True,
                })
                existing_names.append(seg_name)
                n_fill += 1
    items = _merge_candidate_chain(text, items)
    items = _fill_symmetric_sides(text, items)
    # 2026-09-05: 尾端新过滤 —— [n] 垃圾与拼接、疫苗接种建议、推测性诊断
    # - 以 [ 开头 = LLM 复制的方括号小节/拼接垃圾(马鞍山"[CT"/"[幽门螺杆菌IgG")
    #   → 丢弃(fill 兜底会补 [] 标题的干净名)
    # - 疫苗/接种/幼儿 提示 = 健康建议非异常(滨州"疫苗接种健康提示"段)
    # - "血管瘤"与"肝内高回声结节"并存 = 报告推测("考虑血管瘤可能")非确诊(汕头)
    clean = []
    for it in items:
        nm = (it.get("item_name") or "").strip()
        nm = nm.lstrip("▲△★*＊·\u3000 ")
        if not nm or nm.startswith("["):
            continue
        # 2026-09-05: LLM 名带"方法+提示"前缀("放射科(CT)提示…")→ 剥保留发现
        nm = _METHOD_PROMPT_RE.sub("", nm).strip()
        # 2026-09-12: "彩超检查提示1"类方法+提示残片(潮州 23 fill 产出)
        if re.search(r"提示\s*\d*$", nm):
            continue
        # 2026-09-12: 科普残句尾("病理性红细胞增多见于")与影像描述尾缀
        # ("右肺下叶微小磨玻璃类结节，较前相仿" —— 描述句非异常名)
        if re.search(r"(见于|可见于|多见于)$", nm):
            continue
        # 2026-09-12: 括号不平衡名 = LLM/切分残片("横结肠)增生性息肉肠息肉"/
        # "5项阳性)"), 非正常发现名
        if nm.count(")") + nm.count("）") != nm.count("(") + nm.count("（"):
            continue
        # 2026-09-12: 建议/描述残片(LLM 或 _split 豁免 junk 后残留)
        if re.search(r"(仍异常|见一|中盏见|有无结石|动态了解|结节变化|动物内脏|部分息肉)", nm):
            continue
        # 2026-09-12: 数值比较式碎片("体重指数>24"), 非异常名
        if re.search(r"[<>≤≥]=?\s*\d", nm):
            continue
        # 2026-09-05: 牙位前缀剥离("18、28、38牙智齿"/"36牙楔状缺损" → 疾病名)
        nm2 = re.sub(r"^\d+(?:[、,，]\d+)*\s*牙", "", nm).strip()
        # 2026-09-12: 影像描述尾缀("右肺下叶微小磨玻璃类结节，较前相仿" →
        # "右肺下叶微小磨玻璃类结节" —— ";较前相仿"是对结节的描述非异常名)
        nm2 = re.sub(r"[,，]\s*(?:较前|较上次|与前片?|同前)[^,，。;；]{0,16}$", "", nm2).strip()
        nm2 = re.sub(r"[,，]\s*(?:大致)?(?:相仿|相若|变化不大|无明显变化)$", "", nm2).strip()
        if not nm2:
            continue
        if re.search(r"(疫苗|接种|免疫规划|中小学生|心脑血管疾病)", nm2) or "幼儿" in nm2:
            continue
        # 单纯"智齿"= 预防性拔除提示, 非病变(福建第二 6 号行建议拔除)
        if nm2 == "智齿":
            continue
        it = dict(it)
        it["item_name"] = nm2
        clean.append(it)
    if any(("肝内高回声结节" in (i.get("item_name") or "") or "肝内回声结节" in (i.get("item_name") or ""))
           for i in clean) and any((i.get("item_name") or "").strip() == "血管瘤" for i in clean):
        clean = [i for i in clean if (i.get("item_name") or "").strip() != "血管瘤"]
    items = [it for it in clean
             if not _NONABNORM_NAME_RE.match((it.get("item_name") or "").strip())
             and not _NONABNORM_SUFFIX_RE.search((it.get("item_name") or "").strip())
             and not _SYMPTOM_ONLY_RE.match((it.get("item_name") or "").strip())
             and not _ANATOMY_ONLY_RE.match(_write_norm(it.get("item_name") or ""))
             and (it.get("item_name") or "").strip() not in _FINDING_GRADE_ONLY]
    items = _dedup_generic_findings(items)
    return items, n_fill


async def _extract_abnormalities_async(conclusion_text: str,
                                        weak_candidates: bool = False) -> list[dict]:
    """调用 MedGo LLM 从结论文本提取异常项列表。

    方案4(2026-08-24):
    - no_think=True(提取类任务禁用思考, 提速并减少漂移)
    - 职责分离: 不再让 LLM 生成 item_normalized(其"标准化"=摘要化, 失真),
      只提取原文短语; 归一化交给 term_normalizer(方案1+2)。
    - 长文按行分段抽取后合并(防 max_tokens 截断)。
    - 输出 schema 校验(缺字段补默认)。
    """
    from app.ai.llm import get_chat_model, _guarded
    import json as _json, re as _re

    # 预处理1：合并 PDF 导致的折行，同一编号下的行拼成一段
    text = _merge_wrapped_lines(conclusion_text)
    # 预处理2：冒号拆分——只保留冒号后的正文（检查项前缀不发给 LLM）
    text = _keep_body_after_colon(text)
    # 2026-09-04: keep_body_after_colon 丢弃无编号行 —— 对"(1)超重:"/汉字序号
    # 式结论文本(莆田)会整份删空; 减过头(<100字)则退回原始文本(宁多勿缺)。
    if len(text) < 100:
        text = _merge_wrapped_lines(conclusion_text)

    model = get_chat_model(no_think=True)

    async def _call_one(prompt: str):
        return await model.ainvoke([("user", prompt)], max_tokens=4096)

    def _parse_content(content: str) -> list[dict]:
        content = content.strip()
        content = _re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = content.replace('</think>', '').replace('<think>', '')
        content = _re.sub(r'```json\s*', '', content)
        content = _re.sub(r'```\s*', '', content)
        parsed = _json.loads(content)
        if not isinstance(parsed, list):
            return []
        out = []
        for item in parsed:
            # 2026-09-10: MedGo 偶发输出"对象+裸字符串"混合数组(池州人民:
            # 前几条为对象、后几条直接是 "抗碱血红蛋白" 裸串 —— 空格密集文本
            # 分块不稳)。裸串是合法条目名, 直接收下(无建议/方向), 由后续
            # junk/句子式滤卡兜底, 不再整批丢弃(否则漏提大半+fill 补垃圾)。
            if isinstance(item, str):
                nm = _strip_check_prefix(str(item).strip())
                if nm:
                    out.append({"item_name": nm, "suggestion": "",
                                "deviation": None, "is_urgent": False, "_bare": True})
                continue
            if not isinstance(item, dict) or not item.get("item_name"):
                continue
            item["item_name"] = _strip_check_prefix(str(item["item_name"]))
            item.setdefault("suggestion", "")
            item.setdefault("deviation", None)
            item.setdefault("is_urgent", False)
            out.append(item)
        return out

    # === 2026-09-10: LLM 快照重放(新医院适配管线, 详见 AGENTS) ===
    # ABNORMALITY_LLM_SNAPSHOT_DIR=dir: 该目录按 chunk 文本 sha1 存真实 LLM 输出;
    #   文件存在 → 直接重放(不调 LLM, 后处理确定性可秒级迭代);
    # ABNORMALITY_LLM_SNAPSHOT_SAVE=1: 真调后写入快照(首次适配时采一次)。
    def _snap_path(chunk: str):
        d = os.getenv("ABNORMALITY_LLM_SNAPSHOT_DIR")
        if not d:
            return None
        import hashlib
        from pathlib import Path as _Path
        h = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:16]
        return _Path(d) / f"{h}.json"

    async def _llm_once() -> list[dict]:
        got: list[dict] = []
        for chunk in _chunk_text(text, 8000):
            snap = _snap_path(chunk)
            if snap and snap.exists():
                try:
                    replayed = _json.loads(snap.read_text(encoding="utf-8"))
                    if isinstance(replayed, list):
                        _log.info("abnormality snapshot replay: %s (%d)",
                                  snap.name, len(replayed))
                        got.extend(replayed)
                        continue
                except Exception as e:
                    _log.warning("snapshot replay failed %s: %s", snap, e)
            prompt = _ABNORMALITY_PROMPT.format(text=chunk)
            try:
                resp = await _guarded(_call_one(prompt))
                parsed = _parse_content(resp.content)
                got.extend(parsed)
                if snap and os.getenv("ABNORMALITY_LLM_SNAPSHOT_SAVE") == "1":
                    snap.parent.mkdir(parents=True, exist_ok=True)
                    snap.write_text(_json.dumps(parsed, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
                    _log.info("abnormality snapshot saved: %s (%d)", snap.name, len(parsed))
            except Exception as e:
                _log.warning("Failed to extract abnormalities (chunk len=%d): %s", len(chunk), e)
        return got

    def _merge_uncovered(existing: list[dict], extra: list[dict]) -> list[dict]:
        names = [it.get("item_name", "").replace(" ", "") for it in existing]
        for it in extra:
            n = (it.get("item_name") or "").replace(" ", "")
            if not n:
                continue
            dup = any(e and (e == n or (len(e) >= 3 and (e in n or n in e))) for e in names)
            if dup:
                continue
            existing.append(it)
            names.append(n)
            _log.info("double-pass merge: %s", it.get("item_name"))
        return existing

    items = await _llm_once()
    first_empty = not items
    items, n_fill = _postprocess_extracted_items(items, text, raw_text=conclusion_text,
                                                  weak_candidates=weak_candidates)
    # === 2026-09-03: 一致性对账 + 按需二次抽取(B 步) ===
    # safety-net 补了条目(LLM 与确定性标题不一致)或首次空结果 → 本次抽取不可靠,
    # 再抽一次取并, 防 LLM 随机漏提(桂林曾出现同文本两次 10 条 vs 6 条)。
    if (n_fill > 0 or first_empty) and text.strip():
        _log.info("abnormality double-pass: fill=%d first=%d text_len=%d",
                  n_fill, len(items), len(text))
        items2 = await _llm_once()
        if items2:
            items2, _ = _postprocess_extracted_items(items2, text, raw_text=conclusion_text,
                                                   weak_candidates=weak_candidates)
            items = _merge_uncovered(items, items2)
    # === 2026-09-09: 建议归属校验(确定性) ===
    # LLM 偶发把某条目的长建议串复制给相邻/后续条目(茂名 25 多次出现:
    # "…可考虑摘除前列腺。" 被贴给 碳13/肌酐/窦缓 等)。凡建议文本能定位回
    # 原文行、而该行与其条目名所在行不相邻(不同条目区)且建议不含条目名 →
    # 视为错配清空(宁可无建议, 不贴错建议)。
    items = _validate_suggestion_ownership(items, conclusion_text)
    return items


# 器官/部位专属词(建议归属校验用): 建议文本含这些词而条目名不含 → 该建议
# 可能错配自其它条目(茂名"…可考虑摘除前列腺"被贴给碳13/窦缓)。通用生活
# 建议(低盐饮食/适度锻炼/控制体重…)无器官词, 不参与归属校验。
_BODY_ORGANS_RE = re.compile(
    r"(前列腺|甲状腺|肝脏?|肾脏?|脾脏?|胰脏?|胆囊?|胆管|膀胱|输尿管|胃部?|肠道?|"
    r"心脏(?!(?:负担|压力|功能|病史|保健|风险))|心血管|心肌|肺部?|乳腺|子宫|卵巢|"
    r"睾丸|阴茎|肛门|直肠|颈动脉|冠状动脉|血管|动脉|静脉|淋巴结|神经|脊柱|椎间盘|"
    r"关节|骨骼?|晶体|视网膜|角膜|耳蜗|扁桃体|咽喉?|声带|鼻窦|牙周|牙龈|"
    r"垂体|肾上腺|食管|气管|支气管)"
)


def _validate_suggestion_ownership(items: list[dict], raw_text: str) -> list[dict]:
    """建议归属校验(纯函数)。LLM 偶发把某条目的长建议串复制给相邻/后续条目
    (茂名 25 反复出现: "…如病情严重，可考虑摘除前列腺。" 被贴给 碳13/肌酐/电轴 等)。

    仅当建议含**器官专属词**且条目名不含该词时进入归属校验(通用生活建议句
    "低盐饮食/适度锻炼/保持健康体重"可适用于血压/血脂/体重多条, 不校验)。
    命中即清空 suggestion(宁可无建议, 不贴错建议; 定位不到/改写不处理):
    L1 建议文本定位到的行是 *★/●/【 开头的标题行(另一条目), 且该行标题不含
        本条目名 → 错配。
    L2 建议行与本条目名行相距 >3 行, 且建议不含条目名 → 错配(无 ★ 结构兜底)。
    """
    if not raw_text:
        return items
    lines = raw_text.split("\n")

    def _locate(probe: str) -> Optional[int]:
        probe = probe.strip()
        if len(probe) < 4 or probe not in raw_text:
            return None
        for i, ln in enumerate(lines):
            if probe in ln:
                return i
        return None

    def _row_title(row: int) -> str:
        s = lines[row].strip()
        m = re.match(r"^[*＊★●○■\s]+([^：:，,。]{1,24})", s)
        if m:
            return m.group(1).strip()
        m = re.match(r"^【([^】]{1,24})】", s)
        if m:
            return m.group(1).strip()
        return ""

    def _name_probe(name: str) -> str:
        return name[:4] if len(name) >= 4 else name

    out: list[dict] = []
    for it in items:
        name = (it.get("item_name") or "").strip()
        sug = (it.get("suggestion") or "").strip()
        if not (sug and name and len(sug) >= 8):
            out.append(it)
            continue
        # 建议含器官专属词而条目名不含 → 才可能错配
        organ_hits = [w for w in _BODY_ORGANS_RE.findall(sug) if w and w not in name]
        if not organ_hits:
            out.append(it)
            continue
        mis_owned = False
        row_sug = _locate(sug[:24])
        row_name = _locate(name)
        probe = _name_probe(name)
        if row_sug is not None:
            title = _row_title(row_sug)
            if title and probe not in title:
                mis_owned = True  # L1: 建议落在另一条目的标题行
        if not mis_owned and row_sug is not None and row_name is not None \
                and abs(row_sug - row_name) > 3 and name not in sug:
            mis_owned = True  # L2: 远距 + 建议不含条目名
        if mis_owned:
            _log.info("suggestion mis-owned dropped: %s | sug=%s", name, sug[:30])
            it = dict(it)
            it["suggestion"] = ""
        out.append(it)
    return out


def _chunk_text(text: str, limit: int = 8000) -> list[str]:
    """按行切分长文本为不超过 limit 的块(在换行处断开, 不切断条目)。"""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in text.splitlines(keepends=True):
        if cur_len + len(line) > limit and cur:
            chunks.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


# === 方案4(2026-08-24): 提取后确定性噪声过滤 ===
# 2026-09-04: 拆强弱两档 —— LLM 对 OCR/复合行提出的真发现常带方法词
# ("彩超提示肝囊肿""心电图提示左心室高电压", 福建第二), 旧规则"提示/彩超"
# 命中即滤 → 整批真异常被杀成 0 条。强词(科普叙述)命中即滤; 弱词(方法/建议/
# 测量)命中但条目含发现特征词或方向词 → 视为真发现放行。
_JUNK_STRONG_RE = re.compile(
    r"(常见于|多见于|可表现为|可出现|出现于|考虑为|一项|以上即|部分指标|饮食|运动|药物|饮酒|"
    r"造成|导致|使|可能性|中风|血栓|梗塞|血管壁|血压升高|"
    r"所致|引起|症状|为上|为特征|是以|可导致|一系列|"
    r"体检数据|数据为|类圆形|不规则形|直径≤|密度增高|"
    r"性质待查|待查|年\d+月\d+日|"
    r"温馨提示|健康热线|紧急|异常结果|分层|健康教育|既往史|按具体执行|需要进一步|进一步诊治|需进一步)"
)
_JUNK_WEAK_RE = re.compile(
    r"(提示|建议|复查|随访|咨询|呼气试验|血流|大小约|直径|×|mm|cm|TI|RADS|[ABCD]类|"
    r"次/分|Kg|kg/m|\^|/L|\d+岁|"
    r"【|】|彩超|心电图|MR平扫|CT平扫|TCD|动脉硬化检测|经颅多普勒|口腔检查|正常)"
)
# 弱词命中时的放行条件: 含发现特征词或方向/阳性词(真发现)
_JUNK_WEAK_SAFE_RE = re.compile(
    r"(结节|结石|囊肿|增生|肥大|反流|异常|钙化|息肉|脂肪|肿瘤|糜烂|溃疡|硬化|狭窄|增厚|"
    r"斑块|肌瘤|占位|阴影|血管瘤|超重|肥胖|萎缩|心律不齐|血症|沉着|光团|"
    r"偏高|偏低|升高|降低|增高|减少|增多|阳性|弱阳性|反流|增生)"
)
# 2026-08-31: 句子式/描述性条目(LLM 从科普叙述提取的碎片):
# 以"为/属/呈/符合/表明/提示/考虑"等开头的叙述短语(H003"为缺血性 心脑血管病"
# "符合轻度阻塞性睡眠呼吸暂停低通气综合征""表明有幽门螺旋杆菌(HP)感染"),
# 以及独立建议残留短词("多饮水"/"适当"等)
_ABNORMALITY_SENTENCE_RE = re.compile(
    r"^(?:为|属|呈|符合|表明|提示|考虑|怀疑|是|可能|有|见|发现|出现|存在|"
    r"已属|目前|此次|患者|建议|请|适当|减少|加强|不一定)[\u4e00-\u9fa5A-Za-z()（）·\-/ ]{2,}"
    r"|^(?:多饮水|忌|注意)$"
)


def _filter_junk_abnormalities(items: list[dict], source_text: str = "",
                               title_names: Optional[list] = None,
                               junk_source: Optional[str] = None) -> list[dict]:
    # 2026-08-31: 编号标题(确定性来源)豁免科普/句子式过滤:
    # LLM 提取"4:高血压"标题时, 所在行含"建议您/饮食/复查"等科普标记会被
    # junk-context 误杀; 标题是报告方标注的异常条目本体, 直接豁免。
    # 2026-09-09: title_names 显式传入(raw 切段原文解析) —— 兜底标题与 LLM 名
    # 的 is_title 豁免必须基于同一份 raw(加工文本会抹分号/(n) 结构)。
    net_titles = set(title_names) if title_names is not None \
        else (set(_parse_numbered_titles(source_text)) if source_text else set())
    # 2026-09-10: 字距空格型报告(池州人民等 PDF 汉字逐字空格)比较前扁平化 ——
    # 源/名去全部空白后再判 not-in-source / junk-context / 正常描述(LLM 摘要名与
    # 原文字距空格形态不匹配曾整批误杀; 普通报告无字距空格, flat 前后等价)。
    src_flat = _write_norm(source_text) if source_text else ""
    src_flat_nl = re.sub(r"[ \t\u3000\xa0]+", "", source_text) if source_text else "" 
    # 2026-08-31: 椎间盘/椎管狭窄条目拆分与归一(LLM 偶合并为一条):
    # "颈3/4、颈5/6 椎间盘向后突出,颈3/4 水平椎管狭窄" → 椎间盘突出 + 水平椎管狭窄;
    # "椎间盘向后突出" → "椎间盘突出"。拆分条目标记 _split, 豁免"不在原文"检查。
    expanded: list[dict] = []
    for it in items:
        name = it.get("item_name", "")
        if not name:
            continue
        # 2026-09-10: 字距空格型 LLM 名先扁平再判定(池州 "L4-5 及 L 5 - S 1 椎 间盘
        # 膨 出" 的"椎间盘"三字不连续, 旧归一完全失效 → 碎节段变体双份落库)
        flat = re.sub(r"\s+", "", name)
        if "椎间盘" in flat and "椎管狭窄" in flat:
            expanded.append({**it, "item_name": "椎间盘膨出", "_split": True})
            expanded.append({**it, "item_name": "水平椎管狭窄", "_split": True})
        elif "椎间盘" in flat:
            # 2026-09-10(用户口径): 剥节段/部位/方位前缀, **方向词保留原文** ——
            # "L3-4、L4-5及L5-S1椎间盘膨出"→"椎间盘膨出"(报告写膨出);
            # "颈3/4、颈5/6椎间盘向后突出"→"椎间盘突出"(突出不能被改成膨出)。
            # 无方向词(退行性变等): 取"椎间盘"起、剥节段前缀与尾部杂文的整体。
            m = re.search(
                r"椎间盘(?:向后|向前|向侧|向后方|向侧方|中央|旁中央|外侧)?"
                r"(膨出|突出|脱出)", flat)
            if m:
                name2 = "椎间盘" + m.group(1)
            else:
                name2 = re.sub(r"[,，。;；:：].*$", "", flat[flat.find("椎间盘"):])
            expanded.append({**it, "item_name": name2, "_split": True})
        elif "椎管狭窄" in flat:
            # 2026-09-12: "水平椎管狭窄"保留部位修饰(广西人民验收)
            _st = "水平椎管狭窄" if "水平" in flat else "椎管狭窄"
            expanded.append({**it, "item_name": _st, "_split": True})
        else:
            expanded.append(it)
    items = expanded
    out = []
    for it in items:
        raw_name = it.get("item_name", "")
        if not raw_name:
            continue
        # 2026-08-31: 幽门螺旋杆菌/幽门螺杆菌 同义归一(百色报告重复提取两条)
        name = raw_name.replace("幽门螺旋杆菌", "幽门螺杆菌")
        # 2026-08-31: LLM 合并条目("脂肪肝 超重")剥离尾词超重/肥胖
        # (BMI 异常由指标线"身高体重指数"承载, 结论线不再单列)
        name = re.sub(r'\s+(?:超重|肥胖)$', '', name)
        if not name:
            continue
        it["item_name"] = name
        is_title = name in net_titles
        flat_name = _write_norm(name)
        if len(name) <= 1:
            continue
        # 2026-08-31: 编号标题豁免不含"正常性条目"(窦性心律/正常心电图是正常描述)
        if is_title and _SAFETY_NET_JUNK_RE.search(name):
            _log.info("title normal-item filtered: %s", name)
            continue
        if re.fullmatch(r'[\d\s\-\+\.%\/\*~=～]+', name):
            continue
        # 2026-09-04: junk 分强弱 —— 强词(科普叙述)命中即滤; 弱词(方法/建议/测量,
        # 如"彩超提示肝囊肿""心电图提示左心室高电压" 的 彩超/提示)命中但条目含
        # 发现特征词/方向词 → 真发现放行(福建第二 OCR 复合行曾被全杀成 0 条)。
        if _JUNK_STRONG_RE.search(name):
            _log.info("junk item filtered: %s", name)
            continue
        if _JUNK_WEAK_RE.search(name) and not _JUNK_WEAK_SAFE_RE.search(name):
            _log.info("junk item filtered: %s", name)
            continue
        # 2026-08-31: 句子式条目(以"为/符合/表明/建议"等开头的叙述短语)
        # 真异常名以名词开头(甲状腺结节/超重), 不会以这些词开头
        # 2026-09-09: _safety_net(fill)与 _split(标题/LLM 拆分)来源是报告方
        # 标题短语, 豁免句子式/junk-context(山东/莆田 fill 条目带科普同行)。
        if not is_title and not it.get("_safety_net") and not it.get("_split") \
                and _ABNORMALITY_SENTENCE_RE.match(name):
            _log.info("sentence-item filtered: %s", name)
            continue
        # 2026-08-31: 折行/剥离残留(行首孤立标点、空包、纯字母碎片"mm")
        if re.fullmatch(r"[\s。；、,，.…~～-]+", name):
            _log.info("punct-item filtered: %s", name)
            continue
        if re.fullmatch(r"[a-zA-Z]{1,3}", name):
            _log.info("letter-fragment item filtered: %s", name)
            continue
        # 2026-08-31: 解剖位置碎片("颈3/4""颈5/6")不是独立异常
        # 2026-09-10: 扩展字母节段("L3-4""L4-5及L5-S1" —— LLM 把椎间盘行拆出的
        # 残片, 扁平化后判定, 椎间盘主条目由 expanded 归并为"椎间盘膨出")
        if re.fullmatch(
                r"(?:(?:颈|腰|胸|[LTC])\d+(?:/\d+|-S?\d+)?)"
                r"(?:[、及](?:颈|腰|胸|[LTC])\d+(?:/\d+|-S?\d+)?)*", flat_name):
            _log.info("spine-position fragment filtered: %s", name)
            continue
        # 2026-08-28: 正常描述不得当异常条目(超声/心电"未见明显液性暗区/未见分流信号")
        if re.search(r"未见(明显)?(异常|分流|液性|暗区|肿块|占位|出血|钙化)|无异常|未见异常回声", flat_name):
            _log.info("normal-description filtered: %s", name)
            continue
        # 2026-09-12: 纯部位词单独成条 = LLM 碎名(弘爱"二尖瓣" vs 完整"…轻度反流")
        if _ANATOMY_ONLY_RE.match(flat_name):
            _log.info("anatomy-only item filtered: %s", name)
            continue
        # 2026-09-10: junk-context / not-in-source 用扁平化文本比较(字距空格报告)
        # 2026-09-11: junk 检查源优先用未加工原文(raw_text) —— 加工文本会裁句,
        # 使"如您有胸闷、胸痛、心悸等不适,建议专科诊治"失去建议词 → "胸痛"漏滤(日照)。
        _junk_src = re.sub(r"[ \t\u3000\xa0]+", "", junk_source or source_text or "")
        if _junk_src and _all_occurrences_in_junk_context(_junk_src, flat_name or name):
            _log.info("junk-context item filtered: %s", name)
            continue
        # 2026-08-31: 条目不在原文 → LLM 幻觉补全("十二指肠溃"→"十二指肠溃疡"),
        # 滤掉; 用归一前名检查(幽门螺杆菌 归一不误杀); 编号标题/兜底/拆分条目不受影响
        # 2026-09-10: 扁平化后整名比较(字距空格报告); 不做 4-gram 子串放行 ——
        # 会救活"甲状腺结节甲状腺功能正常"类无分号连体垃圾(整名扁平后必可定位)。
        if not is_title and not it.get("_split") and source_text \
                and flat_name not in src_flat \
                and _cmp_norm(name) not in src_flat:
            _log.info("not-in-source item filtered: %s", name)
            continue
        it.pop("_safety_net", None)
        out.append(it)
    # 完全重复去重(不剔子串: "脂肪肝"是"脂肪肝(脂肪性肝病)"的子串但独立有效,
    # 长条若被科普上下文过滤, 剔短者会双双丢失)
    deduped: list[dict] = []
    seen_names: set = set()
    for it in out:
        name = it.get("item_name", "")
        key = _write_norm(name)
        if not key or key in seen_names:
            continue
        seen_names.add(key)
        deduped.append(it)
    # 2026-08-31: 互含碎片去重 —— 短名被同批另一条长名包含且长度差≥2 时删短者
    # (LLM 偶发碎片如 "慢性" vs "慢性萎缩性胃炎"、"钙化灶" vs "肝内钙化灶")
    # 2026-08-31: 程度词规范化 —— "三尖瓣反流" vs "三尖瓣轻度反流" 是同一发现,
    # 剥离(轻度|中度|重度|明显)后同名则保留一条(带程度词的优先, 信息更全)
    def _deg_norm(n: str) -> str:
        return re.sub(r"(轻度|中度|重度|极重度|明显)", "", n)

    names = [x.get("item_name", "") for x in deduped]
    norm_kept: list[dict] = []
    for it in deduped:
        name = it.get("item_name", "")
        nn = _deg_norm(name)
        # 已有程度词版本(如"三尖瓣轻度反流")保留, 裸版剔除
        if any(
            other != name and nn == _deg_norm(other) and nn != other and nn == name
            for other in names
        ):
            _log.info("degree-dup item filtered: %s", name)
            continue
        norm_kept.append(it)
    names = [x.get("item_name", "") for x in norm_kept]
    sub_filtered: list[dict] = []
    for it in norm_kept:
        name = it.get("item_name", "")
        flat = _write_norm(name)
        if any(
            other != name and len(flat) + 2 <= len(_write_norm(other))
            and flat in _write_norm(other)
            for other in names
        ):
            _log.info("fragment item filtered: %s", name)
            continue
        sub_filtered.append(it)
    return sub_filtered


# 科普定义句标记: 条目在原文的所有出现位置都落在含这些标记的句子里 → 科普叙述, 非发现
# 2026-08-28: "*超重"类变体标题行是异常条目本体, 豁免 junk-context 过滤
# 2026-08-31: 补跨行科普句特征(颈动脉粥样硬化科普"彩超可发现...斑块形成、动脉狭窄程度等")
_JUNK_CONTEXT_RE = re.compile(
    r"(常见于|多见于|可出现|可导致|多与|正常人|结合临床|所致|是指|属于|是一种|临床上以|最多见|提示|"
    r"主要表现为|不需特殊(?:治疗|处理)|等症状发生时|请及时|就诊|请至|诊治|随诊|"
    r"密切相关|紧密相关|正相关|负相关|已经证实|在医生指导下|控制体重|严重影响|"
    r"可能(?:是|为)|分为|分(?:为|成)|其中|以及|"
    r"敏感指标|下降到正常|上升到正常|是反映|"
    r"等病理|多种因素|可能为|高度(?:重视|警惕)|如为|可能性大|"
    r"是[一-龥]{1,12}(所致|引起|造成的)|"
    r"等[，,]|为一|为上|以.{0,6}为特征|建议|复查|随访|治疗|预防|饮食|运动|"
    r"食用|进食|食物|多喝水|饮水|油腻|辛辣|蔬菜|水果|嘌呤|动物内脏|"
    r"可发展为|会在此基础上|可评估|可表现为|可进展|可能发展|如果|若发展|可合并|"
    r"可发现|彩超|颈动脉内膜|斑块形成|狭窄程度|重要危险因素|反映全身|的[一-龥]{1,6}之一|"
    r"尿液中|有机物|无机物|沉积|结晶体|形成不溶于|常见引起|生理性因素|等原因|多无症状|"
    r"无需要处理|无需处理|不建议|不影响健康|引起|诱发|可以导致|会导致|导致的|"
    r"考虑|性质待查)"
)


def _all_occurrences_in_junk_context(text: str, name: str) -> bool:
    """item_name 在原文的所有出现位置, 所在句都含科普标记 → True(科普叙述名词)。

    豁免: 条目作为"*标题行"独立出现时(如"*超重"), 视为报告方标记的异常条目, 不滤。
    """
    import re as _re
    # 2026-09-09: 句子边界不含换行 —— PDF 断行会把"…心悸等不适，\n建议专科诊治。"
    # 切成两句, 建议词落到另句, 使"胸痛"类科普挖词漏滤(日照)。
    # 2026-09-10: 但字距空格型报告(池州人民)行间无句号, 全部条目行会连成一句
    # (含"随诊/建议"等词) → junk-context 整批误杀。折行合并只对非条目行生效,
    # 换行后紧跟 编号/星号/【/子编号 处强制切句。
    _split_re = _re.compile(
        r'[。！？]|(?=\n\s*(?:\d+[\s、.．:：]?|[*＊★●○■]|【|[（(]\d+[）)]))')
    sentences = [x for x in _split_re.split(text) if x.strip()]
    hits = [s for s in sentences if name in s]
    if not hits:
        return False
    # 变体标题行豁免: "*超重" / "【超重】" 独立行(长度短且以 * 或编号开头)
    # 2026-09-04: 行首条目前缀豁免 —— name 出现于行首, 前文只有编号/方法/提示
    # ("4. 彩超提示肝囊肿"), 是报告方列举条目而非科普叙述(福建第二 OCR 复合行)
    # 2026-09-05: 枚举句豁免 —— 句子以 编号/方法词/提示 开头("彩超提示双侧颈动脉斑块
    # 形成、右侧锁骨下动脉斑块形成…"), 句内所有发现都是报告方枚举, 不滤(斑块/三尖瓣等)。
    # 2026-09-09: 豁免收紧 —— ①【】行整句不再豁免(日照 24 / 齐鲁 27 的正文定义句
    # "【肝内血管瘤可能】肝血管瘤是一种…" 挖词"肝血管瘤/海绵状血管瘤/颈动脉血管狭窄"等
    # 全被【】行首豁免放行), 只豁免【】标题本体(name == 或 in 首个【】内容);
    # ②枚举豁免必须带 方法词或提示动词(★ 空头/冒号头不再豁免, 茂名 25
    # "尿流中断/变细/淋漓" 从"★前列腺增大:…"正文挖出的症状词漏滤)。
    _head_re = _re.compile(
        r"^\s*[0-9\s、.．:：\-*＊★]*(?:彩超|超声|B\s*超|心电图|CT|X\s*线|DR|检查|体检|"
        r"碳13|胃镜|肠镜|口腔|眼科|耳鼻喉)?\s*(?:提示|示|检出|发现)?\s*$")
    _enum_head_re = _re.compile(
        r"^(?:[0-9\s、.．:：\-*＊★]+)?"
        r"(?:(?:彩超|超声|B\s*超|心电图|脑电图|CT|X\s*线|DR|检查|体检|"
        r"碳13|胃镜|肠镜|口腔|眼科|耳鼻喉|心脏|颈动脉)\s*(?:提示|示|检出|发现|所见|小结|[:：])"
        r"|(?:提示|示|检出|发现|所见|小结))")
    for s in hits:
        s2 = s.strip()
        if _re.match(r'^[*＊★]\s*' + _re.escape(name) + r'$', s2):
            return False
        if _re.match(r'^\d+\s*[、.．]\s*' + _re.escape(name) + r'$', s2):
            return False
        # 【】标题本体豁免: name 等于句首首个【】组内容(或为标题去
        # "可能/待查"尾缀的变体)。2026-09-09 收紧: 不再对"标题的子串挖词"
        # 豁免(日照"肝内血管瘤"从【肝内血管瘤可能】正文挖出 → junk 管)。
        gm = _re.match(r'^\s*【([^】]+)】', s2)
        if gm:
            g = gm.group(1).strip()
            gstem = _re.sub(r"(可能|待查|待定)$", "", g)
            if name == g or (len(gstem) >= 3 and (name == gstem or name in gstem)):
                return False
        idx = s2.find(name)
        # 2026-09-09: 空前缀(head 为空)不再豁免 —— 空串正则全可选必匹配,
        # 会把"瓣膜轻度返流不需特殊治疗…"这类句首名词当行首条目放行(日照)。
        if idx > 0 and _head_re.match(s2[:idx]):
            return False
        mh = _enum_head_re.match(s2)
        if mh and len(mh.group(0).strip()) >= 2 and mh.end() < len(s2) \
                and s2[mh.end():].strip():
            return False  # 句首为报告方枚举标记(方法+提示…), 句内发现为枚举项
    return all(_JUNK_CONTEXT_RE.search(s) for s in hits)


# 编号标题兜底: 首词为检查方法时跳过该词取次词(异常名)
# ("3:肠镜 内痔"→"内痔", "6:心电图 左心室高电压"→"左心室高电压")
_EXAM_NAME_RE = re.compile(
    r"^(胃镜|肠镜|肠镜|B\s*超|超声|彩超|心电图|脑电图|肌电图|CT|MRI|X\s*光|X\s*线|DR|"
    r"TCD|动脉硬化|经颅多普勒|人体代谢率|人体成分|骨密度|口腔检查|耳鼻喉|妇科|前列腺检查|"
    r"颈动脉彩超|甲状腺彩超|腹部彩超|泌尿系彩超|心脏彩超|肝胆胰脾彩超|检查|检测|测定)$"
)


def _expand_title_segments(title: str) -> list:
    """编号/【】整串标题/LLM 名展开为 1+ (干净名称, 建议) 段。

    - 按 ; 分号切段: 山东"…随诊；右肺…钙化灶"两发现连体(拆后各为独立条目);
      齐鲁"甲状腺结节；甲状腺功能正常"正常性后半(功能正常)剥除。
    - 段尾建议短语(",必要时年度随诊")剥离为 suggestion(挂在含该短语的段)。
    - 各段剥检查项前缀(_CHECK_PREFIX_RE)与 RADS 分级, 去星号/空格。
    """
    parts = []
    if not title:
        return parts
    for s in re.split(r"[;；]", title):
        s = s.strip(" \u3000·*＊")
        if not s:
            continue
        seg_sug = ""
        # 段尾建议短语: "…，必要时年度随诊" / "，建议您定期复查"
        m = re.search(
            r"[，,]\s*((?:必要时|建议(?:您)?|酌情|请(?:至|到|及时)?)[^，,。；;]{1,24}?"
            r"(?:随诊|复检|复查|随访|就诊|诊治|处理|咨询|治疗|干预))$", s)
        if m:
            seg_sug = m.group(1).strip()
            s = s[:m.start()].strip("，, ")
        # 正常性后半("甲状腺功能正常" 等)不是异常段 → 丢弃
        if re.fullmatch(r"[^；;。]{0,14}(?:功能|结果|形态|大小|结构)?(?:正常|未见异常|阴性)", s):
            continue
        s = _strip_check_prefix(s)
        s = re.sub(r"[（(][^）)]*RADS[^）)]*[）)]", "", s)
        s = re.sub(r"[A-Za-z]*-?RADS\s*\d*\s*[级类]?\s*", "", s)
        s = re.sub(r"[A-Za-z]*-?RADS\s*[0-9]?\s*[级类]?", "", s)
        s = s.strip("*＊★·\u3000 ：:—")
        if len(s) < 2:
            continue
        # 2026-09-09: 超长组合段(>20字且含顿/逗)按 顿号/逗号 拆 —— 齐鲁
        # "双侧锁骨下动脉狭窄可能，远端动脉血液灌注欠充足、双侧外周动脉僵硬度
        # 增高、左侧下肢动脉中层钙化"(36字组合标题)拆为 4 条;
        # 短名("二尖瓣、三尖瓣少量反流")不拆(莆田 26 需保留整条)。
        if len(s) > 20 and re.search(r"[、,，]", s):
            for sub in re.split(r"[、,，]", s):
                sub = sub.strip("，, ")
                # 2026-09-10: 2 字短发现("超重")保留(山东多发现连串"…增高，…增高，超重")
                if len(sub) >= 2:
                    parts.append((sub, ""))
            continue
        parts.append((s, seg_sug))
    return parts


# 2026-09-10: 兜底"发现标题"垃圾判定(编号/小结标题解析共用) ——
# ①纯方法/检查名(无发现词): "胸部CT/甲状腺B/腹部B"(真名由正文/LLM 承担);
# ②科普定义句: "肺结节是指肺内直径≤3cm的…"(不是发现)。
_JUNK_TITLE_METHOD_RE = re.compile(
    r"^(?:[\u4e00-\u9fa5]{1,6})?\s*(?:CT|MRI|MR|B\s*超?|彩超|超声|"
    r"X\s*[光线]|DR|TCD|心电图|脑电图)$")
_JUNK_TITLE_SCIENCE_RE = re.compile(
    r"是指|是一种|是临床|是尿液|是前列腺|是脂质|是肝|是常见|"
    r"是人体|是眼|是肾|是气体")


def _is_junk_fallback_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(_JUNK_TITLE_METHOD_RE.match(t) or _JUNK_TITLE_SCIENCE_RE.search(t))


def _parse_numbered_titles(text: str) -> list:
    """从结论文本解析编号条目标题, 如 "4、胆囊结节" → "胆囊结节"。

    只取编号行(如 "4、xxx"), 忽略无编号行; 标题取编号后、冒号/句号前的内容。
    方案4(2026-08-24):
    - 纯数字/符号行(表格数据"8""-140")跳过
    - 【】标题兜底: 【甲状腺结节】【血脂异常】 等"发现特征词"标题行也解析
      (防 LLM 漏提取超声/总检发现, 如崇左【甲状腺结节】)
    """
    titles = []
    _lines = (text or "").split('\n')
    for _idx, line in enumerate(_lines):
        stripped = line.strip()
        # 2026-09-10: 字距空格型行(池州人民等 PDF 汉字间逐字空格) —— 压缩 CJK/
        # 字母数字/百分号相邻空白, 使编号/标题结构与名称解析可靠(仅影响解析判定,
        # 不写回展示文本)。判定: 单空格数 ≥ CJK 字符数一半。
        _cjk_n = len(re.findall(r'[\u4e00-\u9fff]', stripped))
        if _cjk_n >= 4 and len(re.findall(
                r'(?<=[\u4e00-\u9fff0-9A-Za-z%]) (?=[\u4e00-\u9fff0-9A-Za-z%])',
                stripped)) * 2 >= _cjk_n:
            stripped = re.sub(
                r'(?<=[\u4e00-\u9fff0-9A-Za-z%]) (?=[\u4e00-\u9fff0-9A-Za-z%])',
                '', stripped)
        # 2026-09-10: 编号形态规整(所有行通用) —— 数字间空格("1 1 、【…】")、
        # 编号后空格夹标点("1 .肝…"/"1 、【…】") → 先压缩再匹配编号
        stripped = re.sub(r'(?<=\d) (?=\d)', '', stripped)
        stripped = re.sub(r'(?<=\d) ([\u3001,.:.\uff09)])', r'\1', stripped)
        # 2026-09-10: 数字直接接字母("50mm需要进一步诊治…")= 尺寸/单位粘连, 非编号条目
        if re.match(r'^\d+[A-Za-z]', stripped):
            continue
        # 2026-08-31: 支持 "3:内痔" 半角冒号编号(H003 崇左格式)
        # 2026-09-10: 编号标点前允许空格("1 .肝…" 池州); head 为 CJK 字距
        # 形态(空格密度 ≥0.5)时压缩, 名称取干净整串
        m = re.match(r'^\d+[\u3001,.:.\uff09)]?\s*(\S.*)$', stripped)
        if m:
            _head0 = m.group(1)
            _hc = len(re.findall(r'[\u4e00-\u9fff]', _head0))
            if _hc >= 3 and len(re.findall(
                    r'(?<=[\u4e00-\u9fff0-9A-Za-z%]) (?=[\u4e00-\u9fff0-9A-Za-z%])',
                    _head0)) * 2 >= _hc:
                _head0 = re.sub(
                    r'(?<=[\u4e00-\u9fff0-9A-Za-z%]) (?=[\u4e00-\u9fff0-9A-Za-z%])',
                    '', _head0)
        if m:
            # 2026-08-31: 只取编号后的"首词/次词"(到空白或标点前), 避免把
            # 叙述整行当标题(H003"4:高血压 您有高血压史...");
            # 首词是检查方法("胃镜/肠镜/心电图/B超/CT...")时取次词(异常名)
            head = _head0.strip()
            # 2026-09-12: "方法(+修饰)+提示"前缀先剥("5. 放射科(骨密度)提示双髋关节
            # 骨质密度减少：" → "双髋关节骨质密度减少"， 福建第二漏项)
            _stripped_head = _METHOD_PROMPT_RE.sub("", head).strip()
            if _stripped_head:
                head = _stripped_head
            # 2026-09-12: "N. 【xxx】" 形式(钦州中医扫描件编号+全角【】标题)——
            # 取【】内内容, 含发现特征词/方向词才用(检查项名如【腹部彩超】仍滤)
            _from_bracket = False
            if head.startswith("【"):
                _grp = re.findall(r"【([^】]+)】", head)
                _inner = " ".join(g.strip() for g in _grp if g.strip())
                if _inner and (_FINDING_TITLE_RE.search(_inner)
                               or re.search(r"(偏高|偏低|偏大|偏小|升高|降低|增高|阳性|异常|增多|减少)", _inner)):
                    head = _inner
                    _from_bracket = True
                else:
                    continue
            # 2026-09-05: 方括号包裹标题("12、[CT 提示:冠脉钙斑]")—— 取 [] 内完整
            # 内容(可含空格/标点), 剥 [] 后作为标题(马鞍山人民 13 条 [n] 式枚举)。
            # 未闭合(跨行折行"9、[幽门螺杆菌IgG 抗体\n高]")→ 不产出, 靠 LLM 覆盖。
            if _from_bracket:
                # 【】标题整串作名(供 _expand_title_segments 拆分: "窦性心律不齐，ST抬高,
                # 提示早期复极" / "*总胆固醇(T-CH)偏高,低密度…,体重指数偏高,肥胖")
                seg = head
            elif head.startswith("["):
                mb = re.match(r'^\[([^\]]+)\]', head)
                if mb:
                    seg = mb.group(1).strip()
                else:
                    # 2026-09-12: 未闭合 [ 跨行("6、[甲状腺结节,\n考虑C-TIRADS3 类]")→
                    # 与下一行拼接取段, 逗号后修饰("考虑…类")剥除(马鞍山 25,
                    # 旧逻辑整条不产, LLM 波动漏提时无兜底)
                    _nxt = _lines[_idx + 1].strip() if _idx + 1 < len(_lines) else ""
                    _mb2 = re.match(r'^\[([^\]]+)\]', head + _nxt)
                    seg = _mb2.group(1).strip() if _mb2 else ""
                    if seg:
                        seg = re.split(r"[,，]", seg)[0].strip()
                if not seg:
                    continue
            else:
                seg = re.split(r'[\s。；;，,、:：()（）]+', head, maxsplit=1)[0]
                # 2026-09-10: 编号条目条件句/建议句不是发现标题(滨州"2.若出现异常
                # 症状或原有症状加重，应及时泌尿外科就诊…"曾被当标题豁免 junk 过滤)
                if seg.startswith(("若", "如", "如果", "必要时", "当", "一旦", "请")):
                    continue
                # 2026-09-10: 多发现行(分号分隔, 弘爱"残根；龋齿；牙龈炎；牙结石（+）")
                # → 整段作 title, 由 _expand_title_segments 拆条(HL 只取首词会漏后项)
                if ("；" in head or ";" in head) and len(head) <= 80:
                    seg = head.split("。")[0].strip()
                if _EXAM_NAME_RE.match(seg):
                    seg = re.split(r'[\s。；;，,、:：()（）]+', head, maxsplit=1)
                    seg = seg[1].strip() if len(seg) > 1 and seg[1].strip() else seg[0]
                    seg = re.split(r'[\s。；;，,、:：()（）]+', seg, maxsplit=1)[0]
            title = seg
        elif re.match(r"^[（(]\s*\d+\s*[）)]\s*[^:：]{1,24}[:：]", stripped):
            # 2026-09-04: 子条式条目"(1)超重:…"(莆田九十五) —— 剥 (n) 前缀取冒号前
            # 名称; 有效性: 整名含发现特征词或方向词(排除"(1)均衡饮食,控制…"建议行、
            # "(1) 甲状腺彩超+颈部淋巴结:" 检查项行)。
            # 2026-09-09: 名称取冒号前完整片段(不再只取首词) —— "(3)二尖瓣、三尖瓣
            # 少量反流:" 首词"二尖瓣"无特征词被丢, 漏提(莆田); 整名含"反流"应产出。
            head = re.sub(r"^[（(]\s*\d+\s*[）)]\s*", "", stripped).split(":", 1)[0].split("：", 1)[0].strip()
            if not re.search(r'(偏高|偏低|升高|降低|增高|异常|阳性|？|\?|增多|减少)', head) \
                    and not _FINDING_TITLE_RE.search(head):
                continue
            title = head
        elif stripped.startswith(('●', '○', '■', '★')):
            # 2026-08-31: 圆点列表行("● 谷丙转氨酶偏高")是报告方枚举的异常条目
            # (防城港第一), 纳入兜底; 2026-09-04: ★ 同款(茂名人民"★ 血压偏低:")
            # 有效性: 含方向词/发现词/问号
            seg = re.split(r'[\s。；;，,、:：()（）]+', stripped[1:].strip(), maxsplit=1)[0]
            if not re.search(r'(偏高|偏低|偏大|偏小|偏重|偏轻|右偏|左偏|升高|降低|增高|'
                             r'增大|肥大|阳性|异常|？|\?|增多|减少)', seg) \
                    and not _FINDING_TITLE_RE.search(seg):
                continue
            title = seg
        elif stripped.startswith('【'):
            # 【】标题: 仅"发现特征词"内容参与兜底(检查项名如【血脂四项】不补)
            grp = re.findall(r'【([^】]+)】', stripped)
            grp = [g.strip() for g in grp if g.strip()]
            # 2026-08-31: 纯数字编号"【1】 甲状腺左侧叶低回声结节,C-TIRADS4a级":
            # 取【】后的首词作为标题(防城港市中报告 16 条枚举全靠 LLM, 不稳)
            if grp and re.fullmatch(r'\d{1,3}', grp[0]):
                rest = re.sub(r'^【[^】]*】\s*', '', stripped).strip()
                # 2026-09-03: 首词常是检查部位/科室("胸部:平扫:右肺…""口腔科检查:牙面…"),
                # 旧逻辑 rest[:24] 放宽导致 title="胸部" 这类垃圾补入。改为:
                # seg 自身含发现特征词才直接用作标题; 否则从词序列取首个含特征词的词片。
                seg = re.split(r'[\s。；;，,、:：()（）]+', rest, maxsplit=1)[0]
                if _FINDING_TITLE_RE.search(seg):
                    title = seg
                else:
                    probe = [w for w in re.split(r'[\s。；;，,、:：()（）]+', rest) if w]
                    cand = next((w for w in probe if _FINDING_TITLE_RE.search(w)), None)
                    if not cand:
                        continue
                    title = cand.rstrip("?？")
            elif not grp or not any(_FINDING_TITLE_RE.search(g) for g in grp):
                continue
            else:
                title = " ".join(grp)
        else:
            continue  # 无编号/无【】的行(解释/建议/页眉)不收
        if not title:
            continue
        if re.fullmatch(r'[\d\s\-\+\.%\/\*~=]+', title):
            continue
        # 2026-08-31: 检查项清单("1.【彩超腹部（肝胆胰脾）(需空腹)】")不是异常标题
        if title.startswith('【') or '】' in title:
            continue
        # 2026-08-31: 句子式标题跳过("符合轻度阻塞性睡眠呼吸暂停低通气综合征"
        # "表明有幽门螺旋杆菌感染"—— 叙述短语不是异常标题, 也不得豁免过滤)
        if _ABNORMALITY_SENTENCE_RE.match(title):
            continue
        # 分级括号清理: "甲状腺双侧叶囊性病灶(CTI-RADS 2类)" → 去分级
        title = re.sub(r'\([^)]*RADS[^)]*\)', '', title).strip()
        # 2026-09-10: 兜底标题过滤(方法名/科普定义句, 见 _is_junk_fallback_title)
        if _is_junk_fallback_title(title):
            continue
        if title not in titles:
            titles.append(title)
    return titles


# 【】标题兜底的"发现特征词"(检查项名如【血脂四项】不含这些词, 不会误补)
_FINDING_TITLE_RE = re.compile(
    r"(结节|结石|囊肿|增生|肥大|反流|返流|异常|钙化|息肉|脂肪|肿瘤|糜烂|溃疡|"
    r"硬化|狭窄|增厚|增粗|斑块|肌瘤|占位|阴影|血管瘤|超重|肥胖|消瘦|息肉|萎缩|心律不齐|血症|"
    r"沉着|光团|ST\s*段|T\s*波|疾病|改变)"
)
# 小结标题行(汉字序号/数字 + 序号标点 + 【): 桂林"五、【甲状腺彩超】"、
# 贵港"1.【彩超腹部(肝胆胰脾)(需空腹)】"。其下的内容行是分行条目。
_SUMMARY_HEAD_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]{1,3}|(?:\d\s*){1,4})\s*[、.．:：]?\s*【"
)


def _clean_summary_item_line(s: str) -> Optional[str]:
    """小结内容行清洗: 剥 (n)/星号前缀与 RADS 分级括号, 冒号后段优先, 去数值。"""
    s = re.sub(r"^[（(]\s*\d+\s*[）)]\s*", "", s.strip())
    s = re.sub(r"^[*＊]\s*", "", s)
    s = re.sub(r"[（(][^）)]*RADS[^）)]*[）)]", "", s)
    # 无括号分级("Lung-RADS 2 级"/"C-TIRADS3 类")与【参考值…】一并剥除
    s = re.sub(r"[A-Za-z]*-?RADS\s*[0-9]?\s*[级类]?", "", s)
    s = re.sub(r"【[^】]*[参]?考?[值]?[^】]*】", "", s)
    if "：" in s or ":" in s:
        tail = re.split(r"[：:]", s)[-1].strip()
        if len(tail) >= 2 and (_FINDING_TITLE_RE.search(tail)
                               or re.search(r"(偏高|偏低|升高|降低|增高|减少|增多)", tail)):
            s = tail
    s = re.sub(r"\d+(?:\.\d+)?", "", s)
    s = re.sub(r"[xX×＊*·、,，\s]+$", "", s).strip("*＊★· ")
    return s or None


def _parse_summary_item_titles(text: str) -> list:
    """小结标题(序号+【】, 桂林"五、【甲状腺彩超】")下的内容行解析 —— 确定性兜底。

    体检综述类报告把异常分行列在小结标题下(如"甲状腺左叶结节(TI-RADS 2-3 级)"/
    "甲状腺右叶结节(TI-RADS 2 级)"), 无编号、不参与编号兜底, 全靠 LLM → 偶漏。
    这里把"小结标题行后、下一标题/编号/列表行前"的内容行按发现行解析:
    含特征词(结节/钙化/囊肿…)或方向词的短行才产出; 科普/建议/正常描述被过滤。
    2026-09-03 新增(提取对账 A 步)。
    """
    titles: list[str] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not _SUMMARY_HEAD_RE.match(s):
            i += 1
            continue
        i += 1
        # 收集小结标题后的内容行, 直到遇到新标题/编号/列表/星号行或空行
        while i < len(lines):
            c = lines[i].strip()
            if not c or _SUMMARY_HEAD_RE.match(c):
                break
            if re.match(r"^(\d+[\u3001,.:.\uff09)]|【|●|○|■|[*＊])", c):
                break
            # 下一段标题("异常指标/健康建议/检查汇总" 等短行)不是小结内容
            if len(c) <= 8 and _FINDINGS_STRONG_RE.search(c):
                break
            cleaned = _clean_summary_item_line(c)
            ok = False
            if cleaned and 2 <= len(cleaned) <= 36:
                if _FINDING_TITLE_RE.search(cleaned):
                    ok = True
                elif re.search(r"(偏高|偏低|升高|降低|增高|减少|增多)$", cleaned):
                    ok = True
                if ok:
                    # 句读/分号行 = 多发现或正文句(如"支气管炎;双肺…纤维灶。"), 不整行产出
                    if re.search(r"[。；;]", cleaned):
                        ok = False
                    elif _SAFETY_NET_JUNK_RE.search(cleaned):
                        ok = False
                    elif len(cleaned) <= 40 and _NORMAL_ONLY_RE.search(cleaned):
                        ok = False
                    elif _ABNORMALITY_SENTENCE_RE.match(cleaned):
                        ok = False
            if ok and not _is_junk_fallback_title(cleaned) and cleaned not in titles:
                titles.append(cleaned)
            i += 1
    return titles


# safety-net 兜底要跳过的非异常标题(报告提示文本/非疾病条目)
# 2026-08-31: 补建议/科普特征词(H004"2.日常保健:适当加强体育锻炼"冒号后正文
# 被 keep_body_after_colon 提升为标题; "1.肾结石是尿液中...结晶体"科普句)
# 2026-09-02: "窦性心律" 后加 (?!不齐) —— "窦性心律不齐" 是真异常(陈美杉),
# 只有纯"窦性心律"(正常心律)才跳过
_SAFETY_NET_JUNK_RE = re.compile(
    r'目标体重|既往史|未完成|温馨提示|请您|建议您|注意|复查提示|检查提醒|结论分层|[ABCD]类|'
    r'指标解读|发现和其他异常|解读以下|异常结果汇总|以下按照|列出本次|'
    r'若出现|原有症状|症状加重|应及时|如有异常|出现异常症状|需要进一步|进一步诊治|需进一步|'
    r'正常心电图|窦性心律(?!不齐)|'
    r'适当|加强|控制体重|日常保健|多饮水|忌食|保持|戒烟|限酒|请到|就诊|复查|'
    r'尿液中|有机物|无机物|结晶体|沉积|'
    r'恶性征象|经病理证实|需每|(?:无|少|忌)饮食|治疗无效|无需治疗|服药|直径大于|'
    r'医院|大学|体检号|第\d+页|页共|联系电话|'
    r'是|为|建议|治疗|可(?:能(?:是|为|表现|导致|出现|以|分为|进展|发展)|以|分为|出现|导致|进展|发展)'
)


# 部位前缀白名单: prefix-recover 只接受器官/部位/方向词开头的前缀,
# 拒绝"您有/已属/是反映"等叙述前缀(H003"4:高血压 您有高血压史"会把标题扩坏)
_ANATOMY_PREFIX_RE = re.compile(
    r"^(肝|肾|胆|脾|胰|胃|肠|肺|心|脑|甲状腺|前列腺|子宫|卵巢|乳腺|睾丸|"
    r"淋巴结|颈|胸|腹|腰|颅|眼|耳|鼻|口|咽|喉|关节|脊柱|椎|骨|皮|血管|动脉|"
    r"双|左|右|上|下|外|内|前|后|中|多|弥漫|弥散|全)"
)
# 叙述/科普标记: 扩展结果含这些词时拒绝(防止扩进整句)
_ANATOMY_PREFIX_BAD_RE = re.compile(
    r"(是|为|可|有|建议|请|您|的|反映|病变|出现|检查|示|见|属|在|和|与|及|等|提示)"
)


def _recover_anatomical_prefix(text: str, item_name: str) -> Optional[str]:
    """从冒号正文中查找包含 item_name 的更长片段。

    若正文中存在"部位词 + item_name"的连续片段（如正文"肝内钙化灶0.5cm"、
    item_name"钙化灶" → 返回"肝内钙化灶"），返回该片段；否则返回原 item_name。

    2026-08-31: 扩展前缀必须是部位词开头且不含叙述词, 否则不扩展
    (防"H003 4:高血压 您有高血压史" → "您有高血压"的误扩)。
    注意：不补"血/血清/血浆/全血"等化验前缀（如"血肌酸激酶" → 保持"肌酸激酶"）。
    """
    if not item_name:
        return item_name
    for line in text.split('\n'):
        # 去掉编号前缀（如"3、"）
        line = re.sub(r'^\d+[\u3001,.:.\uff09)]?\s*', '', line.strip())
        if not line or item_name not in line:
            continue
        # 尝试每个出现位置，取"部位词+item_name"的最长扩展
        for m in re.finditer(re.escape(item_name), line):
            start = m.start()
            # 向前扩展到最近的汉字边界（停止在全角逗号/句号/数字/括号/空格等）
            prefix_start = start
            while prefix_start > 0 and '\u4e00' <= line[prefix_start - 1] <= '\u9fa5':
                prefix_start -= 1
            prefix = line[prefix_start:start]
            # 只补器官/部位前缀（如"肝内"、"甲状腺双叶多发"），跳过化验前缀
            if prefix and len(prefix) >= 1:
                if re.fullmatch(r'[血血清浆全]+', prefix):
                    continue
                if not _ANATOMY_PREFIX_RE.match(prefix):
                    continue
                # 2026-09-09: prefix 自身含叙述/科普词("肝血管瘤是一种较为常见的")
                # → 不扩回(日照 24 把"肝脏良性肿瘤"扩成整句垃圾条目; "是/为/可/有"
                # 是科普定义句特征, 真部位前缀不含)。
                if re.search(r"(电图|提示|示$|彩超|超声|口腔|X线|CT|影像|胃镜|肠镜|检查|检出|"
                             r"是|为|可|有|建议|请|属于|呈|的|和|与|及|等)", prefix):
                    continue
                if _ANATOMY_PREFIX_BAD_RE.search(line[prefix_start:m.end()][len(prefix):]):
                    continue
                return line[prefix_start:m.end()]
    return item_name


def _keep_body_after_colon(text: str) -> str:
    """冒号拆分策略：检查项前缀（冒号前）是检查方法名，正文（冒号后）才是异常发现。
    只保留正文，避免 LLM 把检查项名（如"甲状腺B 超"）当作异常或干扰部位提取。

    规则（按句号切分，冒号为第二边界）：
    1. 只处理有编号前缀的行（如"2、肺结节"），无编号行（解释/数据/建议行）丢弃
    2. 去掉以"建议"开头的句子（那是建议，不是发现）
    3. 去掉以"未检"开头的句子（如"未检项目：便潜血"，是未检说明，不是发现）
    4. 句子含冒号 → 只保留冒号后的内容（正文）
    5. 句子无冒号 → 整句保留（如"外耳道耵聍"）
    """
    out = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        # 方案4(2026-08-24): 【】标题行也是发现载体("【血脂四项】:(1)…"), 保留(冒号后正文)
        if stripped.startswith('【'):
            if '：' in stripped or ':' in stripped:
                after = re.split(r'[：:]', stripped, maxsplit=1)[1].strip()
                if after:
                    out.append(stripped.split('：')[0].split(':')[0] + ':' + after)
            else:
                out.append(stripped)
            continue
        # 2026-08-31: 编号支持半角冒号("3:内痔", H003 崇左格式; "1:胃镜..."的
        # 冒号是编号分隔符, rest 内无内容冒号 → 整句保留)
        m = re.match(r'^(\d+[\u3001,.:.\uff09)]?\s*)(.*)$', stripped)
        if not m:
            # 2026-08-28: 星号/强调标题行("*超重" "*变应性鼻炎")是报告方标记的异常条目,
            # 无编号也保留(桂林/防城港模板), 星号原样保留供 LLM 识别标题结构;
            # 2026-08-31: 圆点列表行("● 尿酸(UA)偏高")同权保留
            # (防城港第一"以下是您本次异常结果的主要部分汇总")
            if stripped.startswith(('*', '＊', '★', '●', '○', '■')):
                out.append(stripped)
            # 其余无编号行(解释/数据/建议行)丢弃
            continue
        num_prefix = m.group(1)
        rest = m.group(2)
        # 按句号/分号切成句子
        sentences = [s.strip() for s in re.split(r'[。；;]', rest) if s.strip()]
        kept = []
        for sent in sentences:
            # 建议句丢弃
            if sent.startswith('建议'):
                continue
            # 未检说明丢弃（如"未检项目：便潜血"）
            if sent.startswith('未检') or sent.startswith('未查') or sent.startswith('未做'):
                continue
            # 有冒号 → 仅当冒号前是检查方法名("甲状腺B 超:...")时取冒号后正文;
            # 否则整句保留("2.胆红素偏高:肝功能检查提示:..."的"胆红素偏高"
            # 是发现标题, 冒号后是检查方式, 取冒号后会丢标题)
            # 2026-08-31: 冒号后为空(如"1.幽门螺旋杆菌感染（14C）:")→ 保留整句
            if '：' in sent or ':' in sent:
                # 先剥"XX检查/检测/测定/试验:"中缀("肝功能检查提示:")
                stripped2 = re.sub(r'[^：:，,。]*?(?:检查|检测|测定|试验)[：:]\s*', '', sent)
                if stripped2 != sent:
                    kept.append(stripped2)
                else:
                    m2 = _CHECK_PREFIX_RE.match(sent)
                    if m2:
                        after = sent[m2.end():].strip()
                        if after:
                            kept.append(after)
                        else:
                            kept.append(sent)
                    else:
                        kept.append(sent)
            else:
                kept.append(sent)
        if kept:
            out.append(num_prefix + ' '.join(kept))
    return '\n'.join(out)


# === 2026-08-31: conclusion_text 二次清洗(存量脏段落) ===
# 旧版锚点切段会把超声/心电图【检查所见】【印 象】细节和科普段收进发现段,
# 干扰异常提取(H003 崇左碎片条目)。重新生成段落前, 对已存段落截断
# 检查细节子段落: 从"【检查所见】/【印 象】/【影像所见】/【超声提示】"起截断,
# 并丢弃"xxx 是...的基础/定义"科普句(行内"为...之一"等短句保留)。
_EXAM_SECTION_RE = re.compile(r"【\s*(?:检查所见|印\s*象|影像所见|超声提示)\s*】")
# 科普/定义句剥离(行首锚定): "肠息肉是指肠粘膜面突出的一种赘生物。"
# / "为常见的肛肠疾病,可表现为出血、脱出、肛门不适等症状。"(独立成行)
# 约束: 行首起 ≤10 字内出现谓语词(是指/乃/是/为/属), 谓语后 ≥6 字,
# 非贪婪到句号为止; 避免误删"已属超重范围。"短句。
# 跨行科普句(如"颈动脉粥样硬化是反映...。")不在此处理, 由
# _all_occurrences_in_junk_context 在提取后过滤其产物。
_SCIENCE_CLAUSE_RE = re.compile(
    r"^[^。；\n]{0,10}(?:是指|乃|是|为|属)[^。；\n]{6,80}?(?=[。；])"
)


def _clean_conclusion_section(conclusion: Optional[str]) -> Optional[str]:
    """存量 conclusion_text 二次清洗: 截断【检查所见】等检查细节子段落,
    剥离科普/定义句(LLM 提取碎片条目的来源)。"""
    if not conclusion:
        return None
    m = _EXAM_SECTION_RE.search(conclusion)
    if m:
        conclusion = conclusion[: m.start()]
    cleaned = _SCIENCE_CLAUSE_RE.sub("", conclusion)
    # 科普句剥离后残留: 行首孤立标点(如"。息肉可合并..."), 双标点
    cleaned = re.sub(r"^\s*[。；、,，]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[。；]{2,}", "。", cleaned)
    cleaned = cleaned.strip()
    return cleaned or None



# === 2026-09-08: 表格型结论段重组(厦门弘爱"三、体检异常结果及医学建议") ===
# 表 dump = [标题][体检异常结果：][我们的建议：][异常cell*][建议句][序号 n.]…
# (每视觉行 = 异常结果列(可折行) + 建议列, 序号列位于块尾)。
# 重组: 序号归位行首; 异常结果与建议之间换行(用户前端展示口径)。
def _reflow_table_conclusion(section: str) -> str:
    """表格型结论重组(厦门弘爱"三、体检异常结果及医学建议")。

    表 dump 行序 = [标题][体检异常结果：][我们的建议：][异常cell*][建议句][序号 n.]…,
    即每视觉行 = 异常结果列(可折行) + 建议列, 序号行位于**块尾**。
    重组: 去掉两列表头行; 序号 n 归位到其前方块的行首; 异常结果与建议之间换行。
    """
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    out = []
    title = ""
    buf = []

    def emit(seq, block):
        if not block:
            return
        text = "".join(block)
        si = next((k for k, ln in enumerate(block)
                   if re.match(r"^(建议|请|提示|必要时|注意|多饮水)", ln)), None)
        if si is not None:
            text = "".join(block[:si]).strip() + "\n" + "".join(block[si:]).strip()
        if seq:
            text = seq + text
        out.append(text)

    for ln in lines:
        if re.match(r"^\d{1,2}\s*[.、。]$", ln):
            emit(ln, buf)  # 块尾序号行 = 其前方内容块的编号
            buf = []
            continue
        if ln.startswith(("体检异常结果", "我们的建议")):
            continue  # 表头两列标题行丢弃
        if re.match(r"^三、体检异常结果及医学建议", ln):
            title = ln
            continue
        buf.append(ln)
    emit("", buf)  # 无后继序号的内容(表尾残留)丢弃策略: 无序号不输出
    if not out:
        return section
    return "\n".join([title] + out)


def _merge_wrapped_lines(text: str) -> str:
    """合并 PDF 折行：同一编号下的连续行拼成一段，避免 LLM 把一条结论拆成多条。

    编号行含冒号（如"3、甲状腺B 超：..."）→ 该行是主条目, 独立成行; 其后的
    独立"建议/请…"行并回该条(供 LLM 生成 suggestion), 科普解释段
    ("是…/其特点是…")不再拼入 —— 解释句拼入会让 LLM 拆出碎片条目
    (福建第二 骨密度 → "骨量减少/生物力学性能下降")。
    编号行无冒号（如"2、肺结节"）→ 该行是发现名, 后续解释段落不合并。
    """
    lines = text.split('\n')
    out = []
    buf = []
    _last_anchor = -1
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(' '.join(buf))
                buf = []
            continue
        if stripped.startswith(('●', '○', '■', '*', '＊', '★')):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            _last_anchor = -1
            continue
        if stripped.startswith('【'):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            _last_anchor = -1
            continue
        if re.match(r'^[（(]\s*\d+\s*[）)]', stripped) \
                or re.match(r'^[一二三四五六七八九十]{1,3}\s*[、.．]', stripped):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            _last_anchor = -1
            continue
        if re.match(r'^\d+[\u3001,.:.\uff09)]?\s*', stripped):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            _last_anchor = len(out) - 1
            continue
        if _last_anchor >= 0 and stripped.startswith((
                "建议", "请", "必要时", "定期", "日常", "同时", "可", "多饮水")):
            out[_last_anchor] += ' ' + stripped
            continue
        buf.append(stripped)
    if buf:
        out.append(' '.join(buf))
    return '\n'.join(out)


_CHECK_PREFIX_RE = re.compile(
    r'^[^：:，,。]*?(?:B\s*超|CT\s*平扫|CT\s*检查|CT|X\s*光|X\s*线|MRI|超声|彩超|磁共振|心电图|'
    r'大便常规|粪常规|便常规|血常规|尿常规|查体|'
    r'人体代谢率|人体成分|骨密度|动脉硬化|经颅多普勒|'
    r'检查|检测|测定)\s*[：:]\s*'
)
# 2026-09-05: "方法(+科室修饰)+提示"前缀(供 fill 标题与 LLM 名清洗共用)
# "放射科(CT)提示右肺上叶间隔旁型肺气肿"→"右肺上叶间隔旁型肺气肿"(福建第二)
_METHOD_PROMPT_RE = re.compile(
    r"^(?:彩超|超声|B\s*超|心电图|脑电图|CT|X\s*线|DR|检查|体检|口腔科?|眼科|耳鼻喉科?|"
    r"妇科|外科|内科|检验科?|碳13|胃镜|肠镜|"
    r"放射科(?:[（(][^）)]*[)）])?|医学影像科(?:[（(][^）)]*[)）])?)?"
    r"\s*(?:提示|示|检出)\s*[:：]?\s*")


def _strip_check_prefix(item_name: str) -> str:
    """剥离 LLM 可能误保留的检查项前缀，如 '甲状腺B 超：甲状腺双叶多发囊性结节' → '甲状腺双叶多发囊性结节'。"""
    item_name = item_name.strip("*＊★☆■□●○·\u3000 ")
    # 2026-09-04: OCR/复合行条目带"方法+提示"前缀("彩超提示肝囊肿"/
    # "心电图提示左心室高电压"/"口腔科提示18、28、38牙智齿")→ 保留下半发现
    item_name = _METHOD_PROMPT_RE.sub("", item_name)
    m = _CHECK_PREFIX_RE.match(item_name)
    if m:
        return item_name[m.end():]
    return item_name


# === 2026-09-10: 名称归一入口化(_write_norm / _cmp_norm) ===
# 背景: "去空白/剥检查前缀/剥括号尾缀"的能力此前分散在 term_normalizer(指标侧)
# 与结论侧多处(filter 的 flat、fill 的 seg flat、_norm_cross、store 的手写 replace), 
# 每遇到一种新形态(字距空格/全角/检查前缀)就要在多个下游分别修, 反复返工。
# 约定: **展示名/落库名保持原文**(ij.item_name 原始发现名不变), 所有"比较类"
# 逻辑(去重/覆盖/过滤对齐/跨线判重)统一调用这两个归一函数。
_CMP_FIX = {"－": "-", "（": "(", "）": ")", "：": ":", "，": ",", "．": ".",
            "；": ";", "％": "%", "　": ""}


def _write_norm(s: str) -> str:
    """书写归一: 全角标点→半角 + 去全部空白(不改变语义, 普通报告等价原样)。"""
    if not s:
        return ""
    for a, b in _CMP_FIX.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s)


def _cmp_norm(name: str) -> str:
    """比较归一名(入口化): 书写归一 + 剥方法/检查前缀 + 剥括号内容与"结论/测定"
    尾缀 + 去尾数字符号。方向词与程度词保留(调用方按需再剥)。展示/落库名不变。"""
    s = _write_norm(name)
    if not s:
        return ""
    s = _strip_check_prefix(s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"结论|测定", "", s)
    s = re.sub(r"[\d.]+[＋+±~～↑↓]?$", "", s)
    s = re.sub(r"[＋+±~～↑↓]*$", "", s)
    return s.strip()


def _norm_cross(s: str) -> str:
    """跨线去重名称规范化: 剥括号单位缩写("尿酸(UA)")/结论后缀/尾缀数字符号
    ("尿蛋白1+")/方向词已在调用方剥(has_dir 时传入 stem)。"""
    s = _cmp_norm(s)
    s = re.sub(r"^(?:血|血清|血浆)", "", s)
    return s.strip()


def _is_cross_dup(item_name: str, anom_names: set) -> bool:
    """结论条目与指标线黄/红名称的跨线去重判定(纯函数, 秒级单测覆盖)。

    - 超重/体重指数 结论: 仅指标名含"身高体重指数"/"BMI"(崇左式)才拦
      (2026-09-09 用户拍板: "体重指数"/"体质指数"放行 —— 27 齐鲁"超重"与
      指标"体重指数 26.37"并存、马鞍山"肥胖+体质指数"同口径)。
    - 其余: 原名相等/互含 → 拦; 共享前 5 字变体 → 拦; 方向词条目的词根
      (≥3字)互含/字符交集(≥3) → 拦; 规范化(剥括号/尾缀)后 ≥2 字相同,
      或 ≥3 字双向互含 → 拦("尿蛋白1+" vs "尿蛋白(PRO)"; 防 2 字核心词
      "尿素"误拦"碳13尿素呼气试验阳性")。
    """
    cross_dup = False
    if not item_name:
        return False
    stem = re.sub(r"(增高|偏高|降低|偏低|升高|下降|增多|减少)$", "", item_name)
    has_dir = stem != item_name
    cmp_name = _norm_cross(stem if has_dir else item_name)
    # 2026-09-12(用户验收更新): "超重"不再因指标"身高体重指数"拦截(崇左式旧
    # 口径作废) —— 结论区应显示"超重"; "体重指数"与"身高体重指数"的互含仍拦。
    if "身高体重指数" in item_name:
        cross_dup = any(n in {"超重", "肥胖", "体重指数"} for n in anom_names)
    else:
        for n in anom_names:
            if not n:
                continue
            if n == item_name or n in item_name or item_name in n:
                cross_dup = True
                break
            # 共享前缀变体(结论"乙肝两对半(第1,2,4,5项阳性)" vs 指标"乙肝两对半结论")
            nn = re.sub(r"[（(].*?[)）]|结论|测定", "", n)
            ii = re.sub(r"[（(].*?[)）]|结论|测定", "", item_name)
            if len(nn) >= 5 and len(ii) >= 5 and nn[:5] == ii[:5]:
                cross_dup = True
                break
            # 方向词条目("低密度脂蛋白增高")与指标名词根匹配
            if has_dir and len(stem) >= 3 and (stem in n or n in stem):
                cross_dup = True
                break
            # 规范化互比(≥2 字相同, 或 ≥3 字双向互含)
            nn2 = _norm_cross(n)
            if cmp_name and len(cmp_name) >= 2 and len(nn2) >= 2 \
                    and (cmp_name == nn2
                         or (len(cmp_name) >= 3 and cmp_name in nn2)
                         or (len(nn2) >= 3 and nn2 in cmp_name)):
                cross_dup = True
                break
            # 字符交集兜底("谷丙转氨酶偏高" vs "谷草/谷丙"), 阈值 3
            if has_dir and len(stem) >= 3 and len(set(stem) & set(n)) >= 3:
                cross_dup = True
                break
    return cross_dup


def _store_abnormalities(db, report_id: int, interpretation_id: int,
                         abnormalities: list[dict]) -> None:
    """将结论提取的异常项写入 report_indicator + indicator_judgment。

    每次解析时检查 interpretation_id 是否已有结论型异常，有则跳过（增量追加，不重复）。
    """
    from app.modules.report.models import ReportIndicator
    from app.modules.interpretation.models import IndicatorJudgment
    from sqlalchemy import text

    if not abnormalities:
        return

    # 检查是否已存在该 interpretation 的结论型异常（通过 report_indicator.raw_text 非空来识别）
    existing = db.execute(
        text("""SELECT COUNT(*) FROM indicator_judgment ij
                JOIN report_indicator ri ON ij.indicator_id = ri.id
                WHERE ij.interpretation_id = :iid AND ri.raw_text IS NOT NULL"""),
        {"iid": interpretation_id},
    ).scalar()
    if existing:
        _log.debug("abnormalities already stored for interp=%d, skip", interpretation_id)
        return

    for item in abnormalities:
        item_name = (item.get("item_name") or "").strip()
        suggestion = (item.get("suggestion") or "").strip()
        deviation = item.get("deviation")
        is_urgent = item.get("is_urgent", False)

        if not item_name and not suggestion:
            continue

        # 标准化名仅用于 disease_mapping 链接，不覆盖 item_name
        llm_normalized = (item.get("item_normalized") or "").strip()
        # === STRATEGY:v2026-08-15-normalization-table 归一化表优先 ===
        # 结论条目落库名: 归一化表命中→稳定标准名(展示+映射共用一套名字);
        # 未命中→回退现有逻辑(映射表兜底 → LLM 名 → 原文)。
        # 2026-09-02: 回退分支改为直接落原文名 —— _normalize_abnormality 的
        # disease_mapping 模糊匹配/自动插入会命中历史 LOCAL 垃圾映射
        # ("血脂异常"→'脂'、"混合性高脂血症"→'改善混合性高脂血症'),
        # 污染 item_name_standard → risk 固化出垃圾 disease_hit。
        # 结论名本身已是发现/疾病名, 无需再经映射表改名。
        # 回退: 删除本分支即恢复旧行为。
        tn_standard, _ = normalize_item_name(item_name)
        if tn_standard and tn_standard != item_name.replace(" ", "").replace("　", ""):
            normalized = tn_standard
        else:
            normalized = item_name
        # === END STRATEGY ===

        # 同一 interpretation 内去重（按归一化名）
        dup = db.execute(
            text("""SELECT COUNT(*) FROM indicator_judgment ij
                    JOIN report_indicator ri ON ij.indicator_id = ri.id
                    WHERE ij.interpretation_id = :iid AND ij.item_name = :nm AND ri.raw_text IS NOT NULL"""),
            {"iid": interpretation_id, "nm": item_name},
        ).scalar()
        if dup:
            _log.debug("abnormality dup skip interp=%d item=%s", interpretation_id, normalized)
            continue

        # === STRATEGY:v2026-08-16-cross-line-dedup 跨线去重 ===
        # 同一异常(如"体重指数>24")既出现在查体指标线(report_indicator)又出现在
        # 总检建议结论线, 落库会重复。此处按**原始名**与指标线比对:
        # 指标线 item_name 或 item_name_standard 命中即跳过结论落库。
        # 2026-08-31: 名称变体也判重复(结论"肌酸激酶" vs 指标"血肌酸激酶"),
        # 精确相等或一方包含另一方(两端均≥3字)即拦。
        # 2026-08-31: 语义等价映射 —— 结论"超重/肥胖/体重指数"与指标线
        # "身高体重指数/BMI"是同一发现(崇左报告重复显示), 互相替换判重。
        # 2026-08-31: **只对指标线异常行生效**(judgment 黄/红) —— 绿色指标
        # ("体重指数" 25.5 正常、"右侧耳前瘘管鼻" 无异常)不拦结论条目
        # (广西人民"超重"、防城港中"右侧耳前瘘管" 应展示)。
        # 2026-09-09: anom_rows 限定 raw_text IS NULL(仅指标行) —— 结论 raw 行
        # 自带黄/红 judgment, 不限会把上一轮结论行混入 anom_names, 造成同源回环。
        # 2026-09-09: 名称规范化比较: 指标名带括号单位缩写("尿酸(UA)"/"尿蛋白(PRO)"),
        # 结论名带尾缀("尿蛋白1+"/"尿酸升高")。比较前剥括号内容/尾缀/方向词,
        # 归一后 ≥2 字相同或互含即拦 —— "尿酸"仅 2 字, 旧 len(stem)>=3 全跳
        # (厦门弘爱 尿蛋白1+/尿酸升高 双双漏拦)。
        # 回退: 删除本段即恢复旧行为。
        anom_rows = db.execute(
            text("""SELECT ri.item_name, ri.item_name_standard FROM report_indicator ri
                    JOIN indicator_judgment ij ON ij.indicator_id = ri.id
                    WHERE ri.report_id = :rid
                      AND ij.color_level IN ('yellow', 'red')
                      AND ri.raw_text IS NULL"""),
            {"rid": report_id},
        ).fetchall()
        anom_names = set()
        for _nm, _std in anom_rows:
            if _nm:
                anom_names.add(_nm)
            if _std:
                anom_names.add(_std)

        cross_dup = _is_cross_dup(item_name, anom_names)
        if cross_dup:
            _log.info("abnormality cross-line dup skip report=%d item=%s", report_id, item_name)
            continue
        # === END STRATEGY ===

        # 创建 report_indicator 占位行：原文名存储，标准化名存 item_name_standard
        ri = ReportIndicator(
            report_id=report_id,
            item_name=item_name,
            item_name_standard=normalized,
            raw_text=item_name,
        )
        db.add(ri)
        db.flush()  # 拿到 ri.id

        # 创建 indicator_judgment
        # === 2026-08-25: 展示名与引擎匹配名分离 ===
        # ij.item_name 存**原始发现名**(返回给用户的总检建议异常, 如"肺结节""窦性心律不齐"),
        # 不做别名/疾病名映射 —— 映射结果(肺癌(疑似)/心律失常)只进 disease_hit, 不返回用户。
        # engine 匹配用双键: j.item_name(原始) + ri.item_name_standard(normalized, 映射入口), 不丢命中。
        ij = IndicatorJudgment(
            interpretation_id=interpretation_id,
            indicator_id=ri.id,
            item_name=item_name,
            result_value=None,
            deviation=deviation if deviation else None,
            color_level="red" if is_urgent else "yellow",
            source="conclusion",
            explanation=item_name,
            suggestion=suggestion,
        )
        db.add(ij)

    # 结论型内部去重：同一 interpretation 内新存储的结论项互相比对，模糊匹配则统一 disease_mapping
    if len(abnormalities) > 1:
        from app.modules.interpretation.service import _fuzzy_overlap, _link_disease_mapping
        for i in range(len(abnormalities)):
            for j in range(i + 1, len(abnormalities)):
                a_name = abnormalities[i].get("item_name", "")
                b_name = abnormalities[j].get("item_name", "")
                if a_name and b_name and _fuzzy_overlap(a_name, b_name):
                    _link_disease_mapping(db, a_name, b_name)

    db.commit()
    _log.info("abnormalities stored report=%d interp=%d count=%d",
              report_id, interpretation_id, len(abnormalities))


def _normalize_abnormality(db, item_name: str, llm_normalized: Optional[str] = None) -> Optional[str]:
    """查 disease_mapping 表，返回标准化 disease_name。
    优先精确匹配，其次用 _fuzzy_overlap 模糊匹配已有条目（避免创建重复映射）。
    找不到时自动创建新条目。

    === STRATEGY:v2026-07-30-colon 冒号策略配套 ===
    llm_normalized: LLM 的 item_normalized。自动插入新条目时，若 LLM 扩展了名称
    （llm_normalized 比 item_name 长，如 "甲状腺囊性结节" > "囊性结节"），
    用 LLM 的长名建条目，避免新库首次遇到该词时建成短名。
    回退: 调用方传 None 即恢复旧行为。
    === END STRATEGY ===
    """
    from sqlalchemy import text
    from app.modules.interpretation.service import _fuzzy_overlap
    import re as _re

    # === STRATEGY:v2026-08-04-nospace 去空格 ===
    # 与 term_normalizer 保持一致：标准化名统一去空格（"腹部B 超"→"腹部B超"）
    item_name = item_name.replace(" ", "").replace("　", "")
    core = _re.sub(r'(偏高|偏低|偏大|偏小|偏重|偏轻|异常|检查|显示|可见)+$', '', item_name).strip()
    if not core:
        return None

    # === 2026-08-27: 垃圾名防护: 无汉字/单位类名称不得匹配或自动建映射 ===
    # 背景: 结论全文(锚点越界)导致 LLM 提取碎片条目('-06-19'、'824'、'次/分'),
    # 旧 worker 直接自动建了 LOCAL 映射, 污染 disease_mapping/disease_hit。
    if not _re.search(r'[\u4e00-\u9fa5]', core):
        return None
    if _re.search(r'(次/分|次每分钟|次每秒|mmHg|kg|cm|ml|mm|g/L|mg/L|umol|mmol|'
                  r'岁|编号|号码|电话|热线|微信|二维码)', core):
        return None

    try:
        # 1. 精确匹配
        row = db.execute(text(
            "SELECT id, item_name_standard, disease_name FROM disease_mapping WHERE enabled=1 AND (item_name_standard=:exact OR disease_name=:exact) LIMIT 1"
        ), {"exact": core}).fetchone()
        if row:
            return row[2]

        # 2. 前缀剥离后匹配
        core_stripped = _re.sub(r'^(血|血清|血浆|全血)', '', core).strip()
        if core_stripped != core:
            row = db.execute(text(
                "SELECT disease_name FROM disease_mapping WHERE enabled=1 AND (item_name_standard=:cs OR disease_name=:cs) LIMIT 1"
            ), {"cs": core_stripped}).fetchone()
            if row:
                return row[0]

        # 3. _fuzzy_overlap 模糊匹配已有条目
        candidates = db.execute(text(
            "SELECT id, item_name_standard, disease_name FROM disease_mapping WHERE enabled=1 ORDER BY id"
        )).fetchall()
        # === 2026-08-27: 指标名不当疾病 ===
        # 名称(剥前缀后)已是报告指标标准名 → 是检验指标而非疾病, 不建映射
        # (防止"血肌酸激酶"类结论条目把指标名写成 LOCAL 疾病映射)
        row = db.execute(text(
            "SELECT id FROM report_indicator WHERE item_name_standard IN (:n1, :n2) LIMIT 1"
        ), {"n1": core, "n2": core_stripped if core_stripped else core}).fetchone()
        if row:
            return None
        best = None
        best_len = 999
        for cid, cstd, cdn in candidates:
            if _fuzzy_overlap(core, cstd) or _fuzzy_overlap(core, cdn) or \
               (core_stripped and (_fuzzy_overlap(core_stripped, cstd) or _fuzzy_overlap(core_stripped, cdn))):
                if len(cstd) < best_len:
                    best = cdn
                    best_len = len(cstd)
        if best:
            return best

        # 4. 未找到 → 创建新映射
        # === STRATEGY:v2026-07-30-colon ===
        # 自动插入用 LLM 扩展名（若更长），否则用剥前缀后的短名
        if llm_normalized and len(llm_normalized) > len(item_name):
            disease = llm_normalized
        else:
            disease = core_stripped if core_stripped else core
        # === END STRATEGY ===
        db.execute(text(
            "INSERT INTO disease_mapping (item_name_standard, item_name, disease_name, disease_category, sort_code) "
            "VALUES (:std, :orig, :dn, 'OTHER', 200) "
            "ON DUPLICATE KEY UPDATE disease_name=VALUES(disease_name)"
        ), {"std": disease, "orig": item_name, "dn": disease})
        db.commit()
        return disease
    except Exception:
        pass
    return None


def _clean_unit_name(unit_name: Optional[str]) -> Optional[str]:
    """剥离机构名后缀，只保留医院名。
    规则：去掉"健康管理中心""体检中心""医疗中心"等后缀。
    "北京医院健康管理中心" → "北京医院"；"北京友谊医院" 不变。
    """
    if not unit_name:
        return None
    import re
    cleaned = re.sub(
        r'(健康管理中心|健康管理部|健康管理|体检中心|体检部|医疗中心|医院集团|门诊部|有限公司)+$',
        '', unit_name.strip(),
    ).strip()
    return cleaned or None


def process_task(db: Session, task_id: int, hospital_id: str,
                 batch_id: Optional[str] = None,
                 file_id: Optional[str] = None,
                 batch_hospital_id: Optional[str] = None):
    task = get_task_status(db, task_id)
    if not task:
        return

    task.status = "parsing"
    db.commit()

    try:
        user_dir = os.path.dirname(task.original_file_path)

        if task.file_type == "image":
            processed_path, error_msg = preprocess(task.original_file_path, user_dir)
            if error_msg:
                task.status = "failed"
                task.error_message = error_msg
                db.commit()
                return
        else:
            processed_path = task.original_file_path

        report_raw_text = None
        images_b64 = None  # for VLM conclusion extraction on image-based reports

        # 2026-09-12: 无文本层 PDF(纯扫描件)先试 hybrid OCR(逐页 8006):
        # 提文成功 → 走文本链(锚点切段 + 规则指标); 失败(OCR 不可用等)保持原
        # VLM 链。钦州中此前走 VLM 把"医院简介"当结论段(用户验收: 结论段错误/
        # 总检建议没提取到)。
        _pdf_text_probe = None
        if task.file_type == "pdf" and not _pdf_has_text(processed_path):
            try:
                _probe = _extract_pdf_text(processed_path, hybrid=True)
            except Exception as e:
                _log.warning("scanned pdf hybrid probe failed task=%s: %s", task_id, e)
                _probe = ""
            if len(_probe or "") > 200:
                _pdf_text_probe = _probe
        # For text-based PDFs, use direct text extraction + LLM parsing
        if task.file_type == "pdf" and (_pdf_has_text(processed_path) or _pdf_text_probe):
            # 2026-09-03: hybrid —— 文本页直接取, 图片结论页走 OCR 补充
            text = _pdf_text_probe if _pdf_text_probe else _extract_pdf_text(processed_path, hybrid=True)
            report_raw_text = text
            # === 2026-09-03: 医院模板档案 —— visual_sort 模板(多栏混排)需按
            # 视觉坐标重排文本流, 否则默认流乱序(目录与详情交错)。
            match_profile, _ = _load_report_profiles()
            profile = match_profile(text)
            compiled = _compile_profile_re(profile)
            if profile.get("visual_sort"):
                text = _extract_pdf_text(processed_path, visual_sort=True)
                report_raw_text = text
            # === 2026-08-25: 指标提取器开关 INDICATOR_EXTRACTOR=signal|llm ===
            # signal(默认): 规则提取(信号行标记 signal_flag=1, 绿区照常提取供解读参考)
            #   - 异常信号(红字/↑↓/异常词) → signal_flag=1, run_rules 强制黄/复核
            #   - 无 LLM, 秒级, 消除 LLM 提取不稳定(梧州 67vs226)
            # llm: 旧方案(LLM 提取全部指标), 出问题切回
            # 回退: export INDICATOR_EXTRACTOR=llm 即恢复
            if os.getenv("INDICATOR_EXTRACTOR", "signal") == "signal":
                from app.modules.report.table_extractor import (
                    extract_indicator_rows,
                    extract_abnormal_signals,
                    col_rows_with_fallback,
                    extract_personal_info,
                )
                # 2026-08-31: 指标解析前挖掉结论段(体检结果综述/总检建议/异常结果汇总),
                # 防止结论条目被误提取进指标线 —— 崇左"甲状腺结节/右肾囊肿/谷丙转氨酶偏高"、
                # 防城港"红细胞计数增多/高尿酸血症" 因此混入指标线, 前端同名剔除结论后
                # 用户看到"漏了总检异常"或指标线显示无参考范围的结论条目。
                findings_sec = _locate_findings_sections(
                    text, extra_break_re=compiled["extra_break_re"],
                    extra_skip_re=compiled["extra_skip_re"],
                    extra_anchor_re=compiled["extra_anchor_re"])
                if findings_sec and len(findings_sec) > 50:
                    text = text.replace(findings_sec, "")
                rows = extract_indicator_rows(text)
                # 2026-08-28: 列式表格(广西"项目名称|检查结果|单位|参考范围|提示")
                # 标志权威 —— 提示列异常标志 signal_flag=3 强制黄, 弃检不入库
                col_rows = col_rows_with_fallback(processed_path, text)
                signals = extract_abnormal_signals(processed_path)
                # 2026-08-28: 列式报告(表格有提示列异常标志)抑制 word/arrow/red 通道 ——
                # 综述异常词配对(残缺重复)、箭头跨块错配(↑向上找名称跨块)、红字样式
                # 配对(提示列值误配名称)都会产生垃圾; 标志权威原则下表格(提示列/标志列)
                # 已覆盖, 无需行式通道补充
                # 2026-09-02: 改为**按行**抑制 —— 全局抑制会误杀序号制表格的 ↑ 信号
                # (步新宇: 眼压"弃检"行 signal=3 曾让全报告箭头通道失效,
                #  同型半胱氨酸/肌酸激酶/游离PSA 的 ↑ 全部丢失判绿)。
                # 仅丢弃"该行已被列式表格覆盖"的行式信号; 其余行式信号保留。
                col_flag_keys = {
                    (r["item_name"], r["result"]) for r in col_rows if r.get("signal_flag") == 3
                }
                if col_flag_keys:
                    signals = [
                        s for s in signals
                        if (s["item_name"], s["result"]) not in col_flag_keys
                    ]
                signal_names = {(s["item_name"], s["result"]) for s in signals}
                # signal_flag: 2=箭头(语义标记, 强制黄), 1=红字/异常词(样式, ref复核),
                #              3=列式提示列异常标志(权威, 强制黄)
                arrow_keys = {
                    (s["item_name"], s["result"]) for s in signals if s.get("signal") == "arrow"
                }
                # 2026-09-07: 反序表(齐鲁青岛"异常标识"列)/双值表(厦门弘爱"本次/上次结果")
                # 由块级解析器(extract_column_table_rows)整块接管 —— 表内名称权威,
                # 行式/信号通道对表区的错配(齐鲁 (X,"-") 海量、弘爱参考列当值)全部抑制。
                auth_names = {r["item_name"] for r in col_rows if r.get("__auth")}
                indicators = []
                seen_sig = set()
                col_keys = set()
                # 2026-08-29: 行式行的标志可能高于列式错配行(桂林"结果高" f=3 vs
                # 列式同 key f=0) → 列式行 flag 不足时用行式的
                row_flag_map = {
                    (r["item_name"], r["result"]): r.get("signal_flag") or 0 for r in rows
                }
                row_by_key = {(r["item_name"], r["result"]): r for r in rows}
                # 列式行优先(带 ref/unit/flag), 行式行补漏
                for r in col_rows:
                    sig_key = (r["item_name"], r["result"])
                    col_keys.add(sig_key)
                    r = dict(r)
                    r.pop("__auth", None)  # 仅作抑制标记, 不落库
                    if (r.get("signal_flag") or 0) < row_flag_map.get(sig_key, 0):
                        r["signal_flag"] = row_flag_map[sig_key]
                    # 2026-09-05: word/arrow 综述信号提升 —— 列式行提示列无标志(flag0)
                    # 但综述/小结标"偏高"时(德宏 LDL 4.23 col 无 H, 小结有"偏高")应判黄
                    sig_from_signal = (
                        (2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0))
                        if r["item_name"] not in auth_names else 0
                    )
                    if (r.get("signal_flag") or 0) < sig_from_signal:
                        r["signal_flag"] = sig_from_signal
                    # 2026-09-05: 列式行 ref/unit 缺失时用行式同 key 行补齐
                    # (德宏 BA% "≤1"/BA# "≤0.06" 单限参考在 reflow 后只有行式拿得到)
                    if r["item_name"] not in auth_names:
                        _rr = row_by_key.get(sig_key)
                        if _rr:
                            if (not r.get("ref_low") and not r.get("ref_high")) \
                                    and (_rr.get("ref_low") or _rr.get("ref_high")):
                                r["ref_low"], r["ref_high"] = _rr.get("ref_low"), _rr.get("ref_high")
                            if not r.get("unit") and _rr.get("unit"):
                                r["unit"] = _rr.get("unit")
                    indicators.append(r)
                for r in rows:
                    sig_key = (r["item_name"], r["result"])
                    if r["item_name"] in auth_names:
                        continue  # 2026-09-07: 块级表区由 col(权威)覆盖, 行式不参与
                    if sig_key in col_keys:
                        continue
                    # 2026-08-29: 行式行自带 signal_flag("结果高/低"标志)优先保留
                    sig = r.get("signal_flag", 0) or (
                        2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0)
                    )
                    if sig and sig_key in seen_sig:
                        continue
                    if sig:
                        seen_sig.add(sig_key)
                    # 2026-09-02: 保留行式提取的 ref(序号制表格"值/单位/参考范围"走行式,
                    # ref 曾被硬编码丢弃 → 陈美杉/步新宇指标无参考范围)
                    indicators.append({
                        "item_name": r["item_name"],
                        "result": r["result"],
                        "unit": r.get("unit", ""),
                        "ref_low": r.get("ref_low"),
                        "ref_high": r.get("ref_high"),
                        "signal_flag": sig,
                    })
                for s_key in signal_names:
                    if s_key[0] in auth_names:
                        continue  # 2026-09-07: 块级表区信号由 col flag 承担
                    if s_key not in seen_sig and s_key not in col_keys:
                        indicators.append({
                            "item_name": s_key[0], "result": s_key[1],
                            "unit": "", "ref_low": None, "ref_high": None,
                            "signal_flag": 2 if s_key in arrow_keys else 1,
                        })
                # 2026-09-05: 定性测定("…测定（定性）")结果"阳性" = 报告方 * 标志异常
                # (福建第二乙肝五项) → 至少黄区注意级
                for _ind in indicators:
                    if _ind.get("signal_flag"):
                        continue
                    if "定性" in (_ind.get("item_name") or "") and str(_ind.get("result", "")).startswith("阳性"):
                        _ind["signal_flag"] = 1
                indicators = normalize_indicators(indicators)
                personal_info = extract_personal_info(text)
                # 2026-08-26: 姓名无效(表头词)或字段缺失 → LLM 按字段补齐(不覆盖已有正确值)
                from app.modules.report.table_extractor import _HEADER_WORD_RE as _hdr_re
                name_bad = (not personal_info.get("name")) or bool(
                    personal_info.get("name") and _hdr_re.match(personal_info["name"])
                )
                if name_bad or not personal_info.get("gender") or not personal_info.get("report_date"):
                    try:
                        parsed = _parse_text_with_llm(text)
                    except Exception:
                        parsed = {}
                    for k in ("name", "gender", "age", "report_date", "unit_name"):
                        v = parsed.get(k)
                        if v and not personal_info.get(k):
                            personal_info[k] = v
            else:
                parsed = _parse_text_with_llm(text)
                personal_info = {
                    "name": parsed.get("name"),
                    "gender": parsed.get("gender"),
                    "age": parsed.get("age"),
                    "check_date": parsed.get("report_date"),
                    "unit_name": parsed.get("unit_name"),
                }
                # LLM already returns ref_low/ref_high — normalize names
                raw_indicators = parsed.get("indicators", [])
                indicators = normalize_indicators([
                    {
                        "item_name": ind.get("item_name", ""),
                        "result": ind.get("result", ""),
                        "unit": ind.get("unit", ""),
                        "ref_low": ind.get("ref_low"),
                        "ref_high": ind.get("ref_high"),
                        "category": ind.get("category"),
                    }
                    for ind in raw_indicators
                ])
            # === END ===
            # 2026-09-12: 纯扫描 PDF(hybrid 探针提文)的**指标区**改用 VLM 提取 ——
            # OCR 表格文本的规则提取不可用(钦州中指标 0 黄回归); VLM 提指标沿用
            # 旧链质量, 结论段仍是 hybrid 文本链(锚点切段, VLM 曾把医院简介当结论)。
            if _pdf_text_probe:
                try:
                    _vlm_imgs = _file_to_base64_list(processed_path, task.file_type)
                    _vlm_res = vlm_client.extract_from_images(_vlm_imgs)
                    _vlm_inds = normalize_indicators(_vlm_res.get("indicators", []))
                    if _vlm_inds:
                        # 2026-09-12: VLM 与规则(OCR 文本)指标并集 —— VLM 提干指标,
                        # 规则补漏(钦州中"血小板平均体积(MPV)"VLM 漏提)
                        _have = {re.sub(r"\s+", "", x.get("item_name", "")) for x in _vlm_inds}
                        for _r in indicators:
                            _k = re.sub(r"\s+", "", _r.get("item_name", ""))
                            if _k and _k not in _have:
                                _vlm_inds.append(_r)
                                _have.add(_k)
                        indicators = _vlm_inds
                    _pi = _vlm_res.get("personal_info") or {}
                    if _pi.get("name") and not (personal_info or {}).get("name"):
                        personal_info = _pi
                except Exception as e:
                    _log.warning("scanned pdf VLM indicators failed task=%s: %s", task_id, e)
        else:
            images_b64 = _file_to_base64_list(processed_path, task.file_type)
            result = vlm_client.extract_from_images(images_b64)
            indicators = normalize_indicators(result.get("indicators", []))
            personal_info = result.get("personal_info", {})
            report_raw_text = result.get("raw_text", "")

        # Update existing report_info (created in create_task), or create if missing
        report = db.query(ReportInfo).filter(ReportInfo.task_id == task.id).first()
        if not report:
            report = ReportInfo(task_id=task.id, user_id=task.user_id)
            db.add(report)
        # 归属锚定名:name 仅在空时回填(单份上传已写入登录账号锚定名,不覆盖)
        if not report.name:
            report.name = personal_info.get("name")
        # 展示名:parsed_name 始终取 PDF 解析出的真实姓名
        report.parsed_name = personal_info.get("name")
        report.gender = personal_info.get("gender")
        report.age = personal_info.get("age")
        # LLM 路径返回 report_date，VLM 路径返回 check_date
        report.report_date = personal_info.get("check_date") or personal_info.get("report_date")
        # report.check_type = personal_info.get("check_type")
        # === STRATEGY:v2026-08-04-unitname 提取体检机构名 ===
        # 从解析结果写入机构名（LLM/VLM 均已在 prompt/正则中支持提取）
        report.unit_name = _clean_unit_name(personal_info.get("unit_name"))
        # === END STRATEGY ===
        db.commit()
        db.refresh(report)

        from app.core.indicator_groups import normalize_panel
        # Extract conclusion text from report raw content
        if images_b64 is not None:
            # Image-based: use VLM to extract conclusion directly from images
            try:
                conclusion = vlm_client.extract_conclusion_from_images(images_b64)
                if conclusion:
                    # 2026-09-03: 图片型结论与文本型走同一展示重排 —— VLM/OCR 文本
                    # 可能带 markdown # 前缀, 先按行剥除再 reflow(钦州中同款)。
                    clean_lines = [re.sub(r"^\s*#{1,6}\s*", "", ln) for ln in conclusion.splitlines()]
                    polished = _reflow_conclusion_lines("\n".join(clean_lines))
                    if polished:
                        conclusion = polished
                    report.conclusion_text = conclusion
                    db.commit()
                    _log.info("conclusion extracted via VLM report=%d len=%d", report.id, len(conclusion))
            except Exception as e:
                _log.warning("VLM conclusion extraction failed report=%d: %s", report.id, e)
        elif report_raw_text and len(report_raw_text) > 100:
            # Text-based PDF: use LLM on extracted full text
            try:
                conclusion = run_async(_extract_conclusion_async(report_raw_text, compiled))
                if conclusion:
                    report.conclusion_text = conclusion
                    db.commit()
                    _log.info("conclusion extracted via LLM report=%d len=%d", report.id, len(conclusion))
            except Exception as e:
                _log.warning("LLM conclusion extraction failed report=%d: %s", report.id, e)

        for ind in indicators:
            # 2026-08-31: LLM 可能输出 ref_low/ref_high="无"/"" (单限指标), 存库前归一为 None
            def _clean_ref(v):
                if v is None:
                    return None
                s = str(v).strip()
                return s if s and s != "无" else None
            db.add(ReportIndicator(
                report_id=report.id,
                item_name=_clean_indicator_name(ind.get("item_name", "")),
                item_name_standard=_clean_indicator_name(ind.get("item_name_standard")),
                item_code=ind.get("item_code"),
                result_value=ind.get("result"),
                unit=ind.get("unit"),
                ref_range_low=_clean_ref(ind.get("ref_low")),
                ref_range_high=_clean_ref(ind.get("ref_high")),
                category=normalize_panel(ind.get("category")),
                raw_text=ind.get("raw_text"),
                signal_flag=ind.get("signal_flag", 0),
            ))
        db.commit()

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        # task.priority 已是 DB int (0/1/100),TaskMessage 需要 str priority 路由
        publish_priority = {0: "normal", 1: "urgent", 100: "bulk"}.get(task.priority or 0, "normal")
        payload = {"report_id": report.id, "hospital_id": hospital_id}
        if batch_id is not None:
            payload["batch_id"] = batch_id
        if file_id is not None:
            payload["file_id"] = file_id
        if batch_hospital_id is not None:
            payload["batch_hospital_id"] = batch_hospital_id
        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=publish_priority,
            payload=payload,
        ))

    except Exception as e:
        task.retry_count += 1
        task.error_message = str(e)
        if task.retry_count >= 3:
            task.status = "failed"
        else:
            task.status = "queued"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
        # 重试决策交给 worker(走 publish_retry 延迟)
        raise


def _pdf_has_text(file_path: str) -> bool:
    """Check if PDF has enough embedded text for direct extraction."""
    try:
        import fitz
        doc = fitz.open(file_path)
        total = sum(len(page.get_text().strip()) for page in doc)
        doc.close()
        return total > 200  # 200+ chars → text-based PDF
    except Exception:
        return False


def _extract_pdf_text(file_path: str, visual_sort: bool = False, hybrid: bool = False) -> str:
    """Extract all text from a text-based PDF.

    2026-09-03: visual_sort=True 时按页视觉坐标(y,x)重排 —— 多栏混排模板
    (防城港市中医医院)默认文本流乱序, 重排后按阅读序输出。
    2026-09-03: hybrid=True 时对"文本极少(<100字)且含多张图片"的页做 OCR 补充
    (福建第二等: 文本化验页 + 图片结论页的混合型 PDF) —— 页级失败不影响整体。
    """
    import fitz
    doc = fitz.open(file_path)
    texts = []
    for i, page in enumerate(doc):
        if visual_sort:
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (round(b[1] / 10), b[0]))
            t = "\n".join(b[4].strip() for b in blocks if b[4].strip())
        else:
            t = page.get_text().strip()
        # 2026-09-12: 纯扫描页(几乎无文本 + 单张大图)此前因"≥2 图"条件被漏
        # (钦州中医整份扫描件 hybrid 提取 0 字, 结论段无法定位) → "多图"或
        # "文本 <10 字的单图页"均触发 OCR
        if hybrid and len(t) < 100 and (
                len(page.get_images()) >= 2 or len(t.strip()) < 10):
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
                img = base64.b64encode(pix.tobytes("png")).decode()
                r = vlm_client.extract_from_image(img)
                ocr_t = (r.get("raw_text") or "").strip()
                if len(ocr_t) > len(t):
                    # 2026-09-12: OCR markdown 标记清理 —— MedGo 对 "### 1.【…】"
                    # 标题符与 "$13\times10mm$" LaTeX 包裹提取质量差(钦州中条目
                    # 大段缺失+科普碎片) → 去标题符/数学符, \times 转 ×
                    ocr_t = re.sub(r"(?m)^\s*#{1,6}\s*", "", ocr_t)
                    ocr_t = ocr_t.replace("\\times", "×")
                    ocr_t = re.sub(r"\$([^$\n]*)\$", r"\1", ocr_t)
                    t = ocr_t
            except Exception as e:
                _log.warning("hybrid OCR page %d failed: %s", i + 1, e)
        if t:
            texts.append(f"--- Page {i+1} ---\n{t}")
    doc.close()
    return "\n\n".join(texts)


def _parse_text_with_llm(text: str) -> dict:
    """Send extracted PDF text to LLM for indicator parsing."""
    return run_async(_parse_text_with_llm_async(text))


async def _parse_text_with_llm_async(text: str) -> dict:
    """实际 async 解析，包裹在 medgo_sem 内。"""
    from app.ai.llm import get_chat_model, _guarded
    prompt = _build_parse_prompt(text)
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=16384)

    resp = (await _guarded(_call())).content
    return _parse_llm_json(resp)


def _build_parse_prompt(text: str) -> str:
    from app.core.indicator_groups import PANEL_HINTS
    allowed = "\n   - ".join(["", *PANEL_HINTS])
    return f"""从以下体检报告文本中提取信息，返回 JSON 格式（不要 Markdown 代码块）：

{{
  "name": "姓名",
  "gender": "男或女",
  "age": 年龄数字或null,
  "report_date": "YYYY-MM-DD或null",
  "unit_name": "体检机构名称（如XX医院、XX医院健康管理中心）或null",
  "indicators": [
    {{"item_name": "指标名称", "result": "检测结果", "unit": "单位", "ref_low": "参考下限", "ref_high": "参考上限", "category": "所属栏目"}}
  ]
}}

规则：
1. 姓名从"尊敬的XXX先生/女士"或"姓名:XXX"提取
2. 性别："先生"→男，"女士"→女
3. 年龄：从"XX岁"提取数字
4. 参考范围如"3.5-9.5"→ref_low="3.5", ref_high="9.5"；如"<5.0"→ref_low="", ref_high="5.0"
5. 只提取化验指标数据（血常规、生化、免疫等），不提取问卷、个人信息
6. **排除"总检建议与结论"段落**：不要提取"总检建议与结论""总检结论""医师建议""健康指导"等结论段落中的任何内容——那里的"XXX偏高/偏低"是结论文本，不是化验指标。真正的化验指标必须有检测数值（数字）和参考范围，或出现在化验数据表中
7. unit_name 从"XX医院""XX医院健康管理中心""XX体检中心"等提取机构名，只要医院名（如"xxx医院"），不要"健康管理中心"后缀；找不到填 null
8. 每条指标必须给 category：该指标所属栏目，只能取下面列出的取值中最接近的一项，不能自创、不能附加说明文字：
{allowed}
   表格上方的栏目标题通常已给出栏目，如"尿常规"、"血常规（体检）,糖化血红蛋白"（一个标题含多个栏目时按各指标归属拆标：血常规行→血常规，全血糖化血红蛋白测定→糖化血红蛋白）；找不到任何合适栏目时 category 填 null
9. 没有的字段填 null

体检报告文本：
{text[:24000]}
"""


def _parse_llm_json(resp: str) -> dict:
    import json, re
    from json_repair import repair_json
    match = re.search(r'\{[\s\S]*\}', resp)
    if not match:
        raise ValueError(f"LLM did not return valid JSON: {resp[:200]}")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        data = json.loads(repair_json(match.group()))
    for ind in data.get("indicators", []):
        ref = ind.pop("ref_range", None)
        if ref and "ref_low" not in ind:
            from app.core.vlm_client import _parse_ref_range
            lo, hi = _parse_ref_range(str(ref))
            ind["ref_low"] = lo
            ind["ref_high"] = hi
    return data


def _file_to_base64_list(file_path: str, file_type: str) -> list[str]:
    if file_type == "image":
        with open(file_path, "rb") as f:
            return [base64.b64encode(f.read()).decode()]
    elif file_type == "pdf":
        import fitz
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(base64.b64encode(pix.tobytes("jpg")).decode())
        doc.close()
        return images
    elif file_type == "docx":
        raise ValueError("DOCX parsing not yet supported via VLM — use text extraction instead")
    else:
        raise ValueError(f"Cannot convert file_type={file_type} to images")


def list_reports(db: Session, hospital_id: str, user_id: Optional[str] = None,
                 name: Optional[str] = None,
                 page: int = 1, page_size: int = 20) -> tuple:
    from sqlalchemy.orm import joinedload
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
        if name:
            q = q.filter(ReportInfo.name == name)
    if not user_id:
        # 医生/管理员全量视图:隐藏 parse 失败行与「无任何可展示内容」的空壳残留行。
        q = (
            q.outerjoin(ReportTask, ReportTask.id == ReportInfo.task_id)
            .filter(
                or_(ReportTask.status.is_(None), ReportTask.status != "failed"),
                or_(ReportInfo.parsed_name.isnot(None),
                    ReportInfo.name.isnot(None),
                    ReportInfo.report_date.isnot(None)),
            )
        )
    total = q.count()
    items = q.order_by(ReportInfo.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # Attach task status to each report
    task_ids = [r.task_id for r in items if r.task_id]
    if task_ids:
        tasks = {t.id: t for t in db.query(ReportTask).filter(ReportTask.id.in_(task_ids)).all()}
    else:
        tasks = {}
    # Attach interpretation status (latest report_interpretation per report)
    report_ids = [r.id for r in items]
    if report_ids:
        from app.modules.interpretation.models import ReportInterpretation
        interps = {}
        for ri in db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id.in_(report_ids)
        ).order_by(ReportInterpretation.id.desc()).all():
            interps.setdefault(ri.report_id, ri)
    else:
        interps = {}
    results = []
    for r in items:
        task = tasks.get(r.task_id)
        interp = interps.get(r.id)
        # 展示名:解析出真实姓名优先;解析中(未完成)不泄露账号锚定名→空;
        # 已完成但未抽出姓名(旧数据/无姓名 PDF)→回退归属锚定名。
        if r.parsed_name:
            display_name = r.parsed_name
        elif task and task.status in ("queued", "parsing"):
            display_name = None
        else:
            display_name = r.name
        results.append({
            "id": r.id,
            "task_id": r.task_id,
            "name": display_name,
            "gender": r.gender,
            "age": r.age,
            "report_date": r.report_date,
            "check_type": r.check_type,
            "unit_name": r.unit_name,
            "task_status": task.status if task else None,
            "interp_status": interp.status if interp else None,
            "overall_level": interp.overall_level if interp else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return results, total


def get_report_detail(db: Session, report_id: int) -> Optional[ReportInfo]:
    return db.query(ReportInfo).filter(ReportInfo.id == report_id).first()


# === 2026-09-01: 绿区展示层垃圾过滤 ===
# 指标解析会把页眉/电话/日期/拼接残段误提取为"绿色"指标(崇左"日期=2026"、
# "提示身高=187"、"米=正常"、"光法)=0.54")。以下规则**只作用于绿区条目**
# (signal_flag=0 且 judgment 非黄/红), 黄/红区指标与总检异常(raw_text 非空)完全跳过。
# 硬垃圾: 页眉页脚/电话/日期/括号残段/单位碎片/结构词
_GREEN_HARD_JUNK_RE = re.compile(
    r"(电话|传真|邮编|地址|日期|时间|编号|流水|门诊|科室|咨询|报告单|温馨提示|"
    r"健康热线|联系电话|普通外科|胸外科|泌尿外|骨外|神经外|午\d{1,2}|077\d|"
    r"姓名|性别|年龄|套餐|次数|页|您的|属于|体检报告|健康体检|^小结$|^备注$|^提示$|"
    r"^个/|^镜检)"
)
# 值侧电话/长数字: "胸外科=0771" "7955892"(7+位数字即电话)
_GREEN_VALUE_PHONE_RE = re.compile(r"^\d{7,}$|^\d{3,4}-\d{3,}$|^077\d")
# 括号残段("光法)") / 纯符号数字 / 单字单位碎片("米")
_GREEN_FRAGMENT_RE = re.compile(
    r"^[\d\s\-+./%()（）、，,。:：;；~～]+$|[)）]$|^[a-zA-Z]{1,3}$|^(?:米|cm|mm|kg|g)$"
)
# 名称污染的结果词(清洗后缀用): "皮肤正常"→"皮肤"、"口吃未发现"→"口吃"、
# "白细胞未见"→"白细胞"(尿镜检结果"未见")
_GREEN_RESULT_WORD_RE = re.compile(
    r"(未见明显异常|未见异常|未触及|无肿大|未发现|无畸形|活动正常|无异常|未见|正常)$"
)
_GREEN_PREFIX_RE = re.compile(r"^(提示|异常)")
# 2026-09-02: 弃检/未检/放弃类非指标行(任何区都滤; 眼压"弃检"被列式误提为行)
_ABANDON_ITEM_RE = re.compile(r"(弃检|未检|拒检|放弃|无法完成)")


def _clean_green_indicator(item_name: str, result_value: str) -> Optional[str]:
    """绿区条目清洗: 硬垃圾返回 None(整条过滤); 名称污染返回清洗后的名称; 正常返回原名称。"""
    name = (item_name or "").strip()
    value = (result_value or "").strip()
    if not name:
        return None
    if _GREEN_HARD_JUNK_RE.search(name):
        return None
    if _GREEN_VALUE_PHONE_RE.match(value):
        return None
    if _GREEN_FRAGMENT_RE.match(name) or (
        name.endswith((")", "）")) and "(" not in name and "（" not in name
    ):
        return None
    cleaned = _GREEN_RESULT_WORD_RE.sub("", name)
    if cleaned != name:
        if len(cleaned) < 2:
            return None  # "齿正常"→"齿" 剥完过短 → 滤
        return cleaned
    cleaned = _GREEN_PREFIX_RE.sub("", name)
    return cleaned or None


def get_report_indicators(db: Session, report_id: int) -> List[ReportIndicator]:
    return db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()
