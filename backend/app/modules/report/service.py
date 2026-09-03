import asyncio
import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator
from app.core.vlm_client import vlm_client
from app.core.term_normalizer import normalize_indicators, normalize_item_name
from app.core.image_preprocess import preprocess
from app.core.rabbitmq import rabbitmq, TaskMessage

_log = logging.getLogger("app.parse")


def create_task(db: Session, hospital_id: str, user_id: int, file_path: str,
                filename: str, file_type: str, file_size: int,
                thumbnail_path: Optional[str] = None,
                priority: str = "normal",
                batch_id: Optional[str] = None,
                file_id: Optional[str] = None) -> ReportTask:
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
    report = ReportInfo(task_id=task.id, user_id=user_id)
    db.add(report)
    db.commit()

    payload = {"task_id": task.id, "hospital_id": hospital_id, "file_path": file_path}
    if batch_id is not None:
        payload["batch_id"] = batch_id
    if file_id is not None:
        payload["file_id"] = file_id
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
    s = ln.strip()
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
_FINDINGS_STOP_BREAK_RE = re.compile(
    r"(主检医生|主检医师|总检医生|总检医师|审核医生|审核医师|录入者|"
    r"初审医生|初审医师|初审日期|终审医生|终审医师|终审日期|"
    r"报告日期|总检日期|"
    r"---\s*Page|Page\s*\d+\s*/\s*\d+|扫描参数|影像所见|诊断意见|"
    r"检查科室)"
)
# 2026-09-03: 建议段后的签名/分检报告起点行 —— 庞海锋(钦州第二)建议段后跟
# "初检:/吴净瑛 2026-08-14 总检:/主检:" 签名与"体 格 检 查/检 验 报 告"
# 分检报告, 无断点会整段混入结论。整行命中即断(仅作用于当前锚点段)。
_FINDINGS_STOP_BREAK_RE3 = re.compile(
    r"^\s*初检[:：]?|^\s*主检[:：]|^\s*初审[:：]|^\s*终审[:：]|^\s*复检[:：]|"
    r"^\s*体\s*格\s*检\s*查\s*$|^\s*检\s*验\s*报\s*告|^\s*分科检查报告\s*$"
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
    r"第\s*\d+\s*/\s*\d+\s*页|第\s*\d+\s*页|^\d{1,4}\s*/\s*\d{1,4}$|^\d{1,4}\s*页$|"
    r"体检编号[:：]|体检号[:：]|流水号[:：]|姓名[:：]|性别[:：]|年龄[:：]|单位[:：]|体检号码[:：]|"
    r"体检日期[:：]|检查日期[:：]|报告日期[:：]|打印日期[:：]|体检次数[:：]|次数[:：]|门诊号[:：]|"
    r"地址[:：]|邮编[:：]|传真[:：]|接收日期[:：]|报告时间[:：])"
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
    r"^(?:医生|医师|检查者|操作者|技师|初评医生|主检医师)\s*[:：]|彩超室|心电图室|超声科|放射科|"
    r"病理诊断|无痛胃镜|无痛肠镜|胃镜报告|肠镜报告|检查结论|病理报告|影\s*像\s*报\s*告|检查部位|"
    r"全国HR|^大便常规(?:[\s（(]|$)|^血常规$|^尿常规[+＋]|^隐血试验$|^尿沉渣|"
    r"^(?:上皮细胞|白细胞团|管型|酵母菌|小圆上皮|蜡样管型|草酸钙结晶|颗粒管型|透明管型|滴虫|精子|尿酸结晶)|^注[:：]|制片|染色检测|"
    r"^(?:颈动脉|甲状腺|腹部|心脏|泌尿系|肝胆胰脾)彩超|超声测值|检查所见[:：]"
)
_NUMBERED_LINE_RE = re.compile(r"^\d{1,3}[\u3001,.:.\uff09)]\s*(?:[\u4e00-\u9fa5*＊★【]|[A-Za-z])")
_NORMAL_ONLY_RE = re.compile(r"未见(明显)?(异常|分流|液性|暗区|肿块|占位|出血|钙化|囊肿|结节|结石)|无异常(发现)?|未见异常回声|阴性")
# 签名行: 层2 回溯锚点的起点
_FINDINGS_SIGN_RE = re.compile(
    r"^(主检医生|总检医生|主检医师|总检医师|检查医生|审核医生|报告医师|医师签名|主检:)"
)


def _locate_findings_sections(text: str) -> Optional[str]:
    """从全文定位"发现段": 收集所有锚点段(锚点行到下一锚点/广告词), 页眉去重, 限长。未命中 None。

    层1: 标题模式正则(整行由医学词+结论词组成且含强词);
    层2(2026-08-28): 签名行回溯 —— 标题模式未命中时, 定位主检/总检医生签名行,
      向上回溯找"短标题行"(≤15字、非句尾、含强词)作为锚点。
    """
    lines = [re.sub(r"^\s*#{1,6}\s*", "", ln) for ln in text.splitlines()]
    # 2026-09-03: 锚点判定对行首 markdown(#) / 列表符(-/•)不敏感 ——
    # VLM/OCR 产物(钦州中"## 本次体检结论及健康指导意见:")也命中。
    def _norm_anchor(ln: str) -> str:
        return re.sub(r"^[-*•·\s]+", "", ln.strip())

    idxs = [i for i, ln in enumerate(lines) if _is_findings_anchor(_norm_anchor(ln))]
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
        seen: set = set()  # 2026-09-03: 去重按段隔离 —— 贵港"异常指标"表与
        # "健康建议"段标题行文本相同, 全局去重会把健康建议条目标题全吞
        for ln in lines[start:end]:
            s = re.sub(r"^\s*#{1,6}\s*", "", ln).strip()
            if not s:
                continue
            if _FINDINGS_STOP_BREAK_RE.search(s):
                break
            if _FINDINGS_STOP_BREAK_RE2.match(s):
                break
            if _FINDINGS_STOP_BREAK_RE3.match(s):
                break
            # 2026-08-31: 页脚页眉(广告/页码/体检号)仅跳过该行, 内容可跨页继续
            if _FINDINGS_STOP_SKIP_RE.search(s):
                continue
            # 2026-09-03: 段内页眉(健康管理中心抬头/电话/号码)整行跳过
            if _FINDINGS_STOP_SKIP_RE2.search(s):
                continue
            # 2026-09-03: 逐行页眉(柳州等: 机构抬头/号码+姓名/性别男/年龄/体检号)
            if _FINDINGS_STOP_SKIP_RE3.search(s):
                continue
            # 2026-09-03: VLM/OCR HTML 表格/图片标签行整行跳过
            if _FINDINGS_HTML_RE.match(s):
                continue
            # 小结标题行(汉字序号"五、【甲状腺彩超】")是发现段正常结构:
            # 不是细节段起点, 且细节段内遇到它即恢复收集。
            if _SUMMARY_TITLE_RE.match(s):
                skip_detail = False
            # 2026-08-31: 检查细节子段落(超声/心电报告)跳过, 遇编号条目恢复
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
        r"^【|^[*＊★●○■]|^\d{1,3}\s*[\u3001,.:.．\uff09)]?\s*[\u4e00-\u9fa5A-Za-zα-ωΑ-Ωγ(（*＊★]"
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
        if summary_head.match(s) or title_head.match(s):
            flush()
            out.append(s)  # 小结标题行独立(其下内容行逐行独立)
            prev = "summary"
            continue
        if item_head.match(s):
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


async def _extract_conclusion_async(text: str) -> Optional[str]:
    """提取报告结论/发现段。

    方案4(2026-08-24): 确定性锚点切段优先(免一次 LLM 调用); 锚点未命中
    才回退 LLM 找段落(no_think, 提取类任务禁用思考)。
    """
    section = _locate_findings_sections(text)
    if section and len(section) > 50:
        return _reflow_conclusion_lines(section)[:16000]
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


async def _extract_abnormalities_async(conclusion_text: str) -> list[dict]:
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

    model = get_chat_model(no_think=True)
    items: list[dict] = []

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
            if not isinstance(item, dict) or not item.get("item_name"):
                continue
            item["item_name"] = _strip_check_prefix(str(item["item_name"]))
            item.setdefault("suggestion", "")
            item.setdefault("deviation", None)
            item.setdefault("is_urgent", False)
            out.append(item)
        return out

    for chunk in _chunk_text(text, 8000):
        prompt = _ABNORMALITY_PROMPT.format(text=chunk)
        try:
            resp = await _guarded(_call_one(prompt))
            items.extend(_parse_content(resp.content))
        except Exception as e:
            _log.warning("Failed to extract abnormalities (chunk len=%d): %s", len(chunk), e)
    if not items:
        return []
    items = _mark_urgent(items)
    # === STRATEGY:v2026-08-03-prefix-recover 部位补全(确定性, 无 LLM) ===
    # 从冒号正文中查找包含 item_name 的更长片段(含解剖部位前缀), 补全 item_name。
    # 方案4: item_normalized 已移除, recover 无条件执行。
    for item in items:
        item_name = item.get("item_name", "")
        enriched = _recover_anatomical_prefix(text, item_name)
        if enriched and enriched != item_name:
            item["item_name"] = enriched
            _log.info("prefix-recover: %s -> %s", item_name, enriched)
    # === STRATEGY:v2026-08-17-numbered-item-safety-net 编号条目兜底校验 ===
    # LLM 逐条提取偶发漏条目(prompt 已要求"逐条核对", 实测仍会漏,
    # 如陈美杉报告中漏"4、胆囊结节")。确定性兜底: 从结论文本解析
    # 编号条目标题, 提取结果中无任何项与标题匹配(相等或互相包含)时
    # 自动补一条, item_name 用标题原文(后续 prefix-recover/归一化照常走)。
    # 回退: 删除本段即恢复旧行为。
    existing_names = [it.get("item_name", "") for it in items]
    for title in _parse_numbered_titles(text):
        if not title:
            continue
        # === STRATEGY:v2026-08-18-safety-net-junk-filter 非异常标题过滤 ===
        # 兜底会把报告提示文本误当异常条目补上(如"您的目标体重64.3公斤.../
        # 既往史/部分体检项目未完成")。标题命中以下模式则跳过。
        # 回退: 删除本段即恢复旧行为。
        if _SAFETY_NET_JUNK_RE.search(title):
            _log.info("safety-net skip junk title: %s", title)
            continue
        # === END STRATEGY ===
        # 2026-08-31: covered 判定 —— 碎片(过短)不算覆盖:
        # LLM 把"慢性萎缩性胃炎"拆成"胃炎/慢性/萎缩性"时, "胃炎"⊂标题
        # 曾导致长标题不补。仅当 existing 条目长度接近标题(差≤2字)或
        # 标题⊂条目时才视为已覆盖。
        covered = any(
            t and (t == title or title in t or (t in title and len(t) + 3 > len(title)))
            for t in existing_names
        )
        if not covered:
            _log.info("safety-net fill: %s", title)
            items.append({
                "item_name": title,
                "suggestion": "",
                "deviation": None,
                "is_urgent": False,
                # 2026-08-31: 兜底条目来源是确定性编号标题, 豁免 junk-context 过滤
                # (如"4:高血压 您有高血压史,建议您..."整行被判科普而误杀)
                "_safety_net": True,
            })
    # === END STRATEGY ===
    items = _filter_junk_abnormalities(items, source_text=text)
    # === 2026-09-03: 问号候选链合并(H004 钦州第二"右肾强光团,钙化灶?结石?" /
    # 钦州中"左肾内强回声团(钙化灶?)") ===
    # 报告方对主发现的鉴别诊断以"X?Y?" 短候选形式紧跟主实体(逗号/问号/括号直接
    # 相连, 无空白分隔), LLM 会将其拆成独立条目("钙化灶"多出一条)。处理: 候选名
    # 若与链成员一致且其所在链紧跟在某个主实体后 → 删除候选条目。链前必须有紧邻
    # 汉字/逗号/顿号/**左括号**(钦州中"…强回声团(钙化灶?)"),
    # "颈部淋巴结?" 这类自身带部位的独立可疑发现不在此列。
    def _merge_candidate_chain(source: str, item_list: list[dict]) -> list[dict]:
        chain_re = re.compile(r"([\u4e00-\u9fa5]{2,8}[?？]){1,3}")
        drop_names: set = set()
        # 链紧邻前置实体判定: 链开始位置前的字符是汉字/中文逗号/顿号/左括号
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

    items = _merge_candidate_chain(text, items)
    # === 2026-09-03: 左右叶/双侧对称补全(H004 桂林体检综述小结五:
    # "甲状腺左叶结节(TI-RADS 2-3 级)"/"甲状腺右叶结节(TI-RADS 2 级)" 分行并列,
    # LLM 偶只提一叶)。行清洗分级括号后仅"左/右"不同即视为孪生行;
    # 现有条目已覆盖其中一侧时, 确定性补另一侧(不依赖 LLM)。
    def _fill_symmetric_sides(source: str, item_list: list[dict]) -> list[dict]:
        cleaned_lines = set()
        for ln in source.splitlines():
            c = re.sub(r"[（(][^）)]*RADS[^）)]*[）)]", "", ln.strip()).strip()
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

    items = _fill_symmetric_sides(text, items)
    # === 2026-09-03: 科室/检查项名垃圾条目剔除(H004 梧州 LLM 提取"身高/内科/外科/眼科") ===
    _NONABNORM_NAME_RE = re.compile(
        r"^(身高|体重|心率|脉搏|呼吸|体温|血压|收缩压|舒张压|腰围|臀围|"
        r"内科|外科|眼科|五官科|口腔科|耳鼻咽喉科|耳鼻喉科|妇科|男科|检验科|"
        r"放射科|超声科|彩超室|心电图室|病理科|一般检查|肛检|舌象|脉象|"
        r"矫正视力(?:\(左\)|\(右\)|（左）|（右）)?|既往史|家族史|现病史|婚育史|过敏史|"
        r"体检|总检|报告|科室|项目)$"
    )
    items = [it for it in items if not _NONABNORM_NAME_RE.match((it.get("item_name") or "").strip())]
    return items


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
# LLM 提取的"科普/超声描述/测量分级"类伪条目, 用特征词确定性剔除(不依赖 LLM 自觉):
#   "常见于甲状腺炎""升高提示肝细胞有损害""一项或一项以上即""左侧叶内混合回声团"
#   "甲状腺结节TI一RADS" 等。真条目(甲状腺结节/脂肪肝/龋齿…)不含这些词, 不受影响。
_JUNK_ITEM_RE = re.compile(
    r"(常见于|多见于|可表现为|可出现|出现于|考虑为|提示|建议|复查|随访|咨询|"
    r"一项|以上即|部分指标|饮食|运动|药物|饮酒|"
    r"造成|导致|使|可能性|中风|血栓|梗塞|血管壁|血压升高|"
    r"所致|引起|症状|为上|为特征|是以|可导致|一系列|"
    r"呼气试验|血流|大小约|直径|×|mm|cm|TI|RADS|[ABCD]类|"
    r"次/分|Kg|kg/m|\^|/L|\d+岁|体检数据|数据为|类圆形|不规则形|直径≤|密度增高|"
    r"性质待查|待查|"
    r"【|】|彩超|心电图|MR平扫|CT平扫|TCD|动脉硬化检测|经颅多普勒|口腔检查|"
    r"正常|年\d+月\d+日|"
    r"温馨提示|健康热线|紧急|异常结果|分层|健康教育|既往史|按具体执行)"
)
# 2026-08-31: 句子式/描述性条目(LLM 从科普叙述提取的碎片):
# 以"为/属/呈/符合/表明/提示/考虑"等开头的叙述短语(H003"为缺血性 心脑血管病"
# "符合轻度阻塞性睡眠呼吸暂停低通气综合征""表明有幽门螺旋杆菌(HP)感染"),
# 以及独立建议残留短词("多饮水"/"适当"等)
_ABNORMALITY_SENTENCE_RE = re.compile(
    r"^(?:为|属|呈|符合|表明|提示|考虑|怀疑|是|可能|有|见|发现|出现|存在|"
    r"已属|目前|此次|患者|建议|请|适当|减少|加强)[\u4e00-\u9fa5A-Za-z()（）·\-/ ]{2,}"
    r"|^(?:多饮水|忌|注意)$"
)


def _filter_junk_abnormalities(items: list[dict], source_text: str = "") -> list[dict]:
    # 2026-08-31: 编号标题(确定性来源)豁免科普/句子式过滤:
    # LLM 提取"4:高血压"标题时, 所在行含"建议您/饮食/复查"等科普标记会被
    # junk-context 误杀; 标题是报告方标注的异常条目本体, 直接豁免。
    net_titles = set(_parse_numbered_titles(source_text)) if source_text else set()
    # 2026-08-31: 椎间盘/椎管狭窄条目拆分与归一(LLM 偶合并为一条):
    # "颈3/4、颈5/6 椎间盘向后突出,颈3/4 水平椎管狭窄" → 椎间盘突出 + 水平椎管狭窄;
    # "椎间盘向后突出" → "椎间盘突出"。拆分条目标记 _split, 豁免"不在原文"检查。
    expanded: list[dict] = []
    for it in items:
        name = it.get("item_name", "")
        if not name:
            continue
        if "椎间盘" in name and "椎管狭窄" in name:
            expanded.append({**it, "item_name": "椎间盘突出", "_split": True})
            expanded.append({**it, "item_name": "水平椎管狭窄", "_split": True})
        elif "椎间盘" in name:
            name2 = re.sub(r"^(?:颈\d(?:/\d+)?、?|腰\d(?:/\d+)?、?|胸\d(?:/\d+)?、?)+", "", name)
            name2 = re.sub(r"(向后|向前|向侧|向后侧|外侧|中央|旁中央|脱出)", "", name2)
            name2 = re.sub(r"(?:椎间盘)(?:膨出|突出|脱出).*", "椎间盘突出", name2)
            expanded.append({**it, "item_name": name2, "_split": True})
        elif "椎管狭窄" in name:
            expanded.append({**it, "item_name": "椎管狭窄", "_split": True})
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
        if len(name) <= 1:
            continue
        # 2026-08-31: 编号标题豁免不含"正常性条目"(窦性心律/正常心电图是正常描述)
        if is_title and _SAFETY_NET_JUNK_RE.search(name):
            _log.info("title normal-item filtered: %s", name)
            continue
        if re.fullmatch(r'[\d\s\-\+\.%\/\*~=～]+', name):
            continue
        # 2026-08-31: junk-item 硬特征(呼气试验/彩超/建议等)即使标题也滤
        # (兜底标题"碳尿素呼气试验提示"由编号行解析出, 仍是检查方法)
        if _JUNK_ITEM_RE.search(name):
            _log.info("junk item filtered: %s", name)
            continue
        # 2026-08-31: 句子式条目(以"为/符合/表明/建议"等开头的叙述短语)
        # 真异常名以名词开头(甲状腺结节/超重), 不会以这些词开头
        if not is_title and _ABNORMALITY_SENTENCE_RE.match(name):
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
        if re.fullmatch(r"(?:颈|腰|胸)\d+(?:/\d+)?(?:、?(?:颈|腰|胸)\d+(?:/\d+)?)*", name):
            _log.info("spine-position fragment filtered: %s", name)
            continue
        # 2026-08-28: 正常描述不得当异常条目(超声/心电"未见明显液性暗区/未见分流信号")
        if re.search(r"未见(明显)?(异常|分流|液性|暗区|肿块|占位|出血|钙化)|无异常|未见异常回声", name):
            _log.info("normal-description filtered: %s", name)
            continue
        if not is_title and source_text and _all_occurrences_in_junk_context(source_text, name):
            _log.info("junk-context item filtered: %s", name)
            continue
        # 2026-08-31: 条目不在原文 → LLM 幻觉补全("十二指肠溃"→"十二指肠溃疡"),
        # 滤掉; 用归一前名检查(幽门螺杆菌 归一不误杀); 编号标题/兜底/拆分条目不受影响
        if not is_title and not it.get("_split") and source_text \
                and raw_name not in source_text and name not in source_text:
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
        if name in seen_names:
            continue
        seen_names.add(name)
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
        if any(
            other != name and len(name) + 2 <= len(other) and name in other
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
    r"(常见于|多见于|可出现|可导致|所致|是指|属于|是[一-龥]{1,12}(所致|引起|造成的)|"
    r"等[,，]|为一|为上|以.{0,6}为特征|建议|复查|随访|治疗|预防|饮食|运动|"
    r"可发展为|会在此基础上|可评估|可表现为|可进展|可能发展|如果|若发展|可合并|"
    r"可发现|彩超|颈动脉内膜|斑块形成|狭窄程度|重要危险因素|反映全身|的[一-龥]{1,6}之一|"
    r"尿液中|有机物|无机物|沉积|结晶体|形成不溶于|常见引起|生理性因素|等原因|多无症状|"
    r"无需要处理|无需处理|不建议|不影响健康|引起|诱发|可以导致|会导致|导致的|"
    r"性质待查)"
)


def _all_occurrences_in_junk_context(text: str, name: str) -> bool:
    """item_name 在原文的所有出现位置, 所在句都含科普标记 → True(科普叙述名词)。

    豁免: 条目作为"*标题行"独立出现时(如"*超重"), 视为报告方标记的异常条目, 不滤。
    """
    import re as _re
    sentences = _re.split(r'[。！？;；\n]', text)
    hits = [s for s in sentences if name in s]
    if not hits:
        return False
    # 变体标题行豁免: "*超重" / "【超重】" 独立行(长度短且以 * 或编号开头)
    for s in hits:
        s2 = s.strip()
        if _re.match(r'^[*＊★]\s*' + _re.escape(name) + r'$', s2):
            return False
        if _re.match(r'^\d+\s*[、.．]\s*' + _re.escape(name) + r'$', s2):
            return False
    return all(_JUNK_CONTEXT_RE.search(s) for s in hits)


# 编号标题兜底: 首词为检查方法时跳过该词取次词(异常名)
# ("3:肠镜 内痔"→"内痔", "6:心电图 左心室高电压"→"左心室高电压")
_EXAM_NAME_RE = re.compile(
    r"^(胃镜|肠镜|肠镜|B\s*超|超声|彩超|心电图|脑电图|肌电图|CT|MRI|X\s*光|X\s*线|DR|"
    r"TCD|动脉硬化|经颅多普勒|人体代谢率|人体成分|骨密度|口腔检查|耳鼻喉|妇科|前列腺检查|"
    r"颈动脉彩超|甲状腺彩超|腹部彩超|泌尿系彩超|心脏彩超|肝胆胰脾彩超|检查|检测|测定)$"
)


def _parse_numbered_titles(text: str) -> list:
    """从结论文本解析编号条目标题, 如 "4、胆囊结节" → "胆囊结节"。

    只取编号行(如 "4、xxx"), 忽略无编号行; 标题取编号后、冒号/句号前的内容。
    方案4(2026-08-24):
    - 纯数字/符号行(表格数据"8""-140")跳过
    - 【】标题兜底: 【甲状腺结节】【血脂异常】 等"发现特征词"标题行也解析
      (防 LLM 漏提取超声/总检发现, 如崇左【甲状腺结节】)
    """
    titles = []
    for line in (text or "").split('\n'):
        stripped = line.strip()
        # 2026-08-31: 支持 "3:内痔" 半角冒号编号(H003 崇左格式)
        m = re.match(r'^\d+[\u3001,.:.\uff09)]?\s*(\S.*)$', stripped)
        if m:
            # 2026-08-31: 只取编号后的"首词/次词"(到空白或标点前), 避免把
            # 叙述整行当标题(H003"4:高血压 您有高血压史...");
            # 首词是检查方法("胃镜/肠镜/心电图/B超/CT...")时取次词(异常名)
            head = m.group(1).strip()
            seg = re.split(r'[\s。；;，,、:：()（）]+', head, maxsplit=1)[0]
            if _EXAM_NAME_RE.match(seg):
                seg = re.split(r'[\s。；;，,、:：()（）]+', head, maxsplit=1)
                seg = seg[1].strip() if len(seg) > 1 and seg[1].strip() else seg[0]
                seg = re.split(r'[\s。；;，,、:：()（）]+', seg, maxsplit=1)[0]
            title = seg
        elif stripped.startswith(('●', '○', '■')):
            # 2026-08-31: 圆点列表行("● 谷丙转氨酶偏高")是报告方枚举的异常条目
            # (防城港第一), 纳入兜底; 有效性: 含方向词/发现词/问号
            seg = re.split(r'[\s。；;，,、:：()（）]+', stripped[1:].strip(), maxsplit=1)[0]
            if not re.search(r'(偏高|偏低|升高|降低|增高|异常|？|\?|增多|减少)', seg) \
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
        if title not in titles:
            titles.append(title)
    return titles


# 【】标题兜底的"发现特征词"(检查项名如【血脂四项】不含这些词, 不会误补)
_FINDING_TITLE_RE = re.compile(
    r"(结节|结石|囊肿|增生|肥大|反流|异常|钙化|息肉|脂肪|肿瘤|糜烂|溃疡|"
    r"硬化|狭窄|增厚|斑块|肌瘤|占位|阴影|血管瘤|超重|肥胖|消瘦|息肉|萎缩|心律不齐|血症|"
    r"沉着|光团)"
)


# safety-net 兜底要跳过的非异常标题(报告提示文本/非疾病条目)
# 2026-08-31: 补建议/科普特征词(H004"2.日常保健:适当加强体育锻炼"冒号后正文
# 被 keep_body_after_colon 提升为标题; "1.肾结石是尿液中...结晶体"科普句)
# 2026-09-02: "窦性心律" 后加 (?!不齐) —— "窦性心律不齐" 是真异常(陈美杉),
# 只有纯"窦性心律"(正常心律)才跳过
_SAFETY_NET_JUNK_RE = re.compile(
    r'目标体重|既往史|未完成|温馨提示|请您|建议您|注意|复查提示|检查提醒|结论分层|[ABCD]类|'
    r'正常心电图|窦性心律(?!不齐)|'
    r'适当|减少|加强|控制体重|日常保健|多饮水|忌食|保持|戒烟|限酒|请到|就诊|复查|'
    r'尿液中|有机物|无机物|结晶体|沉积|是|为|可|建议|治疗'
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
    r"(是|为|可|有|建议|请|您|的|反映|病变|出现|检查|示|见|属|在|和|与|及|等)"
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


def _merge_wrapped_lines(text: str) -> str:
    """合并 PDF 折行：同一编号下的连续行拼成一段，避免 LLM 把一条结论拆成多条。

    编号行含冒号（如"3、甲状腺B 超：..."）→ 后续行是折行/建议，合并到该行。
    编号行无冒号（如"2、肺结节"）→ 该行是发现名，后续行是解释段落，不合并。
    """
    lines = text.split('\n')
    out = []
    buf = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(' '.join(buf))
                buf = []
            continue
        # 2026-08-31: 圆点/星号列表行("● 谷丙转氨酶偏高""*超重")独立成行
        if stripped.startswith(('●', '○', '■', '*', '＊', '★')):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            continue
        # 2026-08-31: 【】标题行独立成行(H004"【1】 胸部:..."是发现载体,
        # 不得与后续行合并, 否则 keep_body_after_colon 的句子切分失效)
        if stripped.startswith('【'):
            if buf:
                out.append(' '.join(buf))
                buf = []
            out.append(stripped)
            continue
        # 2026-08-31: 编号支持半角冒号("3:内痔", H003 崇左格式)
        if re.match(r'^\d+[\u3001,.:.\uff09)]?\s*', stripped):
            if buf:
                out.append(' '.join(buf))
            if '：' in stripped or ':' in stripped:
                buf = [stripped]
            else:
                out.append(stripped)
                buf = []
        else:
            buf.append(stripped)
    if buf:
        out.append(' '.join(buf))
    return '\n'.join(out)


_CHECK_PREFIX_RE = re.compile(
    r'^[^：:，,。]*?(?:B\s*超|CT\s*平扫|CT\s*检查|X\s*光|X\s*线|MRI|超声|心电图|'
    r'人体代谢率|人体成分|骨密度|动脉硬化|经颅多普勒|'
    r'检查|检测|测定)\s*[：:]\s*'
)


def _strip_check_prefix(item_name: str) -> str:
    """剥离 LLM 可能误保留的检查项前缀，如 '甲状腺B 超：甲状腺双叶多发囊性结节' → '甲状腺双叶多发囊性结节'。"""
    item_name = item_name.strip("*＊★☆■□●○·\u3000 ")
    m = _CHECK_PREFIX_RE.match(item_name)
    if m:
        return item_name[m.end():]
    return item_name


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
        # 回退: 删除本段即恢复旧行为。
        anom_rows = db.execute(
            text("""SELECT ri.item_name, ri.item_name_standard FROM report_indicator ri
                    JOIN indicator_judgment ij ON ij.indicator_id = ri.id
                    WHERE ri.report_id = :rid
                      AND ij.color_level IN ('yellow', 'red')"""),
            {"rid": report_id},
        ).fetchall()
        anom_names = set()
        for _nm, _std in anom_rows:
            if _nm:
                anom_names.add(_nm)
            if _std:
                anom_names.add(_std)
        cross_dup = False
        bmi_like = {"超重", "肥胖", "体重指数"}
        stem = re.sub(r"(增高|偏高|降低|偏低|升高|下降|增多|减少)$", "", item_name)
        has_dir = stem != item_name
        if item_name in bmi_like:
            cross_dup = any(
                ("身高体重指数" in n) or ("体重指数" in n) or ("BMI" in n.upper())
                for n in anom_names
            )
        elif item_name and ("身高体重指数" in item_name):
            cross_dup = any(n in {"超重", "肥胖", "体重指数"} for n in anom_names)
        elif item_name:
            for n in anom_names:
                if not n:
                    continue
                if n == item_name or n in item_name or item_name in n:
                    cross_dup = True
                    break
                # 方向词条目("低密度脂蛋白增高")与指标名词根匹配
                if has_dir and len(stem) >= 3 and (stem in n or n in stem):
                    cross_dup = True
                    break
                # 字符交集兜底("谷丙转氨酶偏高" vs "谷草/谷丙" 共享 {谷,丙})
                if has_dir and len(stem) >= 3 and len(set(stem) & set(n)) >= 2:
                    cross_dup = True
                    break
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
                 file_id: Optional[str] = None):
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

        # For text-based PDFs, use direct text extraction + LLM parsing
        if task.file_type == "pdf" and _pdf_has_text(processed_path):
            text = _extract_pdf_text(processed_path)
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
                    extract_column_table_rows,
                    extract_personal_info,
                )
                # 2026-08-31: 指标解析前挖掉结论段(体检结果综述/总检建议/异常结果汇总),
                # 防止结论条目被误提取进指标线 —— 崇左"甲状腺结节/右肾囊肿/谷丙转氨酶偏高"、
                # 防城港"红细胞计数增多/高尿酸血症" 因此混入指标线, 前端同名剔除结论后
                # 用户看到"漏了总检异常"或指标线显示无参考范围的结论条目。
                findings_sec = _locate_findings_sections(text)
                if findings_sec and len(findings_sec) > 50:
                    text = text.replace(findings_sec, "")
                rows = extract_indicator_rows(text)
                # 2026-08-28: 列式表格(广西"项目名称|检查结果|单位|参考范围|提示")
                # 标志权威 —— 提示列异常标志 signal_flag=3 强制黄, 弃检不入库
                col_rows = extract_column_table_rows(text)
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
                indicators = []
                seen_sig = set()
                col_keys = set()
                # 2026-08-29: 行式行的标志可能高于列式错配行(桂林"结果高" f=3 vs
                # 列式同 key f=0) → 列式行 flag 不足时用行式的
                row_flag_map = {
                    (r["item_name"], r["result"]): r.get("signal_flag") or 0 for r in rows
                }
                # 列式行优先(带 ref/unit/flag), 行式行补漏
                for r in col_rows:
                    sig_key = (r["item_name"], r["result"])
                    col_keys.add(sig_key)
                    r = dict(r)
                    if (r.get("signal_flag") or 0) < row_flag_map.get(sig_key, 0):
                        r["signal_flag"] = row_flag_map[sig_key]
                    indicators.append(r)
                for r in rows:
                    sig_key = (r["item_name"], r["result"])
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
                    if s_key not in seen_sig and s_key not in col_keys:
                        indicators.append({
                            "item_name": s_key[0], "result": s_key[1],
                            "unit": "", "ref_low": None, "ref_high": None,
                            "signal_flag": 2 if s_key in arrow_keys else 1,
                        })
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
                    }
                    for ind in raw_indicators
                ])
            # === END ===
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
        report.name = personal_info.get("name")
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

        # Extract conclusion text from report raw content
        if images_b64 is not None:
            # Image-based: use VLM to extract conclusion directly from images
            try:
                conclusion = vlm_client.extract_conclusion_from_images(images_b64)
                if conclusion:
                    report.conclusion_text = conclusion
                    db.commit()
                    _log.info("conclusion extracted via VLM report=%d len=%d", report.id, len(conclusion))
            except Exception as e:
                _log.warning("VLM conclusion extraction failed report=%d: %s", report.id, e)
        elif report_raw_text and len(report_raw_text) > 100:
            # Text-based PDF: use LLM on extracted full text
            try:
                conclusion = asyncio.run(_extract_conclusion_async(report_raw_text))
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
                item_name=ind.get("item_name", ""),
                item_name_standard=ind.get("item_name_standard"),
                item_code=ind.get("item_code"),
                result_value=ind.get("result"),
                unit=ind.get("unit"),
                ref_range_low=_clean_ref(ind.get("ref_low")),
                ref_range_high=_clean_ref(ind.get("ref_high")),
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


def _extract_pdf_text(file_path: str) -> str:
    """Extract all text from a text-based PDF."""
    import fitz
    doc = fitz.open(file_path)
    texts = []
    for i, page in enumerate(doc):
        t = page.get_text().strip()
        if t:
            texts.append(f"--- Page {i+1} ---\n{t}")
    doc.close()
    return "\n\n".join(texts)


def _parse_text_with_llm(text: str) -> dict:
    """Send extracted PDF text to LLM for indicator parsing."""
    return asyncio.run(_parse_text_with_llm_async(text))


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
    return f"""从以下体检报告文本中提取信息，返回 JSON 格式（不要 Markdown 代码块）：

{{
  "name": "姓名",
  "gender": "男或女",
  "age": 年龄数字或null,
  "report_date": "YYYY-MM-DD或null",
  "unit_name": "体检机构名称（如XX医院、XX医院健康管理中心）或null",
  "indicators": [
    {{"item_name": "指标名称", "result": "检测结果", "unit": "单位", "ref_low": "参考下限", "ref_high": "参考上限"}}
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
8. 没有的字段填 null

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


def list_reports(db: Session, hospital_id: str, user_id: Optional[int] = None,
                 page: int = 1, page_size: int = 20) -> tuple:
    from sqlalchemy.orm import joinedload
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
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
        results.append({
            "id": r.id,
            "task_id": r.task_id,
            "name": r.name,
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
