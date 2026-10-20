"""确定性指标行提取器(层1+层3, 2026-08-25)。

第一性原理: 指标行有硬结构信号 —— 名称 + 结果(数值或定性词)。
LLM 自由提取不稳定(梧州 67 vs 226 项), 改为规则定位:

- 行内模式: 一行内 "名称 数值 [单位] [范围]"  (常规表格/综述列表)
- 分行模式: 名称行 + 下一行纯值行("身高\\n156\\n体重\\n50.6", 北京模板两列表)
- 定性值: 正常/未见/无/未闻及/阴性/阳性 等与名称配对

层3校验: 名称+结果必须都存在; 按(名称,结果)去重。
"""
import re
from typing import List, Optional

_QUALITATIVE_WORDS = (
    "正常|未见异常|未见|未闻及|未触及|未检出|无异常|无|阴性|弱阳性|阳性|"
    "可疑|偏低|偏高|正常范围|轻度|中度|重度|检出|少量|\(\+\)|\(-\)|\(±\)|±|"
    "律齐|律不齐|无肿大|无压痛|无包块|无结节|无粘连|无分离|无移位|"
    "活动正常|搏动正常|未见肿大|未见明显异常|未见异常回声|无异常回声|"
    "清晰|清亮|清澈|淡黄色|黄色|浑浊|对称|居中|光滑|呈正常生理弯曲|屈曲正常|未触及肿大|未扪及|"
    "第[0-9,，、\s]{1,15}项(阳性|阴性)|"
    "[-–—]|1\+|2\+|3\+|4\+|5\+|\d\+|±|\+-|-\+"
)
# === 2026-09-07: 值单元格规范化 ===
# ">1000[阳性反应（+）]" → ">1000"(符号+数字, 剥方括号); 半定量 "+1"/"1+2.0" 原样作值
_SIGNED_VAL_RE = re.compile(r"^([<>≤≥]?\s*[\d.]+)\s*(?:\[[^\[\]]*\])?$")
# 2026-09-28: 定量结果带定性后缀("0.00(-)"/"410.89(+)"/"0.01(+)", 乙肝两对半定量)
# —— 剥后缀返回数值; 正负定性由标志列(↑)/参考范围表达, 结果只留数值
# (付立光/蔡明 乙肝两对半: 参考范围被"含(+/-)结果"挤成单独结果行的回归)。
_QUAL_SUFFIX_VAL_RE = re.compile(r"^([<>≤≥]?\s*[\d.]+)\s*[（(][+\-][)）]$")
_SEMI_VAL_RE = re.compile(r"^[+]?[1-5]\+?[\d.]*$")
_COMBO_VAL_RE = re.compile(r"^([\d.]+)\s*[,，]\s*(超重|肥胖|偏高|偏低|升高|降低|阳性|异常)$")


# 2026-09-15: 结果与箭头同行("17.6 ↓"/"7.17↑"/"阳性 ↑") —— 报告无提示列, 标志
# 在结果右侧(嘉兴市中医医院陈镜霓)。返回 (值, 是否带箭头)。
_TRAILING_ARROW_VAL_RE = re.compile(
    # 2026-09-28: 箭头可带括号("7.9(↓)", 弘爱 本次结果列) —— 否则信号行自身值取不到,
    # 向下找值会先撞到上一列"上次结果"(9.2)造成错配假黄
    r"^([<>≤≥]?\s*[\d.]+|阴性|阳性|弱阳性|[+\-±]{1,3})\s*[（(]?\s*[↑↓]\s*[)）]?$")


def _value_with_arrow(s: str) -> tuple:
    m = _TRAILING_ARROW_VAL_RE.match((s or "").strip())
    if m:
        return m.group(1).strip(), True
    return None, False


# 2026-09-16: 等级值(罗马数字/IV+级)与纯加号半定量 —— 坐标路径结果形态
# (陈镜霓 H004-40 白带常规: "阴道清洁度（QJD） Ⅲ级 ↑" / "白细胞（WBC1） ++")。
# 仅坐标路径(layout)启用: 行式路径"值后按文本序消费单位"会把下一行单位列的
# "/HP"错配给等级行(视觉上 /HP 属下一行), 与坐标真值冲突; 且行式值后单位会经
# service 组装回填进列式行, 污染正确产物。
# 2026-10-13: 支持"度"形态("III度 ↑", 漳州三院胡月 白带清洁度)
_GRADE_VAL_RE = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[IV]+)\s*(?:级|度)$")
_GRADE_ARROW_VAL_RE = re.compile(
    r"^((?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|[IV]+)\s*(?:级|度))\s*[↑↓]$")
_PLUS_ONLY_VAL_RE = re.compile(r"^\+{2,4}$")


def _grade_value_with_arrow(s: str) -> tuple:
    """等级值+箭头("Ⅲ级 ↑"/"III级↓") → (值, True); 否则 (None, False)。"""
    m = _GRADE_ARROW_VAL_RE.match((s or "").strip())
    if m:
        return m.group(1).strip(), True
    return None, False


def _norm_value_cell(ln: str) -> Optional[str]:
    """若整行是"可作结果的单元格"(符号数字+方括号注释/半定量), 返回规范化 result, 否则 None。"""
    # 2026-10-14: 全角符号归一(纪卓武 尿常规 "＋－" 曾不被识别 → 尿蛋白行整行丢失)
    ln = (ln or "").replace("＋", "+").replace("－", "-").replace("−", "-")
    # 2026-10-07: 数值+双符号括号("10(+-) 个/uL" 白炜尿隐血)整串作结果 —— 旧式
    # 只认单符号"(+)"(取值丢括号), "10(+-)"不被识别 → 箭头配对滑到参考"阴性"产物
    # 假黄(尿隐血(BLD)|阴性); 双符号是 ± 半定量标记, 打印结果含它。
    m = re.match(r"^[<>≤≥]?\s*[\d.]+\s*[（(][+\-±]{2}[)）]$", ln.strip())
    if m:
        return ln.strip()
    m = _QUAL_SUFFIX_VAL_RE.match(ln.strip())
    if m:
        return m.group(1).strip()
    m = _SIGNED_VAL_RE.match(ln)
    if m:
        return m.group(1).strip()
    if _SEMI_VAL_RE.match(ln):
        return ln.strip()
    m = _COMBO_VAL_RE.match(ln)
    if m:
        return m.group(1).strip()
    return None


# 值行: 数字[+空格][+单位(字母或汉字, 如"60 次/分")]; 不含负号(排除"140 -270"范围行)
# 2026-09-10: 汉字单位后缀必须带空格(如"60 次/分") —— 数字开头且与汉字无空格的
# 行是**指标名称**("25羟基维生素D" 池州/维生素D, 曾整行被当"值25+单位"漏提);
# 字母单位不带空格仍允许("42.85nmol/L")。
_VALUE_RE = re.compile(
    r"^([\d.]+)((?:\s+(?=[\u4e00-\u9fa5])[\u4e00-\u9fa5a-zA-Z%‰/·×^μ]+)|\s*[a-zA-Z%‰/·×^μ]*)$"
)
_QUAL_RE = re.compile(rf"^({_QUALITATIVE_WORDS})\s*[+\-]?$")
# 2026-09-14: 定性值带括号注释("阳性( DOB:10.30)" 华山 C13 呼气试验, "阳性(234.27)")
# → 视作结果单元格; 仅限 阳性/阴性/弱阳性, 避免吞并其它定性词的说明性括号。
_QUAL_ANNOT_RE = re.compile(
    r"^(?:阳性|阴性|弱阳性)\s*[A-Za-z]{0,8}\s*[（(][^）)]*[)）]\s*$")
# 2026-09-28: 数值结果带中文定性后缀("130 阳性"/"0.00732 阳性", 蔡明乙肝两对半) ——
# 拆出数值作结果, 定性词转标志(旧行为把整串当结果, 与行式"130"重复两条; 行式还把
# "阳性"当单位)
_QUAL_TAIL_RE = re.compile(r"^([<>≤≥]?\s*[\d.]+)\s*(阳性|弱阳性|阴性|±)$")


def _split_qual_tail(t: str) -> tuple:
    """数值+中文定性后缀 → (数值, 定性词或 None)。"""
    m = _QUAL_TAIL_RE.match((t or "").strip())
    if not m:
        return t, None
    return m.group(1).strip(), m.group(2)
# 名称须含汉字(排除单位行"cm""ng/ml"); 允许前导标记(▲/▲★/★/＊/*, 滨州/北京/贵港表格,
# 可带空格);
# 2026-08-29: 允许希腊字母/字母/数字开头("γ-谷氨酰转移酶""*L-γ-谷氨酰基转移酶""C14尿素呼气试验")
_NAME_RE = re.compile(
    # 2026-09-28: 允许 "[HR]" 等方括号前缀(曾繁鲁检验表名称列统一"[HR]xxx") ——
    # 否则首段名不被认作名称, 折行续行"测定(HBsAg)"被当独立指标
    r"^(?:\[[A-Za-z0-9]{1,6}\]\s?)?[▲△★*＊]{0,2}\s?[A-Za-z0-9α-ωΑ-Ωγ#\-.()（）%％\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A]*\s?[\u4e00-\u9fa5]"
    r"[\u4e00-\u9fa5A-Za-z0-9()（）%·#\-/α-ωΑ-Ωγ\[\]\. \uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A]{0,24}$"
    # 2026-09-28: 单位尾段补 min("ml/min", 单吉飞肾小球过滤率) —— 否则被当 ASCII 斜杠名称
    r"|^(?i:(?!.*\/(?:mmol|umol|mg|ug|ng|pg|g|ml|dl|l|iu|u|fl|min)\s*$)"
    r"[A-Za-z][A-Za-z0-9]*(?:/[A-Za-z0-9]+)+$)"
    r"|^(?:pH|PH)$"
)
# 单位行: 字母/符号(可含数字如"10~9/L"); 2026-09-05: 允许 * 前缀(柳州 "*10^9/L")、全角 ％
_UNIT_RE = re.compile(
    r"^[*＊]?\s*[\d~a-zA-Z%％‰/·×^μ]+(?:\s+[a-zA-Z%％‰/·×^μ]+)?$"
)
# 范围行: "9-50" / "5.0-9.0" / "<5.2" / ">1.04" / "0-64" / "1.16--1.42" / "1.16–1.42" / "0.7～1.7"
# 2026-08-29: 单限 "～5.20"(防城港市中) "~5.17" 全/半角波浪号开头
# 2026-08-31: ↑/↓ 异常前缀(广西人民"↑208～428""↑<3.37", 可叠加符号)
# 2026-09-05: ≤/≥ 单侧上限/下限(德宏 BA% "≤1"、BA# "≤0.06")
_RANGE_RE = re.compile(
    # 2026-10-07: 支持方括号包裹的参考范围(中科体检陈霓"总胆固醇…[<5.2]/[3.5--9.5]")
    r"^\[?\s*(?:[<>＜＞≤≥~～↑↓]\s*){0,2}[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+\s*\]?$"
    r"|^\[?\s*(?:[<>＜＞≤≥~～↑↓]\s*){0,2}[\d.]+\s*\]?$"
)
# 箭头行(偏高/偏低标记): "↑" "↓"
_ARROW_RE = re.compile(r"^[↑↓]$")
# 名称行黑名单(页眉/标题/结构化标签)
_SKIP_NAMES = re.compile(
    r"^(姓名|性别|年龄|体检日期|检查日期|审核日期|健康档案号|体检编号|登记号|电话|话$|建议$|弃检|未检|拒检|放弃检查|身份证|证件号码|\d{0,3}岁|体检次数|"
    r"项目名称|检查结果|检查医生|检查医师|审核医师|审核者|审核人|报告医师|科室小结|检查者|报告整理|"
    r"主检医师|总检医师|总检建议与结论|温馨提示|说明[:：]|单位名称|单位备注|样本编号|"
    r"团体|序号|初步意见|合理建议|补充说明|参检单位|受检单位|室间隔厚度|左室后壁厚度|^的[\d.]+倍|"
    r"第\s*\d+\s*页|第|页/共|页|一般项目|内科查体|外科查体|一般检查|龄|"
    r"参考范围|正常范围|参考区间|历史结果|提示参考范围|体检健康建议|健康建议|"
    r"总检建议|检查所见|"
    r"个/[\wμµ]+|个/视野|其它$|小结[:：]?$|体检$|住院号|病室|"
    r"报告日期|图像层厚|层距|电话|手机|终审日期|初审日期|打印日期|接收日期|总检时间|总审时间|总计|日期|"
    r"体检号[:：]?|体检者|体检号码|总检日期|查\s*体\s*号|查体日期|出生日期|档案ID|咨询电话|"
    r"服务热线|服务时间|报告编号|登记号|检查时间|检查医生|检查结论|体检条码|条码|男$|女$|每$|法$|缘$|白A|"
    r"参考值|次/分|"
    r"单位[:：]|单位$|部门[:：]|体检报告|健康体检报告|本体检报告|"
    r"体重超过标准体重|边缘|合适水平|升高|降低|提示|异常|现病史|既往史|家族史|"
    # 2026-09-23: 第四批反馈清理 —— 生活方式/超声描述碎片
    r"吸烟史|饮酒史|最大|最小|"
    r"^(?:肾功|肝功|血脂|血常规|尿常规|尿沉渣|常规生化|乙肝|丙肝|甲功|甲胎|心肌酶)[一二三四五六七八九十\d]*项?$|^比$|"
    r"大小约|小约为|直径约|"
    r"常规心电图|彩超|B超|超声|X线|DR|CT|MR|TCD|碳13|幽门螺杆菌(?!I?gG?|抗体|抗原|HP)|门诊|瞬时弹性成像)"
)
# 2026-09-28: 页面机构抬头整行("江西中医药大学附属医院", 蔡明每页页眉) —— 坐标路径
# 与数据行同 x 近 y 被并进名称("江西中医药大学附属医院肌钙蛋白I"), 名称列出现时跳过
_HOSPITAL_HEADER_RE = re.compile(
    # 2026-10-16: 前缀式(不再要求整行恰好是机构名) + 允许数字(联勤保障部队第910医院);
    # 用于名称列跳过 / 折行名合并守卫(页眉机构名与下一数据行并名)
    r"^[\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:医院|体检中心|健康管理中心)")
# 2026-09-05: 名称含词黑名单(单位/页眉嵌名:"福建省第二人民医院健康管理中心·体检报告")
# 2026-10-19(韩先锋): 名称内含测量值("室间隔厚度10mm左室后壁厚度")是表格/文本
# 并格垃圾, 不是指标名
_NAME_MEAS_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|ml|mL)(?![A-Za-z])")
_SKIP_SUB_RE = re.compile(
    r"(健康管理中心|健康体检中心|医院体检中心|·体检报告|健康管理有限公司|医院有限公司|"
    r"体检号|体检者|体检号码|流水号|联系电话|历年对比|结果对比图|体检结果对比|对比图|"
    r"结果提示|已做|未做|收费项目|"
    # 2026-09-28: OCR 分检报告头字段(蔡丰泽 image 页: 检查号/病案号/申请科室…)
    r"审核时间|报告时间|检测结果|病案号|检查号|病人信息|申请科室|申请医生|"
    # 2026-10-07: 检验科来源行(陈霓"数据来源：南昌中科医学检验实验室" 曾被当指标)
    r"数据来源|审核者|检验者[:：]?$|"
    # 2026-10-07: 个人信息表头合并名(陈东海视觉重排后"单位/体检编号"与相邻行并名:
    # "单位体检编号"/"中华人民共和国八所出入境边防检查站身高" 曾被当指标)
    r"体检编号|证件号码|婚育史|运动习惯|体检类型|中华人民共和国|编\s*码[:：]?|"
    # 2026-10-13: 机构名/驻点行(任晓光"南昌醫學院第一附屬医院"/"江西出入境边防检查总站")
    r"醫學院|医学院|附屬|附属医院|出入境|边防|体检中心$|健康管理中心$|"
    # 2026-09-23: 表头字段合并名(台州 48 "登记日期电话号码"/"健康号" 曾被当指标)
    r"登记日期|电话号码|健康号|出生日期|打印时间|体检日期|"
    # 2026-09-23: 医院联系信息行(常逢龙"总 院：021-…/浦东院区：021-…")
    r"院区|总\s*院|"
    # 2026-10-14: 表头"正常参考值"列与前一行并名("正常参考值身高", 纪卓武);
    # "小结"尾缀合并名("辨色力正常小结"/"正常心电图小结"); 报告单头合并名
    # ("尿液分析检验报告单条码号"/"敦煌康美综合医院报告单标本号")
    r"正常参考值|小结$|报告单|条码号|标本号|诊断意见|上上次|上次|附属|预约号|注意事项)"
)


# 名称续行(折行): 纯汉字短行, 非值/非名称/非跳过; 如"平均红细胞血红蛋白含\n量\n29.60"
_NAME_CONT_RE = re.compile(r"^[\u4e00-\u9fa5]{1,8}$")
# 2026-09-28: 混合折行续行("测定(HBsAg)"/"移酶(AST)"/"原氨酸测定(FT3)", 曾繁鲁
# 检验表名称列折行) —— 短中文 + 括号缩写; 纯中文括号("(定量)")不并入(保持跳板语义)
_NAME_CONT_MIXED_RE = re.compile(r"^[\u4e00-\u9fa5]{1,8}[（(][^（()）]{1,24}[)）]$")
# 2026-10-19(韩先锋): 中文名折行续行 = 1-6 汉字 + 拉丁缩写("平均血小板体积测"+
# "定MPV" → 平均血小板体积测定MPV; "平均血小板分布宽"+"度PDW")
_NAME_CONT_CJK_LATIN_RE = re.compile(r"^[\u4e00-\u9fa5]{1,6}[A-Za-z][A-Za-z0-9%/]*$")
# 2026-10-19(韩先锋): 纯拉丁缩写续行("MON%"/"LYM%"/"NEUT%")并入上一条中文名
_NAME_CONT_LATIN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9%％]*%?$")
# 2026-09-23: 封面装饰重复字行("上上上上上…")不得并进名称(曹嘉冰"淋巴细胞百分比上上上…")
_REPEAT_CHAR_JUNK_RE = re.compile(r"^(.)(?:\1|[\s()（）]){3,}$")
# 2026-09-02: 纯括号折行("(定量)")—— 名称与值之间的折行修饰, 不单独成行
_PAREN_ONLY_RE = re.compile(r"^[（(][^（()）]*[)）]$")
# 2026-09-07: 拉丁缩写+括号续行("IgG(IgG/VCA)", 日照 EB 衣壳抗原名称续行) → 并入名称
# 2026-09-28: 前缀须含拉丁**字母** —— 纯数字值+括号("0.00(-)"/"0.37(-)", 付立光乙肝
# 定量表)也满足旧字符集, 被当"名称括注续行"错配给上上行却保留本值("0.00(-)"后的
# "<0.05" 被当结果产生重复行); 数值+定性括号不是名称。
_LATIN_PAREN_RE = re.compile(r"^[A-Za-z0-9/·.]*[A-Za-z][A-Za-z0-9/·.]*[（(][^（()）]*[)）]$")


def _skip_name(s: str) -> bool:
    """名称黑名单统一判定: 前缀黑名单 + 含词黑名单(页眉/单位名嵌名)。
    2026-09-23: 同时用**去空白**形态再判 —— 表头单元格常带字间空格("体 检 号"/
    "姓 名"/"登记日期 电话号码"), 原名判不出被当指标(第四批台州 48 表头垃圾行)。"""
    if _SKIP_NAMES.match(s) or _SKIP_SUB_RE.search(s):
        return True
    if _REPEAT_CHAR_JUNK_RE.match((s or "").strip()):
        return True  # 重复字装饰行
    t = re.sub(r"\s+", "", s or "")
    if _NAME_MEAS_RE.search(t):
        return True  # 2026-10-19: 名称内嵌测量值("室间隔厚度10mm…")是并格垃圾
    return bool(t != s and (_SKIP_NAMES.match(t) or _SKIP_SUB_RE.search(t)))


def _clean_name_tail(name: str) -> str:
    """2026-09-05: 名称尾词清洗 —— 综述行"名称偏高 (值"被名称组吞入的异常词/悬挂括号剥掉。
    注意须保留报告方把异常词当名称组成部分的情形较少, 此处只剥词缀组合形态。"""
    return re.sub(
        r"\s*(增高|升高|偏高|降低|偏低|异常|阳性|弱阳性|增快|过重|减少|超重|肥胖|偏轻|偏重)"
        r"\s*[（(]?$", "", name).strip()


# 2026-09-23: 行式垃圾单元格形态(第四批反馈三份报告):
#   "阳性( DOB"(定性碎片) / "…A1c(NGSP) 5.60"(名称尾随数值) / "清晰透明清晰透明"(重复名)
_JUNK_NAME_FRAG_RE = re.compile(
    r"[阳阴]性\s*[（(]|\s\d+(?:\.\d+)?$|^(.{2,4})\1$|(.)\2{3,}$|"
    # 2026-09-28: 名称含日期残片("年06 月22 日听力(右)", 生明月分科页眉拼接)
    r"年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
#   合并多条目行(单位里含"，名称: 数值" —— "蛋白A: 0.80 , 总胆固醇: 5.33")
_JUNK_UNIT_MERGE_RE = re.compile(r"[，,]\s*[\u4e00-\u9fa5]{1,12}[:：]\s*[\d.]")
#   查体部位行 + 正常结果(用户 2026-09-23 指定清理: 头面部/裂隙灯/甲状腺)
# 2026-09-28: 补五官/查体部位(生明月分科页"听力/扁桃体/嗅觉/心脏听诊"混入绿区) ——
# 仅当结果为"正常性"时滤(异常结果如"宫颈糜烂样改变"不受影响)
_EXAM_PART = (r"(?:头面部|裂隙灯|甲状腺|听力(?:[（(]?[左右][)）]?)?|扁桃体|嗅觉|咽喉|"
              r"外阴|阴道|宫颈|心脏听诊|肺部听诊|肺部|皮肤|浅表淋巴结|淋巴结|角膜|"
              r"脊柱|四肢|关节|胸廓)")
_EXAM_PART_JUNK_RE = re.compile(rf"^{_EXAM_PART}(?:[、，,]{_EXAM_PART})*$")
_NORMAL_RESULT_RE = re.compile(
    r"^(未见明显异常|未见异常|正常|无异常|未见|无肿大|未闻及异常|未闻及|通畅|无殊|"
    r"未触及|阴性|（-）|\(-\)|-|－)$")


def _strip_result_marker(t: str) -> str:
    """剥结果前缀装饰标记(◆◇■□●○) —— 报告方分隔符 cell 被并入结果列(单吉飞)。"""
    return (t or "").lstrip("◆◇■□●○").strip()


def _junk_row_cell(name: str, unit: str = "", result: str = "") -> bool:
    """行式/列式行落库前垃圾判定(名称碎片/合并行/查体部位+正常结果)。"""
    n = (name or "").strip()
    if _JUNK_NAME_FRAG_RE.search(n):
        return True
    # 2026-09-28: 结果为字段标签残片("检查者凌延昭"/"报告时间2026-05-20", 单吉飞
    # 分科页眉"■内科常规检查"被拼成指标行)
    if re.search(r"检查者|报告时间|检查日期|审核日期|检查医生|审核医师|检查结果|检查项目",
                 result or ""):
        return True
    # 2026-09-23: 括号未闭合的截断标题("右侧甲状腺小结节可能（TI-RADS" + 值3 + 单位"）")
    if (n.count("(") + n.count("（")) > (n.count(")") + n.count("）")):
        return True
    if unit and _JUNK_UNIT_MERGE_RE.search(unit):
        return True
    if _EXAM_PART_JUNK_RE.match(n) and _NORMAL_RESULT_RE.match((result or "").strip()):
        return True
    return False


def _is_value_line(s: str) -> bool:
    s = (s or "").replace("＋", "+").replace("－", "-").replace("−", "-")
    return bool(_VALUE_RE.match(s) or _QUAL_RE.match(s) or _QUAL_ANNOT_RE.match(s)
                or _QUAL_SUFFIX_VAL_RE.match(s))


def _next_line_kind(lines: list[str], i: int) -> str:
    """下一行类型: value(结果/定性值) / unit / range / arrow / name / other"""
    if i + 1 >= len(lines):
        return "other"
    nxt = lines[i + 1]
    if _ARROW_RE.match(nxt):
        return "arrow"
    if _UNIT_RE.match(nxt):
        return "unit"
    if _RANGE_RE.match(nxt):
        return "range"
    if _is_value_line(nxt):
        return "value"
    if _NAME_RE.match(nxt) and not _skip_name(nxt):
        return "name"
    return "other"


def _merge_unclosed_paren_lines(lines: list[str]) -> list[str]:
    """2026-09-05: 括号折行合并 —— 行内 ( 或 （ 未闭合时拼下一行(仅拼一次)。
    福建第二"★乙型肝炎表面抗原测定（定\n性）" → 名称跨行断在括号内。
    不能循环拼(正文任一行"参考范围（"会把后续全文吞成一行)。
    """
    out: list[str] = []
    last_merged = False
    for ln in lines:
        if out and not last_merged and not ln and not out[-1]:
            continue
        if out and not last_merged and (out[-1].count("(") + out[-1].count("（")) > (out[-1].count(")") + out[-1].count("）")):
            out[-1] += ln
            last_merged = True
        else:
            out.append(ln)
            last_merged = False
    return out


def _merge_wrapped_names(lines: list[str], latin_wrap: bool = False) -> list[str]:
    """预处理: 名称折行合并("平均红细胞血红蛋白含\\n量\\n29.60" → 名称拼一行)。"""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]

        # 2026-10-14: 值短语结尾行("形态正常"/"不肿大"/"无畸形"/"肋下未触及")不得
        # 作名称折行首段(敦煌检体区"形态正常四肢"/"不肿大甲状腺" 合并名);
        # 单汉字行("齐")是值不是名(心电图区"齐杂音")
        _val_tail = bool(re.search(r"(正常|异常|肿大|畸形|触及|明显|未见)$", ln)) \
            or bool(_HOSPITAL_HEADER_RE.match(ln))
        _cjk_n = len(re.findall(r"[\u4e00-\u9fa5]", ln))
        if (
            i + 2 < n
            and not _is_value_line(ln)
            and _NAME_RE.match(ln) and not _skip_name(ln)
            and not _val_tail and _cjk_n >= 2
            and (_NAME_CONT_RE.match(lines[i + 1])
                 or _NAME_CONT_MIXED_RE.match(lines[i + 1])
                 or _NAME_CONT_CJK_LATIN_RE.match(lines[i + 1])
                 or (latin_wrap and _NAME_CONT_LATIN_RE.match(lines[i + 1]))
                 # 2026-09-28: 纯拉丁括号续行("(NEUT%)"/"(ALT)")并入名称;
                 # 纯中文括号("(定量)")保持跳板(与列式 key 一致, 勿并)
                 or (_PAREN_ONLY_RE.match(lines[i + 1])
                     and re.search(r"[A-Za-z]", lines[i + 1])))
            and not _REPEAT_CHAR_JUNK_RE.match(lines[i + 1])  # 2026-09-23: 重复字装饰行不并名
            and not _is_value_line(lines[i + 1])  # 2026-09-05: 续行不得是定性值("正常/阴性"),
            # 否则"发育情况\n正常\n正常"式双列表格被误合并(崇左检体区)
            and lines[i + 1] not in ("男", "女", "年龄")  # 2026-09-07: 莆田头部"蔡芳坤/男/30岁"防误拼
            and _is_value_line(lines[i + 2])
        ):
            out.append(ln + lines[i + 1])
            i += 2  # 跳过名称行与续行, 下一轮处理值行
            continue
        out.append(ln)
        i += 1
    return out


def _merge_range_fragments(lines: list[str]) -> list[str]:
    """2026-10-19(韩先锋): 参考范围数字被表格换行拆断("0.108-0.2"/"82",
    "1.003-1.0"/"30", "5000-1200"/"0") —— 高位小于低位且下一行是短数字时拼接。"""
    out: list[str] = []
    i = 0
    n = len(lines)
    _rng = re.compile(r"^[<>≤≥]?\s*([\d.]+)\s*[-~－—]\s*([\d.]+)\s*$")

    def _dec(x: str) -> int:
        return len(x.split(".")[1]) if "." in x else 0

    while i < n:
        ln = lines[i]
        if i + 1 < n and re.fullmatch(r"\d{1,4}", lines[i + 1].strip()):
            m = _rng.match(ln.strip())
            if m:
                lo_s, hi_s = m.group(1), m.group(2)
                try:
                    lo, hi = float(lo_s), float(hi_s)
                except ValueError:
                    lo = hi = None
                _joined_hi = hi_s + lines[i + 1].strip()
                # 判据: 高位小于低位, 或高位小数位少于低位(截断) 且拼接后小数位
                # 与低位一致、范围有效 → 视为折行
                if lo is not None and hi is not None and (
                        hi <= lo or _dec(hi_s) < _dec(lo_s)):
                    try:
                        _jhi = float(_joined_hi)
                    except ValueError:
                        _jhi = None
                    if (_jhi is not None and _jhi > lo
                            and _dec(_joined_hi) == _dec(lo_s)):
                        out.append(ln.strip() + lines[i + 1].strip())
                        i += 2
                        continue
        out.append(ln)
        i += 1
    return out


def _merge_unclosed_square(lines: list[str]) -> list[str]:
    """2026-09-07: 方括号跨行(">1000[阳性反应\\n（+）]" 日照) → 拼到 ] 闭合。"""
    out: list[str] = []
    for ln in lines:
        if out and out[-1].count("[") > out[-1].count("]") and "[" in out[-1]:
            out[-1] += ln
        else:
            out.append(ln)
    return out


def _merge_para_wrap(lines: list[str]) -> list[str]:
    """2026-09-07: 综述段落长行折行导致的"半截名称行"修复 —— 厦门华西
    "(5)体检血脂：甘油三\\n酯：2.06mmol/L↑" 折行处把"酯"切到行首, 行内模式
    会把"酯：2.06"当指标(垃圾)。规则: 当前行 = "短名称(≤8汉字)+冒号+数值"且
    上一行以汉字结尾 → 上一行折行未完, 两行拼接后重扫。
    拼接后整行行首若为 (【 等(段落原行)则不会命中行内/名称分支, 一并消"臀围"折行垃圾。
    """
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if (
            i > 0
            and re.match(r"^[\u4e00-\u9fa5]{1,8}\s*[:：]\s*[\d.]+", ln)
            and re.search(r"[\u4e00-\u9fa5]$", out[-1])
            and len(out[-1]) < 150
        ):
            out[-1] += ln
            i += 1
            continue
        out.append(ln)
        i += 1
    return out


def _parse_ref(rest: str) -> tuple[Optional[str], Optional[str]]:
    """从行内剩余文本解析参考范围: "3.1-5.7" / "1.16--1.42" / "<5.2" / ">1.04" / "～5.20" / "(18-24)"。
    2026-08-31: 容忍 ↑/↓ 异常前缀(广西人民"↑208～428" —— ↑ 是报告方异常标记, 非 ref 一部分)。
    """
    m = re.search(r"\(?([<>＜＞≤≥↑↓]?[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+|[<>＜＞≤≥~～]\s*[\d.]+)\)?", rest)
    if not m:
        return None, None
    s = m.group(1)
    if s.startswith(("<", "＜", "≤")):
        return None, s[1:].strip()
    if s.startswith((">", "＞", "≥")):
        return s[1:].strip(), None
    if s.startswith("~") or s.startswith("～"):
        return None, s[1:].strip()
    s = s.lstrip("↑↓")
    if "-" in s or "~" in s or "～" in s or "至" in s or "–" in s or "—" in s:
        lo, hi = re.split(r"[-~～至–—]{1,2}", s, maxsplit=1)
        return lo.strip(), hi.strip()
    return None, None


# === 2026-09-07: 参考值单元格带单位(厦门华西三列表 "0.29-1.70mmol/L" / "≤5ng/ml";
# 血常规 10^n 复合单位与范围数字粘连: "4.3-5.810^9/L" = hi 5.8 + 单位 10^9/L) ===
_REF_UNIT_SUFFIX_RE = re.compile(
    r"(10\^[0-9]+\s*/?\s*L?|mmol/L|umol/L|μmol/L|µmol/L|ng/ml|mg/L|g/L|IU/L|U/L|"
    r"mIU/mL|ml/min|IU/ml|ng/mL|μg/L|ug/L|pg/ml|mol/L|fL|fl|%)$"
)


def _range_with_unit(s: str) -> Optional[tuple]:
    """若 s 为"参考范围(可带单位)"单元格整行, 返回 (ref_low, ref_high, unit), 否则 None。
    2026-09-07: 剥单位后的 head 必须是纯范围形态 —— 注释文本("TPSA在4-20ug/L 时…")
    不能吞(FPSA/TPSA 行被注释 ref 污染判黄的回归教训)。
    """
    s = s.strip()
    unit = ""
    m = _REF_UNIT_SUFFIX_RE.search(s)
    if m:
        unit = m.group(1)
        # 2026-09-28: 剥单位后去尾部空格 —— "20.00～50.00 %"(生明月血常规表) 余
        # "20.00～50.00 " 带空格, fullmatch 失败导致单位 % 丢失
        s = s[:m.start()].strip()
        # 10^n 复合单位与范围数字粘连: 单位 "10^9/L" 剥离后余尾 "10" 属单位前缀
        if unit.startswith("10^") and re.search(r"\d10$", s):
            s = s[:-2]
    if not s:
        return None
    if not re.fullmatch(
            r"[<>＜＞≤≥~～↑↓]?\s*[\d.]+\s*[-~～至–—]{1,2}\s*[\d.]+"
            r"|[<>＜＞≤≥~～↑↓]?\s*[\d.]+", s):
        return None
    lo, hi = _parse_ref(s)
    if lo or hi:
        return lo, hi, unit.strip()
    return None


# === 2026-09-06: "历年对比"表整段挖除 ===
# 德宏等报告含"历年对比"双年值列表(标题"历年对比" + 表头含 2025-xx-xx/2026-xx-xx 日期列,
# 值=历史/本年度, 无异常标志) —— 该表不是"检查指标明细表"(真表带 检查结果/H 提示列),
# 参考范围左值与历史值会被行式/列式误当指标(肌酐 57/3.4/0.4 绿区垃圾)。
# 规则: 独立标题行命中即挖到下一段落标题(本次体检结论/体检综述/总检建议等)前。
# 2026-10-16: 心脑血管病风险评估页标记(陈佳漩: "心脑血管病风险评估年龄(岁)"/
# "您的心脑血管病10年发病风险为:"/"应该接受生活方式指导…" 被坐标列当指标)
_RISK_ASSESS_LINE_RE = re.compile(r"心脑血管病.{0,6}(?:风险评估|发病风险)")
_HIST_CMP_TITLE_RE = re.compile(
    r"(历年对比|历次部分结果对比|历次.{0,4}结果对比|历年结果对比|历年体检结果|两年对比|近两年对比|"
    r"与既往我院体检结果对比图|历年体检结果对比图|体检结果对比图|结果对比图|"
    r"历次体检结果比对|历次体检对比|历次检查异常结果比对|"
    # 2026-09-24: 陈海波 p22 "前次体检结果对比"(历年对比表标题变体; 该表历史值曾被
    # 当本次指标提取, 微白蛋白 >150 等)
    r"前次体检结果对比|前次结果对比|前次体检对比|与上次体检结果对比|与上次结果对比)")
_HIST_CMP_END_RE = re.compile(
    r"^(本次体检结论|体检综述|体检健康建议|健康建议|总检建议|检查所见|科普说明|总检医生|"
    r"主检医师|报告医师|校对员|终审|操作员|--- Page |【)")


def _strip_history_compare_table(text: str) -> str:
    lines = (text or "").split("\n")
    out: list[str] = []
    skipping = False
    kept_skip = 0
    # 2026-10-19: 标题在文档末尾 ~3 页内(李承龙 历次部分结果对比 pages 19-21)时,
    # 该表跨页延续 → "--- Page" 不作为段尾(否则表尾行漏进指标线)
    _total_pages = sum(1 for ln in lines if ln.strip().startswith("--- Page"))
    _title_ln = None
    for _idx, ln in enumerate(lines):
        s = ln.strip()
        if not skipping:
            if _HIST_CMP_TITLE_RE.search(s):
                skipping = True
                kept_skip = 0
                _title_ln = _idx
                continue
            out.append(ln)
            continue
        kept_skip += 1
        if _HIST_CMP_END_RE.match(s):
            _page_marker = s.startswith("--- Page")
            _title_page = sum(1 for l2 in lines[:_title_ln or 0]
                              if l2.strip().startswith("--- Page")) if _title_ln else 0
            if _page_marker and _total_pages and _title_page >= _total_pages - 3:
                continue  # 末段历次对比表跨页: 页标记不结束
            skipping = False
            out.append(ln)
            continue
        if kept_skip > 400:
            # 2026-10-19: 安全阀 60→400 —— 历次对比表跨页 100+ 行(李承龙 pages 19-21),
            # 60 行处提前恢复把表尾(未查/PDW 历史值)漏进指标线
            skipping = False
            out.append(ln)
            continue
    return "\n".join(out)



# === 2026-09-07: 反列序表格检测 ===
# 茂名人民: 表头 dump = 项目名称/参考值/提示/检查结果/单位 —— "参考值"列在"检查结果"前,
# 视觉上每指标 ref 在名称/值之前 → 行式值行后 range 属下一指标, 不得向后消费。
# 检测: 表头窗口内 参考类词 index < 结果类词 index。
def _data_order_clearly_normal(lines: list[str]) -> bool:
    """数据行序**明确正序**判定: 数值型指标行的参考范围出现在值**之后**(≥20 票且
    超过反序票 2 倍)。

    2026-10-07: 白炜"项目名称/参考值/单位/提示/检查结果"表 —— 文本层表头 dump 顺序
    被排版打乱(参考值出现在检查结果前), `_text_has_reversed_ref` 误报反序 → 行式
    "值后 range 不消费 + `_above_flag` 取名称上方箭头"把**上一行尾部的箭头**(该表
    箭头紧随本行参考值)记到下一个指标, 成片假黄(用户反馈 HBeAg/红细胞平均血红蛋白
    浓度/PDW/尿胆红素/尿隐血 假黄)。数据行序(值后带参考范围)比表头文本序可靠。
    仅在文本判定为反序时用作**降级**(正序→反序方向不由本判定升级: 茂名 16/7、
    齐鲁/弘爱 block 表等不受影响)。
    """
    rev = normal = 0
    n = len(lines)
    for i in range(1, n):
        cand = lines[i]
        if not _NAME_RE.match(cand) or _skip_name(cand) or _is_value_line(cand):
            continue
        _prev = lines[i - 1].strip()
        prev_rng = bool(_RANGE_RE.match(_prev)) and bool(
            re.search(r"[-~～–—]|^[<>≤≥]", _prev))
        nxt_rng = False
        has_val = False
        for j in range(i + 1, min(n, i + 5)):
            lj = lines[j]
            if _NAME_RE.match(lj) and not _is_value_line(lj):
                break
            t = lj.strip()
            if _RANGE_RE.match(t) and re.search(r"[-~～–—]|^[<>≤≥]", t):
                nxt_rng = True
                break
            if _VALUE_RE.match(lj) and re.search(r"\d", lj):
                has_val = True
        if prev_rng and has_val and not nxt_rng:
            rev += 1
        elif nxt_rng and not prev_rng:
            normal += 1
    return normal >= 20 and normal > rev * 2


def _text_has_reversed_ref(lines: list[str]) -> bool:
    """反列序检测: 表头单元格**连续收集**(与列式表头收集同口径), 参考类词
    出现在结果类词**之前**(茂名: 项目名称/参考值/提示/检查结果/单位)。
    2026-09-07: 改为连续收集 —— 21 华西多表头密集时 ±8 窗口跨表会误判(清澈比重
    ref 被断、酸碱度 误配 1.003-1.030 的回归)。"""
    n = len(lines)
    for i in range(n):
        if not _COL_HEADER_START_RE.match(lines[i]):
            continue
        j = i + 1
        cells: list[str] = []
        while j < n and len(cells) < 6 and (
            _COL_HEADER_CELL_RE.match(lines[j]) or len(lines[j]) <= 2
        ):
            if _COL_HEADER_CELL_RE.match(lines[j]):
                cells.append(lines[j])
            j += 1
        if len(cells) < 2:
            continue
        ri = next((k for k, c in enumerate(cells) if c in ("参考值", "参考范围", "正常值")), None)
        si = next((k for k, c in enumerate(cells) if c in ("检查结果", "结果", "本次检查结果")), None)
        if ri is not None and si is not None and ri < si:
            return True
    return False


# === 2026-10-06: 非指标表页整体跳过(中科体检 陈霓) ===
# 该院报告含"结论词/分级(A-G 级别)"表、"强烈建议下次体检项目(检查意义/建议详情)"表、
# "微量元素检验解读(临床意义/生理功能)"表、"个人历次体检档案记录"表 —— 行式与信号通道
# 会从中采出"人体成分分析|18"(序号当结果, 'A' 级别码当标志)、"密切关注|3"、
# "状腺癌等疾病密切关注|2" 等 ~60 条垃圾; 坐标列式通道本就不产这些。
# 按页签名(两个表头词同行页)或页级表名单整页跳过两个文本通道。
_NON_IND_PAGE_SIGS = (("分类项目", "结论词"), ("检查意义", "建议详情"),
                      ("临床意义", "生理功能"),
                      # 2026-10-16: 心脑血管病风险评估页(风险等级/建议 prose)
                      ("心脑血管病", "风险评估"))
_NON_IND_PAGE_LINE_RE = re.compile(
    r"^\s*(?:个人历次体检档案记录|体检分级提示|分级标准|强烈建议下次体检项目|"
    r"微量元素检验解读)\s*$")


def _is_non_indicator_table_page(page_lines: list) -> bool:
    txts = [str(t).strip() for t in page_lines if str(t).strip()]
    for a, b in _NON_IND_PAGE_SIGS:
        if any(a in t for t in txts) and any(b in t for t in txts):
            return True
    return any(_NON_IND_PAGE_LINE_RE.match(t) for t in txts)


_Q_REF_RE = re.compile(r"^[<>≤≥]?\s*[\d.]+\s*[-~—～]\s*[\d.]+$|^[<>≤≥]\s*[\d.]+$")
_Q_UNIT_RE = re.compile(r"^[A-Za-z%μµ/^0-9.\-]{1,14}$")
_Q_VAL_RE = re.compile(
    r"^(?:[<>≤≥]?\s*[\d.]+|[+\-]{1,3}|阴性\(?[+-]?\)?|阳性\(?[+-]?\)?|"
    r"弱阳性|未检出|[Nn]eg(?:ative)?|正常)$")


def _extract_dump_quartet_rows(lines: list) -> list[dict]:
    """单元格转储四元组解析(2026-10-13, 漳州市医院): PDF 文本层把化验表按单元格逐行
    导出为 "名称 / 参考值 / 单位 / 结果( / 标志)" 四行一组(如 "*钾\n3.5-5.3\nmmol/L\n3.86"),
    行式/列式均不识别 → 单位丢失; 本通道严格匹配四行形态确定性补行/补列。
    2026-10-13: 跟踪最近表标题(尿常规/粪便常规) —— 裸"隐血"按所属表命名
    ("尿隐血"/"粪便隐血"), 否则尿/粪两处隐血同名(用户反馈无法区分)。"""
    out: list[dict] = []
    _ctx = ""
    for i in range(len(lines) - 3):
        if re.search(r"尿常规|尿液|尿沉渣", lines[i]):
            _ctx = "urine"
        elif re.search(r"粪便|大便|粪常规|便常规", lines[i]):
            _ctx = "stool"
        nm, ref, unit, val = lines[i], lines[i + 1], lines[i + 2], lines[i + 3]
        if not _NAME_RE.match(nm) or _skip_name(nm) or _is_value_line(nm):
            continue
        if not _Q_REF_RE.match(ref):
            continue
        if not _Q_UNIT_RE.match(unit) or not _Q_VAL_RE.match(val):
            continue
        lo, hi = _parse_ref(ref)
        name = nm.lstrip("*＊#").strip()
        if name in ("隐血", "潜血"):
            name = "尿隐血" if _ctx == "urine" else ("粪便隐血" if _ctx == "stool" else name)
        out.append({"item_name": name, "result": val,
                    "unit": unit, "ref_low": lo, "ref_high": hi})
    return out


_Q_DASH_RE = re.compile(r"^[-—–]{1,3}$")
_Q_QREF_RE = re.compile(r"^(?:阴性|阳性|弱阳性|正常)\(?[+-]?\)?$")


def _extract_reversed_quartet_rows(lines: list) -> tuple:
    """倒序行组 [结果][标志?][参考?][单位?][名称] 解析(2026-10-14, 任晓光 OCR 导出序:
    "14 / 0 - 40 / IU/L / 谷草转氨酶")。**二期(同日)**: 变长窗口 —— 名称行前的全部
    cell(1..4 个)组成该行, 无单位/无范围行("1.030 / 1.003 - 1.030 / 尿比重")同样解析
    (旧固定四/五元组把这些行漏给通用行式 → "名配下一行值"逐行错位)。返回
    (rows, consumed_indices) —— 消费行须从行式输入剔除。"""
    rows: list[dict] = []
    used: set = set()
    prev = -1  # 上一个名称行下标(下一名称的组左界)
    for i, ln in enumerate(lines):
        s = (ln or "").strip()
        if (not s or not _NAME_RE.match(s) or _skip_name(s) or _is_value_line(s)):
            continue
        # 2026-10-14: 单位样词("Cell/uL")不是名称 —— 否则它吃掉前一组的
        # [值,参考] 变成垃圾行, 真正的名称行反而丢组(任晓光尿潜血)
        if re.search(r"/(?:uL|μL|ul|HP|LP|hpf|lpf)$", s, re.I):
            prev = i
            continue
        _left = prev + 1
        group = [(k, (lines[k] or "").strip()) for k in range(_left, i)]
        prev = i
        # 组内丢弃注释行(检查医生:/条码号 等)与空行, 不计入形态
        cells = [c for _k, c in group
                 if c and not _skip_name(c) and not re.search(r"[:：]", c)]
        if not (2 <= len(cells) <= 4):
            continue
        unit = None
        if (_Q_UNIT_RE.match(cells[-1]) and re.search(r"[A-Za-z%μµ]", cells[-1])
                and not _Q_DASH_RE.match(cells[-1])):
            unit = cells[-1]
            cells = cells[:-1]
        val = cells[0]
        flag = ref = None
        ok = bool(_Q_VAL_RE.match(val))
        for c in cells[1:]:
            if flag is None and (_ARROW_RE.match(c) or c in ("*", "＊")
                                 or _FLAG_TEXT_RE.match(c)):
                flag = c
            elif ref is None and (_Q_REF_RE.match(c) or _Q_DASH_RE.match(c)
                                  or _Q_QREF_RE.match(c)):
                ref = c
            else:
                ok = False
                break
        if not ok:
            continue
        lo = hi = None
        if ref and (_Q_REF_RE.match(ref) or _Q_QREF_RE.match(ref)):
            lo, hi = _parse_ref(ref)
        _row = {"item_name": s.lstrip("*＊#").strip(), "result": val,
                "unit": unit, "ref_low": lo, "ref_high": hi}
        if flag:
            _row["signal_flag"] = 2 if _ARROW_RE.match(flag) else 3
        rows.append(_row)
        used.update(range(_left, i + 1))
    return rows, used


def extract_indicator_rows(text: str, keep_bare_arrow: bool = False,
                           reversed_dump: bool = False,
                           latin_wrap: bool = False) -> list[dict]:
    """按行扫描, 提取(名称, 结果, 单位, 参考范围)对。支持行内/分行/定性值/序号制/名称折行。

    keep_bare_arrow=True(扫描件): 裸箭头仍作本行标志; False(文本件): 交信号通道同排配对。
    """
    text = _strip_history_compare_table(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = _merge_unclosed_paren_lines(lines)
    lines = _merge_unclosed_square(lines)
    lines = _merge_para_wrap(lines)
    lines = _merge_wrapped_names(lines, latin_wrap=latin_wrap)
    lines = _merge_range_fragments(lines)
    # 2026-10-06: 非指标表页整体剔除(陈霓结论词表/建议项目表/解读表/档案表)
    if lines:
        _kept: list[str] = []
        _page: list[str] = []
        for _ln in lines:
            if _ln.startswith("--- "):
                if _page and not _is_non_indicator_table_page(_page):
                    _kept.extend(_page)
                _page = [_ln]
            else:
                _page.append(_ln)
        if _page and not _is_non_indicator_table_page(_page):
            _kept.extend(_page)
        lines = _kept
    # 2026-10-14: 倒序四元组先解析并从行式输入剔除消费行(防"名配下一行值"错位)。
    # **按院开关**(江西边检站): 该模式在其它院校验为高误配(冻库 11 处), 默认关。
    _rev_rows: list = []
    if reversed_dump:
        _rev_rows, _rev_used = _extract_reversed_quartet_rows(lines)
        if _rev_used:
            lines = [ln for _i, ln in enumerate(lines) if _i not in _rev_used]
    # 2026-09-07: rev/dual 块表区由 extract_column_table_rows 整块接管, 行式不参与
    # (厦门弘爱双值表: 行式会把"参考列/说明行/上次值"当指标; 齐鲁反序表同理)
    if lines:
        _drop = sorted((a, b) for a, b, _m in _block_windows(lines))
        if _drop:
            kept: list[str] = []
            prev = 0
            for a, b in _drop:
                kept.extend(lines[prev:a])
                prev = b
            kept.extend(lines[prev:])
            lines = kept
    out: list[dict] = []
    _consumed_ref_lines: set[int] = set()  # 已被某值行后向消费的 range 行号(防前置 ref 误配)
    # 2026-09-07: 参考列在结果列前的表(茂名)。2026-10-07: 文本序误报时用数据行序降级(白炜)
    _rev_ref = _text_has_reversed_ref(lines)
    if _rev_ref and _data_order_clearly_normal(lines):
        _rev_ref = False
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        # 2026-08-29: 桂林"名称结果高/低(值 单位)"行内标志(一行可多个,
        # 如"高密度脂蛋白结果低(1.47 mmol/L) 低密度脂蛋白结果高(2.77 mmol/L)")
        m3s = list(re.finditer(
            r"([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）·\-/]{0,20}?)结果(高|低)\(([\d.]+)",
            ln,
        ))
        if m3s:
            for m3 in m3s:
                if _skip_name(m3.group(1)):
                    continue
                out.append({
                    "item_name": m3.group(1).strip(),
                    "result": m3.group(3),
                    "unit": "",
                    "ref_low": None, "ref_high": None,
                    "signal_flag": 3,  # "结果高/结果低" = 报告方标志
                })
            i += 1
            continue
        # 行内模式: 名称 + 数字(可能带单位/范围)在同一行
        m = re.match(r"^([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·#\-/ ]{0,20}?)[\s:：]+([\d.]+)", ln)
        if m and not _skip_name(m.group(1)):
            rest = ln[m.end():]
            # 2026-09-05: 签名/日期字段("吴净瑛 2026-08-14 总检:…" "打印日期:2026-07-31")
            # 值 = 年份 + 余下 "-MM-DD" 日期形态 → 非指标, 跳过
            if re.match(r"^\s*-?\s*\d{1,2}\s*[-/]\s*\d{1,2}", rest) \
                    and re.match(r"^\d{2,4}$", m.group(2)):
                i += 1
                continue
            # 2026-09-07: 值后紧跟"、数字汉字"列举(健康科普"尿酸偏高:1、血尿酸偏高…")
            if re.match(r"^[、，,、]\s*[\u4e00-\u9fa5]", rest):
                i += 1
                continue
            # 2026-09-28: 值后紧接范围分隔符+数字 = 参考范围折行片段(性激素参考
            # "期:4.54-22.51，黄体"), 不是"名称:结果" → 不产行(毕惠丽 visual 文本)
            if re.match(r"^\s*[-~～至–—]\s*[\d.]", rest):
                i += 1
                continue
            name = m.group(1).strip()
            # 2026-09-05: 名称尾词清洗 —— "…偏高 (5.75" 等综述残行(未走全扫的形态)名称
            # 会被名称组吞入"偏高 (", 剥掉异常词尾缀与悬挂括号。
            name = re.sub(r"(增高|升高|偏高|降低|偏低|异常|阳性|增快|过重|减少|偏轻|超重|肥胖)\s*[（(]?$", "", name).strip()
            rest = ln[m.end():]
            # 单位清洗: 剥括号注释/箭头/多空格("IU/ml(0--34.00 IU/ml) ↑" → "IU/ml")
            unit = re.split(r"[（(]|[↑↓]|\s{2,}", rest)[0].strip()[:12]
            ref_lo, ref_hi = _parse_ref(rest)
            if name and not _skip_name(name) and not _junk_row_cell(name, unit, m.group(2)):
                out.append({"item_name": name, "result": m.group(2), "unit": unit,
                            "ref_low": ref_lo, "ref_high": ref_hi})
            i += 1
            continue
        # 分行模式: 当前行是值行(含符号值">1000[..]"/半定量"+1" 形态)
        # 2026-09-15: 结果与箭头同行("17.6 ↓")也是值行(嘉兴市中医院陈镜霓)
        _va, _vaf = _value_with_arrow(ln)
        _norm_v = None if i == 0 else _norm_value_cell(ln)
        if (_is_value_line(ln) or _norm_v or _vaf) and i > 0:
            prev = lines[i - 1]
            prev_is_name = (
                _NAME_RE.match(prev) and not _skip_name(prev)
                and not _is_value_line(prev) and not _PAREN_ONLY_RE.match(prev)
            )
            # 2026-09-02: 名称与值之间夹括号折行(广西人民"乙型肝炎表面抗体\n(定量)\n448.08"):
            # 括号行不是名称/值/单位/范围 → 原名配对失败、ref 丢失。跳过括号行再配对。
            # 2026-09-05: 括号含 ASCII 英文(如"(A-TPO)")= 名称的英文简称 → 并入名称;
            # 中文括号行("(定量)")维持跳板(避免 key 与列式/归一不一致)。
            name_offset = 1
            paren_part = ""
            if not prev_is_name and (_PAREN_ONLY_RE.match(prev) or _LATIN_PAREN_RE.match(prev)) and i >= 2:
                prev2 = lines[i - 2]
                if (_NAME_RE.match(prev2) and not _skip_name(prev2)
                        and not _is_value_line(prev2) and not _PAREN_ONLY_RE.match(prev2)):
                    prev = prev2
                    name_offset = 2
                    prev_is_name = True
                    if re.search(r"[A-Za-z]", lines[i - 1]):
                        paren_part = lines[i - 1]
            # 2026-09-07: 反列序表 提示列箭头夹在 名称 与 值 之间(茂名
            # "[ref]\n肌酐\n↑\n115.4\nμmol/L") → 值行前一行是箭头, 再前一行才是名称
            if not prev_is_name and _ARROW_RE.match(prev) and i >= 2:
                prev2 = lines[i - 2]
                if (_NAME_RE.match(prev2) and not _skip_name(prev2)
                        and not _is_value_line(prev2)):
                    prev = prev2
                    name_offset = 2
                    prev_is_name = True
                    row_flag = 2  # 箭头归属本指标(值行前箭头 = 报告方异常标志)
            _above_flag = False
            if prev_is_name and _rev_ref:
                # 2026-09-22: **反序表**"名称上方一行是提示列文字/箭头"(郑州 糖化血红蛋白:
                # 参考值 4.0--6.0 / 偏高 / 糖化血红蛋白 / 6.2) → 本行标志=2。
                # 必须限定反序表(_rev_ref): 正序表名称上方常是上一指标的提示/参考值,
                # 无条件应用会给整表加假黄(护栏回归 崇左/贵港/防城港等 8 份)。
                _above = lines[i - name_offset - 1] if i - name_offset - 1 >= 0 else ""
                if _ARROW_RE.match(_above) or _FLAG_TEXT_RE.match(_above):
                    _above_flag = True
            if not prev_is_name:
                # 序号行(北京序号制表格): 上一行不是名称(是表头/范围/上一值)
                # 而当前是小整数序号 → 跳过
                i += 1
                continue
            # 结果行: 上一行是名称行 → 配对(两列表/三列表/序号制的值都在名称后)
            # 2026-10-07: [名][<参考单限][单位][结果][↑] dump 序(陈刚乙肝两对半表,
            # 视觉列序正常但文本序把参考甩到结果前) —— 首数值行是参考单限、其后果值才
            # 是结果; 交换(旧行为把参考当结果, 真结果被当"单数历史列"跳过)。
            _lim_swap = None
            _m_lim = re.fullmatch(r"([<>≤≥])\s*([\d.]+)", ln.strip())
            if _m_lim and i + 2 < n:
                _u2 = lines[i + 1].strip()
                _v2 = lines[i + 2].strip()
                if _UNIT_RE.match(_u2) and re.fullmatch(r"[\d.]+", _v2):
                    _lim_swap = (_v2, _u2)
            m2 = _VALUE_RE.match(ln)
            _norm_result = _norm_v if (m2 is None and _norm_v) else None
            unit = m2.group(2).strip() if m2 else ""
            # 2026-09-28: "130 阳性"式尾随定性词不是单位(_VALUE_RE 把空格后汉字当单位)
            if unit and _QUAL_RE.match(unit):
                unit = ""
            # 2026-09-05: 值行后 单位/参考范围/箭头 顺序循环消化(马鞍山序号制含
            # "历史结果"单数列: "13.70|13.50|↓|15.5--18.1|fL" —— 单数行跳过,
            # 继续向后找真 ref/unit)
            ref_low = ref_high = None
            row_flag = 2 if _vaf else 0  # 值自带箭头 = 报告方异常标志
            if _above_flag:
                row_flag = max(row_flag, 2)  # 名称上方提示列文字/箭头归属本行
            # 2026-09-07: 反列序表(茂名 "[ref]\n名称\n[↑]\n值\n单位"): 名称前一行
            # RANGE = 本指标参考 → 优先于向后消化(值后 range 属下一指标, 曾错配假黄)
            prev_ref_idx = i - name_offset - 1
            prev_ref_avail = (
                _rev_ref
                and prev_ref_idx >= 0
                and prev_ref_idx not in _consumed_ref_lines
                and _RANGE_RE.match(lines[prev_ref_idx])
            )
            j = i + 1
            taken = 0
            while j < n and taken < 5:
                lj = lines[j]
                taken += 1
                if _is_block_name(lj) and not _is_value_line(lj):
                    break  # 2026-09-07: 值后消化遇下一指标名称即断(莆田 pH 行曾
                    # 被当单位/"6.0"被当历史列, 4.5-8.0 被误配给 维生素C)
                # 2026-10-07: 表格空占位符("--"/"—")跳过继续消化 —— 陈东海尿常规
                # [名][值][--][参考][--] 的占位符曾让循环 break, 参考"4.5-8.0"丢失
                if re.fullmatch(r"[-—–─_·]{1,3}", lj.strip()):
                    j += 1
                    continue
                if _ARROW_RE.match(lj):
                    # 2026-09-15: 文本件裸箭头交坐标信号通道(高帅); 扫描件仍作本行标志
                    if keep_bare_arrow:
                        row_flag = max(row_flag, 2)
                    j += 1
                    continue
                if _FLAG_TEXT_RE.match(lj) and not (
                        unit and unit.rstrip().endswith("/")
                        and re.fullmatch(r"[A-Za-zμµ]{1,3}", lj.strip())):
                    # 提示列文字("偏高"/"H" 等)夹在 值 与 单位/ref 之间(滨州/德宏) → 异常信号
                    # 2026-10-19: 单位折行尾行("mmol/" 后的 "L", 韩先锋)不是标志
                    row_flag = max(row_flag, 2)
                    j += 1
                    continue
                # 2026-10-14: 结果后独立"弱阳性"注释行(敦煌"异常描述"列: 纪卓武
                # 尿蛋白|＋－|弱阳性) = 报告方异常标注 → flag3; 仅当结果含 ± 形态时
                # 认定(程永春/毕惠丽 结果列"弱阳性"不经此路径, 零漂移)。
                # 2026-10-19(封猛反馈): 结果"-"(阴性/无结果)时, 后续"弱阳性"是
                # **参考范围列**(尿胆原 参考"弱阳性"), 不是异常标注 —— 仅半定量
                # 形态(±/"+-")才算报告方标注
                if lj.strip() == "弱阳性" and re.search(
                        r"[±]|[+＋]\s*[-－]|[-－]\s*[+＋]", ln):
                    row_flag = max(row_flag, 3)
                    j += 1
                    continue
                # 2026-10-19(韩先锋): 半定量结果后的纯整数 = 报告方计数值
                # (潜血 2+ / 50 Ery/ul, 51 显示在"结果"列) → 异常标注判黄
                if (re.search(r"[+±＋]", ln)
                        and re.fullmatch(r"\d{1,4}", lj.strip())
                        and ref_low is None and ref_high is None and not unit):
                    row_flag = max(row_flag, 3)
                    j += 1
                    continue
                if _RANGE_RE.match(lj):
                    if (ref_low is None and ref_high is None) and _rev_ref:
                        break  # 反列序表: 参考列在名称前, 值后 range 属下一指标, 不向后消费
                    lo, hi = _parse_ref(lj)
                    if lo or hi:
                        if ref_low is not None or ref_high is not None:
                            break  # ref 已设 → 后续 range 属下一指标(崇左 比重后接 pH ref)
                        ref_low, ref_high = lo, hi
                        if lj.startswith(("↑", "↓")):
                            row_flag = max(row_flag, 2)  # "↑0～10.0": 参考行带箭头前缀 = 异常
                        _consumed_ref_lines.add(j)
                        j += 1
                        continue
                    j += 1  # 单数(历史结果列) → 跳过
                    continue
                # 2026-09-07: 参考范围带单位同行(厦门华西 "0.29-1.70mmol/L"/"≤5ng/ml",
                # "100-30010^9/L" 粘连) → 剥单位后解析 ref, 单位补空
                _ru = _range_with_unit(lj)
                if _ru and (ref_low is None and ref_high is None):
                    rlo, rhi, runit = _ru
                    ref_low, ref_high = rlo, rhi
                    _consumed_ref_lines.add(j)
                    if not unit and runit:
                        unit = runit
                    if lj.startswith(("↑", "↓")):
                        row_flag = max(row_flag, 2)
                    j += 1
                    continue
                if _UNIT_RE.match(lj):
                    # 2026-10-19: 单位被折行("mmol/" + "L", 韩先锋) → 续接
                    if unit and unit.rstrip().endswith("/") and re.fullmatch(
                            r"[A-Za-zμµ]{1,3}", lj.strip()):
                        unit = unit.rstrip() + lj.strip()
                        j += 1
                        continue
                    if ref_low is not None or ref_high is not None:
                        # 2026-09-28: ref 后的"单位样"行需再判 —— 下一行是值说明它其实
                        # 是下一指标名("*pH"/"5.00", 崇左) → 断; 否则是纯单位行
                        # ("%"、单吉飞 血常规 VALUE/REF/UNIT 顺序) → 补单位。
                        _nxt = lines[j + 1].strip() if j + 1 < n else ""
                        if _is_value_line(_nxt) or _norm_value_cell(_nxt):
                            break
                    if not unit:
                        unit = lj.lstrip("*＊").strip()
                    j += 1
                    continue
                break
            # 2026-09-05: 参考范围列在名称**前**的排版(滨州/德宏反列序表格):
            # "59--104\n▲★尿酸\n478\n偏高" —— 名称前一行 RANGE 且未被任何值行消费 → 归属本行。
            prev_ref_idx = i - name_offset - 1
            if (ref_low is None and ref_high is None
                    and prev_ref_idx >= 0
                    and prev_ref_idx not in _consumed_ref_lines
                    and _RANGE_RE.match(lines[prev_ref_idx])):
                plo, phi = _parse_ref(lines[prev_ref_idx])
                if plo or phi:
                    ref_low, ref_high = plo, phi
                    _consumed_ref_lines.add(prev_ref_idx)

            result_v = m2.group(1) if m2 else (_norm_result or (_va if _vaf else ln))
            if _lim_swap:
                _lo2, _hi2 = _parse_ref(ln.strip())
                ref_low, ref_high = _lo2, _hi2
                result_v, unit = _lim_swap[0], _lim_swap[1]
            # 2026-09-28: 数值+中文定性后缀("130 阳性") → 结果只留数值, 定性转弱标志
            _qv, _qual = _split_qual_tail(result_v)
            if _qual:
                result_v = _qv
                if _qual in ("阳性", "弱阳性", "±"):
                    row_flag = max(row_flag, 1)
            _nm = _strip_refcell_prefix((prev + paren_part).lstrip("★*＊▲△"))
            if _junk_row_cell(_nm, unit, result_v):
                i += 1
                continue
            row = {"item_name": _nm, "result": result_v, "unit": unit,
                   "ref_low": ref_low, "ref_high": ref_high}
            if row_flag:
                row["signal_flag"] = row_flag
            out.append(row)
            i += 1
            continue
        i += 1
    # 层3校验: 名称+结果齐全; 去重(名称,结果)
    seen: dict = {}
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            # 2026-09-07: 同 key 行取信息更全者 —— 综述行内命中(无 ref/flag)
            # 先出现会顶掉表格行(厦门华西"甘油三酯:2.06mmol/L↑…"综述先于表格)。
            old = deduped[seen[key]]
            old_has = (old.get("ref_low") or old.get("ref_high")) or old.get("signal_flag")
            new_has = (row.get("ref_low") or row.get("ref_high")) or row.get("signal_flag")
            if new_has and not old_has:
                deduped[seen[key]] = row
            elif new_has and old_has:
                if (row.get("ref_low") or row.get("ref_high")) and not (old.get("ref_low") or old.get("ref_high")):
                    deduped[seen[key]]["ref_low"] = row.get("ref_low")
                    deduped[seen[key]]["ref_high"] = row.get("ref_high")
                if row.get("signal_flag"):
                    deduped[seen[key]]["signal_flag"] = max(old.get("signal_flag", 0), row.get("signal_flag", 0))
            continue
        seen[key] = len(deduped)
        deduped.append(row)
    # 2026-08-31: 建议/科室文本残留("脂肪肝健康管理门诊" 建议行、
    # "瞬时弹性成像检查脂肪肝CAP值稍高(" 检查名)不是指标
    deduped = [
        r for r in deduped
        if not re.search(r"门诊|弹性成像|CAP值", r["item_name"])
    ]
    # 2026-10-13: 单元格转储四元组补行/补列(漳州市医院; 字段合并到同键既有行)
    try:
        _quat = _extract_dump_quartet_rows(lines)
    except Exception:
        _quat = []
    _q_by = {(r.get("item_name"), r.get("result")): r for r in deduped}
    for _r in list(_rev_rows) + list(_quat):
        _k = (_r.get("item_name"), _r.get("result"))
        _cur = _q_by.get(_k)
        if _cur is None:
            deduped.append(_r)
            _q_by[_k] = _r
        else:
            for _fld in ("unit", "ref_low", "ref_high"):
                if not _cur.get(_fld) and _r.get(_fld):
                    _cur[_fld] = _r[_fld]
    return deduped


def extract_indicators_from_pdf(pdf_path: str) -> list[dict]:
    import fitz
    doc = fitz.open(pdf_path)
    text = "".join(p.get_text() for p in doc)
    doc.close()
    return extract_indicator_rows(text)


# === 列式表格解析(2026-08-28): 广西模板"项目名称|检查结果|单位|参考范围|提示" ===
# fitz 文本提取把列式表格打散成逐行文本(名称/值/单位/ref/提示 每指标一个连续块)。
# 以表头序列为锚, 块内按 名称→值→单位→ref→提示 五元组聚合。
# 判定口径(2026-08-28 决策): 标志权威 —— 提示列/标志列的异常标志(偏高/↑/H...)
# 直接判黄(signal_flag=3, 不做 ref 数值复核); 弃检类不入库。
_COL_HEADER_START_RE = re.compile(
    r"^(项目名称|指标名称|检查项目|检验项目|检查内容|检查项目名称|测定项目|序号项目名称)$"
)
_COL_HEADER_CELL_RE = re.compile(
    r"^(检查结果|结果|测定值|单位|参考范围|参考值|正常值|提示|标志|临床意义|历史结果|提示参考范围)$"
)
# 2026-09-12(用户口径): 判异常看**标志/提示列** —— 有 "±" 符号才算异常;
# "弱阳性" 是结果值描述, 标记列非 ± 时不判异常(池州维生素C 的标志列是 ± 才黄)。
_FLAG_TEXT_RE = re.compile(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|A|\*|±)$")
_FLAG_ABNORMAL_RE = re.compile(
    r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|A|\*|±|增高↑|升高↑|降低↓|偏低↓)$"
)
_FLAG_NORMAL_RE = re.compile(r"^(正常|未见异常|无异常|阴性|弱阳性|阴性[（(]-?[)）]|阳性[（(]-?[)）]|-)$")
_FLAG_SKIP_RE = re.compile(r"^(弃检|未检|拒检|放弃|未查|无法完成|未测)$")
_SIGN_LINE_RE = re.compile(
    r"(检验师|检查医生|检查医师|审核医生|审核人|录入者|报告医师|主检|总检|签名|"
    r"日期[:：]\s*\d|第\s*\d+\s*页|共\s*\d+\s*页)"
)


def _assemble_column_row(name: str, block: list[str],
                         keep_bare_arrow: bool = False) -> Optional[dict]:
    """五元组聚合: 名称 + [值|单位|ref|提示]。弃检不入库; 异常标志 → signal_flag=3。

    2026-08-29: 数字类行(含 > 前缀值 ">1000")统一按"首个=result, 其后=ref"分流。
    keep_bare_arrow=True(扫描件: 信号通道无文本层可用)时裸 ↑/↓ 仍作本行标志;
    文本件由信号通道按同排坐标定夺, 此处跳过裸箭头防错配(高帅)。
    """
    row = {"item_name": name, "result": "", "unit": "", "ref_low": None, "ref_high": None,
           "signal_flag": 0}
    for b in block:
        if _FLAG_SKIP_RE.match(b):
            return None  # 弃检/未检: 不入库
        # 2026-09-15: 文本件裸 ↑/↓ 由信号通道按同排(y)坐标配对定夺 —— 文本序下"箭头
        # 在下一行名称前"的版面会把箭头错配给上一行(高帅 血小板压积/甘油三酯 假黄);
        # 扫描件无文本层(信号通道为空), 仍按列式文本序作本行标志(钦州二 钾(K))
        if not keep_bare_arrow and _ARROW_RE.match(b):
            continue
        if _FLAG_ABNORMAL_RE.match(b):
            row["signal_flag"] = 3
            continue
        if _FLAG_NORMAL_RE.match(b):
            continue
        if _is_value_line(b) and not row["result"]:
            m = _VALUE_RE.match(b)
            if m and m.group(2):
                row["result"], row["unit"] = m.group(1), m.group(2).strip()
            elif m:
                row["result"] = m.group(1)
            else:
                row["result"] = b
            continue
        nvc = _norm_value_cell(b)
        if nvc and not row["result"]:
            row["result"] = nvc
            continue
        if re.match(r"^[<>≤≥~～]?\s*[\d.]+", b):
            # 数字类: 首个(含 > 前缀值如 "> 1000")为 result, 其后为 ref
            if not row["result"]:
                # 2026-08-31: 保留 >/< 符号(乙肝表面抗体 ">1000" 前端需显示符号);
                # ~～ ≤≥ 开头视为单限 ref 风格, 不当作 result 符号
                m = re.match(r"^([<>≤≥]?)\s*([\d.]+)", b)
                if not m:
                    continue
                row["result"] = (m.group(1) if m.group(1) in ("<", ">") else "") + m.group(2)
            else:
                lo, hi = _parse_ref(b)
                if lo or hi:
                    row["ref_low"], row["ref_high"] = lo, hi
            continue
        if _UNIT_RE.match(b) and not row["unit"]:
            row["unit"] = b
    if not row["result"]:
        return None
    return row


def extract_column_table_rows(text: str, keep_bare_arrow: bool = False) -> list[dict]:
    """列式表格解析: 表头序列识别 → 块聚合。返回含 signal_flag 的行。

    2026-09-07: 支持两类"单元格 dump 顺序与视觉列序相悖"的新模板, 由表头词汇分派:
      - rev(齐鲁青岛): 视觉列 = 检查项目名称|检查结果|参考值|单位|异常标识,
        dump 序 = [异常标识][单位][参考值][检查结果][项目名](名在块尾)。
      - dual(厦门弘爱): 视觉列 = 检验项目|本次结果|上次结果|参考值,
        dump 序 = [项目名][参考*][上次值][本次值]; 异常标志 (↑/↓/*) 以括号挂在值后,
        "本次结果" = 块内最后一个值。
    两种模式产出行带 "__auth" 键, service 组装据此抑制行式/信号通道对表内名称的错配。
    """
    text = _strip_history_compare_table(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = _merge_unclosed_paren_lines(lines)
    lines = _merge_unclosed_square(lines)
    lines = _merge_para_wrap(lines)
    out: list[dict] = []
    i, n = 0, len(lines)
    # 2026-09-07: 反列序表(参考值列在检查结果列前, 茂名人民) —— 常规列式前向
    # 聚合会把"值后的下一指标 ref"当本指标 ref(col 覆盖行式正确结果);
    # 该形态由行式反列序逻辑(_rev_ref)承担, 常规 col 不再参与(rev/dual 块模式不受影响)。
    _col_rev_ref = _text_has_reversed_ref(lines)
    while i < n:
        if not (_COL_HEADER_START_RE.match(lines[i]) or lines[i] in ("本次结果", "上次结果")):
            i += 1
            continue
        # 表头词收集: START 命中行 ±6 行内找表头词汇(容忍 dump 乱序/夹杂)
        lo = max(0, i - 6)
        hi = min(n, i + 6)
        words: list[str] = []
        for t in range(lo, hi):
            x = lines[t]
            if (_COL_HEADER_START_RE.match(x) or _COL_HEADER_CELL_RE.match(x)
                    or x in ("本次结果", "上次结果", "异常标识")):
                words.append(x)
        mode = None
        if "异常标识" in words:
            mode = "rev"
        elif "本次结果" in words and "上次结果" in words:
            mode = "dual"
        if not mode and _col_rev_ref:
            # 反列序常规表: 跳过(行式反列序承担), 直到下一表头
            i += 1
            while i < n and not (_COL_HEADER_START_RE.match(lines[i])
                                 or lines[i] in ("本次结果", "上次结果")):
                i += 1
            continue
        if not mode:
            # 常规表头(原逻辑): 要求 START 行后跟 CELL 词
            j = i + 1
            header_cells: list[str] = []
            while j < n and len(header_cells) < 6 and (
                _COL_HEADER_CELL_RE.match(lines[j]) or len(lines[j]) <= 2
            ):
                if _COL_HEADER_CELL_RE.match(lines[j]):
                    header_cells.append(lines[j])
                j += 1
            if len(header_cells) < 2:
                i += 1
                continue
            k = j
            while k < n:
                ln = lines[k]
                if _COL_HEADER_START_RE.match(ln):
                    break
                if _SIGN_LINE_RE.search(ln):
                    k += 1
                    continue
                if _NAME_RE.match(ln) and not _skip_name(ln) and not _is_value_line(ln):
                    name = _strip_refcell_prefix(ln.lstrip("★*＊▲△").strip())
                    block: list[str] = []
                    m2 = k + 1
                    # 2026-09-05: 名称与值之间夹英文简称括号行("(A-TPO)")→ 并入名称
                    if m2 < n and _PAREN_ONLY_RE.match(lines[m2]) and re.search(r"[A-Za-z]", lines[m2]):
                        name += lines[m2].strip()
                        m2 += 1
                    while m2 < n and len(block) < 5:
                        b = lines[m2]
                        if _COL_HEADER_START_RE.match(b):
                            break
                        if re.match(r"^[*＊]\s*[A-Za-z]{1,8}$", b):
                            break  # 2026-09-05: 星号+纯字母 = 下一镜检/尿检项标记("*pH"), 防止串块
                        if (_NAME_RE.match(b) and not _skip_name(b) and not _is_value_line(b)
                                and not _FLAG_NORMAL_RE.match(b) and not _FLAG_ABNORMAL_RE.match(b)):
                            break  # 参考/提示形态(阴性(-)/A/H/L)不算名称行, 收进块
                        if _SIGN_LINE_RE.search(b):
                            m2 += 1
                            continue
                        if (_UNIT_RE.match(b) or _RANGE_RE.match(b) or _is_value_line(b)
                                or _range_with_unit(b) is not None
                                or _norm_value_cell(b) is not None
                                or _FLAG_ABNORMAL_RE.match(b) or _FLAG_NORMAL_RE.match(b)
                                or _FLAG_SKIP_RE.match(b)):
                            block.append(b)
                            m2 += 1
                        else:
                            break
                    row = _assemble_column_row(name, block, keep_bare_arrow=keep_bare_arrow)
                    if row:
                        out.append(row)
                    k = m2
                    continue
                k += 1
            i = k
            continue
        # rev / dual: 整窗口交给块模式解析, 窗口 = 最后一个表头词行之后到下一表头词/START
        word_idx = [t for t in range(lo, hi)
                    if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                        or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        body_start = max(word_idx) + 1
        body_end = n
        for t in range(body_start, n):
            x = lines[t]
            if _COL_HEADER_START_RE.match(x) or x in ("本次结果", "上次结果", "异常标识") \
                    or (_COL_HEADER_CELL_RE.match(x) and t > body_start):
                body_end = t
                break
        if mode == "rev":
            _parse_rev_window(lines, body_start, body_end, out)
        else:
            _parse_dual_window(lines, body_start, body_end, out)
        i = body_end
    # 去重
    seen: dict = {}
    deduped = []
    for row in out:
        key = (row["item_name"], row["result"])
        if key in seen:
            # 2026-09-07: 同 key 行取信息更全者 —— 综述行内命中(无 ref/flag)
            # 先出现会顶掉表格行(厦门华西"甘油三酯:2.06mmol/L↑…"综述先于表格)。
            old = deduped[seen[key]]
            old_has = (old.get("ref_low") or old.get("ref_high")) or old.get("signal_flag")
            new_has = (row.get("ref_low") or row.get("ref_high")) or row.get("signal_flag")
            if new_has and not old_has:
                deduped[seen[key]] = row
            elif new_has and old_has:
                if (row.get("ref_low") or row.get("ref_high")) and not (old.get("ref_low") or old.get("ref_high")):
                    deduped[seen[key]]["ref_low"] = row.get("ref_low")
                    deduped[seen[key]]["ref_high"] = row.get("ref_high")
                if row.get("signal_flag"):
                    deduped[seen[key]]["signal_flag"] = max(old.get("signal_flag", 0), row.get("signal_flag", 0))
            continue
        seen[key] = len(deduped)
        deduped.append(row)
    return deduped


# === 2026-09-07: rev(名称在后, 齐鲁青岛)与 dual(双值, 厦门弘爱)窗口解析 ===
# 值/定性单元格(不含参考范围与说明): 数字(可带尾括号异常标志)/半定量 d+/
# 免疫定性"阳性[234.27]"/纯定性词。参考行("65.00～85.00 g/L"/"成人:≤24 U/L"/
# "阴性-[<0.05] IU/mL")走 _RANGE_RE/_range_with_unit 或落到 ref_rows, 不算值。
_DUAL_QUAL_RE = re.compile(
    r"^(?:阴性|阳性|弱阳性|正常|未检出|淡黄色|黄色|清澈|浑浊|阴性或弱阳性|正常范围)"
)
_DUAL_VALUE_RE = re.compile(
    r"^(?:[<>≤≥]?\s*[\d.]+|[1-5]\+)(?:[（(](?:↑|↓|\*)[)）])?$"
    r"|^(?:阴性|阳性)\[[^\]】]+\](?:[（(](?:↑|↓|\*)[)）])?$"
    r"|^第\d+项(?:阳性|阴性|弱阳性)(?:[（(]\*[)）])?$"  # 厦门弘爱 "乙肝两对半结论: 第2项阳性(*)"
)
_BLOCK_FLAG_TAIL_RE = re.compile(r"[（(](?:↑|↓|\*)[)）]$")
_SIGN_TAIL_RE = re.compile(r"^(?:已检|未检|弃检|拒检)$")
# 块级名称判定放宽: 兼容名称内嵌 ASCII 斜杠缩写与冒号
# ("AST/ALT比值(AST/ALT)" / "载脂蛋白B:A1(APOB:A)") —— 仅块模式内使用
_BLOCK_NAME_FULL_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9()（）/:·.%#+\-]{2,40}"
)



# === 2026-09-07: dual/rev 块表窗口检测(供行式剔除, 避免行式在块表区产错位行) ===
def _block_windows(lines: list[str]):
    """扫描全部表头, 返回 [(body_start, body_end, mode)], mode in {"rev","dual"}。"""
    wins: list[tuple] = []
    n = len(lines)
    i = 0
    while i < n:
        if not (_COL_HEADER_START_RE.match(lines[i]) or lines[i] in ("本次结果", "上次结果")):
            i += 1
            continue
        lo, hi = max(0, i - 6), min(n, i + 6)
        words = [lines[t] for t in range(lo, hi)
                 if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                     or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        mode = "rev" if "异常标识" in words else (
            "dual" if "本次结果" in words and "上次结果" in words else None)
        if not mode:
            i += 1
            continue
        word_idx = [t for t in range(lo, hi)
                    if (_COL_HEADER_START_RE.match(lines[t]) or _COL_HEADER_CELL_RE.match(lines[t])
                        or lines[t] in ("本次结果", "上次结果", "异常标识"))]
        start = max(word_idx) + 1
        end = n
        for t in range(start, n):
            x = lines[t]
            if _COL_HEADER_START_RE.match(x) or x in ("本次结果", "上次结果", "异常标识") \
                    or (_COL_HEADER_CELL_RE.match(x) and t > start):
                end = t
                break
        wins.append((start, end, mode))
        i = end
    return wins



# === 2026-09-07: 参考/结果 cell 残留前缀剥离 ===
# 莆田 26: 上一 cell "阴性(-)"(HBSAG 参考)与下一名称粘连成一行 "阴性(-)乙肝表面抗体"
_REFCELL_PREFIX_RE = re.compile(r"^(?:阴性|阳性|弱阳性)\s*[（(]\s*-?[)）]\s*")


def _strip_refcell_prefix(name: str) -> str:
    m = _REFCELL_PREFIX_RE.match(name)
    if m:
        name = name[m.end():].strip()
    return name


def _is_block_name(b: str) -> bool:
    if _NAME_RE.match(b) and not _skip_name(b):
        return True
    return (not _skip_name(b)
            and _BLOCK_NAME_FULL_RE.fullmatch(b)
            and re.search(r"[\u4e00-\u9fa5]", b))


def _parse_rev_window(lines: list[str], start: int, end: int, out: list[dict]) -> None:
    """反序块: dump = [异常标识][单位?][参考值?][检查结果][项目名](名在块尾)。
    以名称为锚向上取 ≤4 行: 紧邻上 = result, 再上 range 行 = ref, 单位行 = unit,
    块首 ↑/↓ = 异常标志。"""
    i = start
    while i < end:
        ln = lines[i]
        if _COL_HEADER_START_RE.match(ln) or ln in ("本次结果", "上次结果", "异常标识") \
                or _SIGN_LINE_RE.search(ln):
            i += 1
            continue
        if _is_block_name(ln) and not _is_value_line(ln) \
                and not _DUAL_QUAL_RE.match(ln):
            name = _strip_refcell_prefix(ln.lstrip("★*＊▲△").strip())
            ups: list[str] = []
            t = i - 1
            while t >= start and len(ups) < 4:
                if lines[t].startswith("--- Page "):
                    break  # 2026-09-07: 页脚/页眉行不参与反序块装配
                if _is_block_name(lines[t]) \
                        or _COL_HEADER_START_RE.match(lines[t]):
                    break
                ups.append(lines[t])
                t -= 1
            if not ups:
                i += 1
                continue
            result = None
            flag = 0
            unit = ""
            ref_lo = ref_hi = None
            # 紧邻名称上方 = 检查结果单元格
            v0 = ups[0]
            if re.match(r"^[<>≤≥]?\s*[\d.]+$", v0):
                result = v0.strip()
            elif _is_value_line(v0) and not _SIGN_TAIL_RE.match(v0) and v0 != "-":
                result = v0
            # 2026-09-08: 跨页镜检表(齐鲁青岛 P14 大便区, 表头在 P13 尾) —— 每名前
            # 2-3 单元格 = [结果/标志(↑/-)][单位 /HP][参考(0~1/无)], 名在块尾;
            # 与主体 5 列(含结果列)不同。组内含 "/HP" 时按 镜检形态 装配:
            #   '/HP' 远侧行 = 结果/标志(↑ 判黄), '/HP' 近侧行 = 参考。
            if "/HP" in ups and len(ups) <= 4:
                # 视觉行 = 名称|结果|参考(空)|单位 /HP|标志(↑/-): dump = [标志][/HP][结果][名]
                # 2026-09-08(用户复核): 结果 = 紧邻名称上方行(脂肪滴 "0~1"), ↑ 仅为标志,
                # 无参考范围(齐鲁青岛 大便镜检区; 红细胞/白细胞结果为 "无" 或 "-")。
                for u in ups:
                    if re.match(r"^[↑↓]$", u):
                        flag = 3
                unit = "/HP"
                cand = ups[0]
                if cand != "/HP" and not re.match(r"^[↑↓]$", cand):
                    result = cand
            if result:
                for u in ups[1:]:
                    if _UNIT_RE.match(u) and not unit:
                        unit = u
                    elif _RANGE_RE.match(u) or _range_with_unit(u) is not None:
                        if ref_lo is None and ref_hi is None:
                            rlo, rhi, runit = _range_with_unit(u) or (*_parse_ref(u), "")
                            ref_lo, ref_hi = rlo, rhi
                            if runit and not unit:
                                unit = runit
                for u in ups:
                    if re.match(r"^[↑↓]$", u):
                        flag = 3
                        break
            row = {"item_name": name, "result": result or "-", "unit": unit,
                   "ref_low": ref_lo, "ref_high": ref_hi, "signal_flag": flag,
                   "__auth": True}
            out.append(row)
            i += 1
            continue
        i += 1


def _parse_dual_window(lines: list[str], start: int, end: int, out: list[dict]) -> None:
    """双值块: dump = [项目名][参考*][上次值][本次值]; 本次 = 块内最后一个值。
    参考行在值区前(可为多行叙述/带单位/单限/定性), 值带 (↑/↓/*) = 异常标志。"""
    i = start
    while i < end:
        ln = lines[i]
        if (_COL_HEADER_START_RE.match(ln) or ln in ("本次结果", "上次结果", "异常标识")):
            break
        if _SIGN_LINE_RE.search(ln):
            i += 1
            continue
        if _is_block_name(ln) and not _is_value_line(ln) \
                and not _DUAL_QUAL_RE.match(ln):
            name = ln.lstrip("★*＊▲△").strip()
            if i + 1 < end and _PAREN_ONLY_RE.match(lines[i + 1]) and re.search(r"[A-Za-z]", lines[i + 1]):
                name += lines[i + 1].strip()
                i += 1
            ref_rows: list[str] = []
            vals: list[str] = []
            t = i + 1
            while t < end:
                b = lines[t]
                if (_COL_HEADER_START_RE.match(b) or b in ("本次结果", "上次结果", "异常标识")):
                    break
                if b.startswith("--- Page "):
                    break  # 2026-09-07: 跨页页眉/页脚行不属表格块(弘爱双值表跨页续排)
                if _SIGN_LINE_RE.search(b):
                    t += 1
                    continue
                if _is_block_name(b) and not _is_value_line(b) \
                        and not _DUAL_QUAL_RE.match(b):
                    break  # 下一指标名
                if _DUAL_VALUE_RE.match(b) or _DUAL_QUAL_RE.fullmatch(b):
                    vals.append(b)
                    t += 1
                    continue
                ref_rows.append(b)  # 参考/说明行("成人:≤24 U/L"、"合适水平:<5.2…")
                t += 1
            if vals:
                cur = vals[-1]  # 本次结果(末值)
                if len(vals) >= 3:
                    ref_rows.append(vals[0])  # 参考列亦为定性形态(尿检 阴性/阴性/1+(*))
                row = {"item_name": name, "unit": "", "ref_low": None, "ref_high": None,
                       "signal_flag": 0, "__auth": True}
                flag = 0
                if _BLOCK_FLAG_TAIL_RE.search(cur):
                    flag = 3
                    cur = _BLOCK_FLAG_TAIL_RE.sub("", cur)
                row["result"] = cur
                row["signal_flag"] = flag
                for rl in ref_rows:
                    m = _range_with_unit(rl)
                    if m:
                        rlo, rhi, runit = m
                        if (row["ref_low"] is None and row["ref_high"] is None):
                            row["ref_low"], row["ref_high"] = rlo, rhi
                        if not row["unit"] and runit:
                            row["unit"] = runit
                        continue
                    if _UNIT_RE.match(rl) and not row["unit"]:
                        row["unit"] = rl
                    elif (row["ref_low"] is None and row["ref_high"] is None):
                        lo2, hi2 = _parse_ref(rl)
                        if lo2 or hi2:
                            row["ref_low"], row["ref_high"] = lo2, hi2
                out.append(row)
            i += 1
            continue
        i += 1


# === 个人信息规则提取(2026-08-25) ===
# 替代 LLM 提取 name/gender/age/report_date/unit_name; 失败字段返回 None, 由调用方回退 LLM。
# 2026-08-26: 柳州/梧州等"表头行+值行错位"PDF 修复:
#   - 姓名候选排除表头词(性别/部门名称/单位/体检号码...)
#   - 单位后紧跟 男/女 时, 该值为姓名(柳州"单位: 石坤 男 41岁")
#   - 姓名: 后 80 字窗口内第一个非表头词(梧州"姓名: 性别: 男 团体: ... 谢国宾")
_HEADER_WORD_RE = re.compile(
    r"^(性别|年龄|电话|单位|部门名称|员工编号|体检号码|打印日期|团\s*体|体检类型|"
    r"证件号码|健康档案|登记日期|首检日期|检查日期|体检日期|体检时间|姓名|性别|"
    # 2026-09-22: 第四批(陈诚"南通市第一人"/华裕健"出生日期") —— 表头标签/机构名
    # 被当姓名; 精确匹配(非子串), 真名不受影响
    r"出生日期|日期|时间|地址|手机|编号|条码|打印时间|报告日期|审核日期|总检日期|"
    r"小结|结论|医师|尊敬的|"
    r"身份证|身份证号|科室|部门|医院|中心|公司|体检|门诊|检验|检查|影像|超声|心电|"
    r"报告|结果|项目|名称|类型|次数|序号|流水号|档案|人员|客户|受检|单位名称|工作单位|"
    r"健康管理中心|联系人|联系电话)$"
)


_BAD_NAME_RE = re.compile(
    r"(市|县|区|医院|中心|公司|日期|出生|体检|单位|健康|管理|学院|大学|银行|支队|"
    r"检查站|门诊|有限|电话|地址|编号|报告|尊敬)"
)
# 2026-09-22: 地址碎片尾缀(华裕健"东海大道"/"台州市椒江区") —— 真名不以这些词结尾
_BAD_NAME_TAIL_RE = re.compile(r"(大道|路|街|号|栋|楼|室|层|镇|村|组)$")


def _name_ok(cand: str) -> bool:
    """姓名合理性: 表头标签/机构名/日期词/地址碎片一律不算姓名(第四批 陈诚/华裕健)。"""
    return (bool(cand) and not _HEADER_WORD_RE.match(cand)
            and not _BAD_NAME_RE.search(cand) and not _BAD_NAME_TAIL_RE.search(cand))


def apply_redref_flags(indicators: list, signals: list) -> None:
    """2026-10-19(望京口径): 红字通道 raw="redref" —— 结果数值超出数值参考才判黄
    (f=1 再按参考复核), 红字但无参考/范围内不标; 红字信号不新建行。"""
    names = {(s.get("item_name") or "").strip() for s in signals
             if s.get("signal") == "redref" and (s.get("item_name") or "").strip()}
    if not names:
        return

    def _num(v):
        try:
            return float(str(v).strip().replace(",", ""))
        except Exception:
            return None

    for ind in indicators:
        if (ind.get("item_name") or "").strip() not in names:
            continue
        # 2026-10-19(陈阳反馈): 红字=报告方异常标注(PH 5 打印参考 4.6-8.0 仍标红);
        # 有参考范围的项按红字判黄; 无参考(身高/体重/腰围/臀围)保持绿
        lo, hi = _num(ind.get("ref_low")), _num(ind.get("ref_high"))
        if lo is None and hi is None:
            continue
        if (ind.get("signal_flag") or 0) < 2:
            ind["signal_flag"] = 2


def extract_name_from_layout(pdf_path: str) -> Optional[str]:
    """2026-10-19: 首页姓名栏坐标配对取值(标签与值**分列**时文本流取不到 ——
    陈行富文本流: 左列"姓 名:/性 别:/单 位:…", 右列"陈行富/男/138…", 顺序读出
    时姓名值距标签 5 行, 文本启发式只能退回"尊敬的 XXX")。标签同排(y)右侧首格
    即值; 扫描件(无文本层)返回 None 交给文本启发式。"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if not len(doc):
            return None
        page = doc[0]
        spans: list = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for line in b["lines"]:
                for sp in line["spans"]:
                    t = (sp.get("text") or "").strip()
                    if t:
                        spans.append((sp["bbox"], t))
        doc.close()
        for (x0, y0, x1, y1), t in spans:
            if not re.fullmatch(r"姓\s*名\s*[:：]?", t):
                continue
            yc = (y0 + y1) / 2
            cands = sorted(
                ((bx0, bt) for (bx0, by0, bx1, by1), bt in spans
                 if bx0 > x0 and abs((by0 + by1) / 2 - yc) <= 8
                 and ":" not in bt and "：" not in bt and len(bt) <= 10),
                key=lambda z: z[0])
            if cands:
                return cands[0][1]
        return None
    except Exception:
        return None


_SUMMARY_ITEM_RE = re.compile(r"【([^】]{2,80})】")
_SUMMARY_VAL_TAIL_RE = re.compile(
    r"(?:偏高|偏低|升高|降低|增高|减低|异常|弱阳性|阳性|\+\d*|\d+\+|±|\+|-)\s*$")
_SUMMARY_PREFIX_RE = re.compile(
    r"^\s*(?:尿常规|血常规|生化全套?|肝功能|肾功能|血脂|心电图|彩超)\s*[:：]\s*")


def _norm_summary_name(s: str) -> str:
    return re.sub(r"[\s（）()\u3000]", "", s or "")


def mark_indicators_by_summary(indicators: list, section: str) -> None:
    """2026-10-19: 档案声明 summary_marks(日照市中医医院) —— 总结段【异常项】给
    **既有**指标行标黄(signal_flag=3, 权威), 不新建行。用于结果非数字/参考为文字
    且表内无标志的项(尿红细胞(隐血)+1 / 尿蛋白弱阳性); 每个条目只标最长匹配行
    (防"蛋白"误标到"总蛋白")。"""
    if not section or not indicators:
        return
    heads = []
    for m in _SUMMARY_ITEM_RE.finditer(section):
        h = _SUMMARY_PREFIX_RE.sub("", m.group(1).strip())
        for _ in range(3):
            h2 = _SUMMARY_VAL_TAIL_RE.sub("", h).strip()
            if h2 == h:
                break
            h = h2
        h = _norm_summary_name(h)
        if len(h) >= 2:
            heads.append(h)
    for h in heads:
        cand = []
        for idx, ind in enumerate(indicators):
            n = _norm_summary_name(ind.get("item_name") or "")
            if len(n) >= 2 and n in h:
                cand.append((len(n), idx))
        if not cand:
            continue
        _max = max(c[0] for c in cand)
        for ln, idx in cand:
            if ln == _max and (indicators[idx].get("signal_flag") or 0) < 3:
                indicators[idx]["signal_flag"] = 3


def extract_personal_info(text: str) -> dict:
    import re as _re
    out: dict = {}
    # 2026-09-14: 双语标签("姓名(Name)：___ 陈磊")—— 允许键后括号英文与下划线占位
    # 2026-09-22: 扫描**所有**"姓名"标签取首个合理候选 —— 表头乱序时首个标签后可能是
    # 机构名/标签(陈诚"姓名→南通市第一人民医院健康管理中心"、华裕健"姓名→出生日期"),
    # 后续标签(第2页"姓名: 华裕健")才带真名。
    # 2026-10-19: "姓名：尊敬的 XXX"(陈行富 OCR)优先取尊称后的真名 —— 否则通用
    # 循环会把"尊敬的"拒掉后抓到表格里的碎词("中性粒细胞比")
    _m_hon = _re.search(
        r"姓\s*名(?:\([^)]*\))?[:：\s_]*尊\s*敬\s*的\s*([\u4e00-\u9fa5·]{2,6})", text)
    if _m_hon and _name_ok(_m_hon.group(1)):
        out["name"] = _m_hon.group(1).strip()
    for m in [] if out.get("name") else _re.finditer(
            r"姓\s*名(?:\([^)]*\))?[:：\s_]*([\u4e00-\u9fa5·]{2,6})", text):
        if _name_ok(m.group(1)):
            out["name"] = m.group(1).strip()
            break
    # 兜底0: "尊敬的XXX先生/女士"(陈诚"尊敬的\n陈诚  男士，您好！")
    if not out.get("name"):
        # 2026-10-19: 允许无"先生/女士"后缀(陈行富 OCR "尊敬的  陈行富\n您好！")
        m = _re.search(
            r"尊\s*敬\s*的\s*([\u4e00-\u9fa5·]{2,6})(?:\s*(?:先生|女士|男士|小姐|同志))?", text)
        if m and _name_ok(m.group(1)):
            out["name"] = m.group(1).strip()
    # 兜底1: 单位后紧跟性别字 → 姓名(柳州"单位: 石坤 男 41岁")
    if not out.get("name"):
        m = _re.search(r"单\s*位[:：]\s*([\u4e00-\u9fa5·]{2,6})(?=\s*[（(]?(男|女))", text)
        if m and _name_ok(m.group(1)):
            out["name"] = m.group(1).strip()
    # 兜底2: 姓名: 后 80 字窗口内第一个非表头词(梧州"姓名: 性别: 男 团体: ... 谢国宾")
    if not out.get("name"):
        m = _re.search(r"姓\s*名(?:\([^)]*\))?[:：]", text)
        if m:
            window = text[m.end():m.end() + 80]
            for cand in _re.findall(r"[\u4e00-\u9fa5·]{2,6}", window):
                if _name_ok(cand):
                    out["name"] = cand
                    break
    m = _re.search(r"性\s*别(?:\([^)]*\))?[:：\s_]*(男|女)", text)
    if m:
        out["gender"] = m.group(1)
    if not out.get("gender"):
        # 兜底: 头部区 男/女 后跟岁数(柳州"男 41岁" / 梧州表"谢国宾 | 男 | 36")
        m = _re.search(r"(男|女)[\s\S]{0,6}?\d{1,3}\s*岁", text[:1500])
        if m:
            out["gender"] = m.group(1)
    m = _re.search(r"年\s*龄(?:\([^)]*\))?[:：\s_]*(\d{1,3})\s*岁?", text)
    if m:
        # 2026-09-22: 表头乱序时"年龄"后紧跟的是身份证/手机(华裕健 330/曹剑鸿 139)
        # → 仅接受 1-120 的合理值, 否则丢弃交 LLM 兜底
        _a = int(m.group(1))
        if 1 <= _a <= 120:
            out["age"] = m.group(1)
    m = _re.search(r"(体检日期|检查日期|体检时间|登记日期|首检日期)(?:\([^)]*\))?\s*[:：]?\s*[_\s]*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        out["report_date"] = f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    m = _re.search(r"单\s*位[:：]\s*([\u4e00-\u9fa5A-Za-z（）()]{2,30})", text)
    if m and not _re.search(r"(部门名称|单位名称|联系单位|检查单位)", m.group(1)) \
            and not _re.fullmatch(
                r"(体检日期|单位部门|部门|姓名|性别|年龄|电话|手机|编号|日期|时间|科室|单位)",
                m.group(1).strip()):
        out["unit_name"] = m.group(1).strip()
    return out


# === 异常信号提取(2026-08-25): 黄区红区完全准确, 绿区可例外 ===
# 三个确定性信号通道, 命中即黄区候选(不需要解析全部表格式):
#   通道1 红字: span 颜色为红色系(#ff0000 近邻) → 北京/防城港一院等
#   通道2 箭头: 文本含 ↑/↓ → 广西多数 + 北京
#   通道3 异常词: 行文本含 增高/偏高/降低/偏低/异常/阳性/弱阳性 → 崇左类纯文本综述
# 输出 (item_name, result, unit); 绿区无信号行直接忽略。
_RED_SPAN_MAX = 120  # red 通道判定: r>200, g<max, b<max
_ABNORMAL_WORD_RE = re.compile(
    r"(增高|升高|偏高|降低|偏低|异常|阳性|弱阳性|可疑|超重|肥胖|偏轻|偏重|"
    r"增快|过重|↑|↓)"
)
# 异常词通道噪声: 标题/提示类行不做指标
_ABNORMAL_WORD_SKIP = re.compile(
    r"(体检结论|结论分析|测评总分|普检室|检查日期|体检报告|健康管理|温馨提示|"
    r"建议|请结合临床|单位[:：]|科室|医院体检中心|正常的|之间|参考范围)"
)


def _is_red_color(color: int) -> bool:
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return r > 200 and g < _RED_SPAN_MAX and b < _RED_SPAN_MAX


# 2026-09-05: 仅信号配对侧过滤的垃圾名(正常表格行仍保留 —— 体重/腰围等记录项)
_SIGNAL_ONLY_SKIP_RE = re.compile(r"^(体重|腰围|以下|本次体检结论|体检结论|结果|检查所见)$")


def _pair_from_lines(lines: list[str], idx: int, signal: str) -> Optional[dict]:
    """从信号行 idx 上下文中配对(名称, 结果): 信号行本身或其前后行。

    场景:
      - 行内: "尿酸 431 ↑" / "腰围 85 cm"(全红行)
      - 值行: "431\n↑"(信号行是值/箭头行, 向上找名称行, 向下找单位)
      - 通道3综述行: "【血脂四项】:(1)低密度脂蛋白增高4.73" → 名称=低密度脂蛋白 值=4.73
    """
    ln = lines[idx]
    # 2026-09-05: ▲/▲★ 标记行(滨州模板: "▲★尿酸 478 偏高")= 报告方异常标记
    if signal == "mark":
        # guard: ▲ 行本身或下方 4 行内须有异常佐证(偏高/升高/↓/H…), 否则该 ▲ 只是
        # 普通行标记(滨州全表行均带 ▲★, 不能全判异常)
        ctx = " ".join(lines[idx:min(idx + 4, len(lines))])
        if not re.search(r"(偏高|升高|增高|降低|偏低|异常|阳性|↑|↓|\bH\b)", ctx):
            return None
        ln = re.sub(r"^[▲△★*＊\s]+", "", ln)
        rest2 = re.sub(r"^(?:[（(]\d+[)）]|\d+\s*[、.．])\s*", "", ln)
        m = re.match(
            r"^([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/]{0,20}?)[\s:：]*([\d.]+)",
            rest2,
        )
        if m and not _skip_name(m.group(1)):
            return {"item_name": _clean_name_tail(m.group(1)), "result": m.group(2),
                    "unit": "", "signal": signal}
        # 名称行形态("▲★白细胞数" 值在下一行) → 向下找值行
        for j in range(idx + 1, min(idx + 3, len(lines))):
            v = lines[j]
            m2 = _VALUE_RE.match(v)
            if m2:
                name = _clean_name_tail(re.sub(r"^[▲△★*＊\s]+", "", lines[idx]))
                return {"item_name": name, "result": m2.group(1),
                        "unit": m2.group(2).strip(), "signal": signal}
        return None
    # 通道3: 综述行内解析("【检查项】:(n)名称 异常词 数值")
    if signal == "word":
        rest = re.sub(r"^【[^】]*】[:：]?", "", ln)
        rest = re.sub(r"^[（(]\d+[)）]|^\d+[、.．]", "", rest).strip()
        m = re.match(
            r"^([\u4e00-\u9fa5A-Za-z()（）%·\-/ ]{1,20}?)"
            r"(增高|升高|偏高|降低|偏低|阳性|弱阳性|超重|肥胖|偏轻|偏重|增快|过重)[:：]?\s*([\d.]+)",
            rest,
        )
        if m and not _skip_name(m.group(1)):
            return {"item_name": _clean_name_tail(m.group(1)), "result": m.group(3), "unit": "", "signal": signal}
    # 信号行行内解析: 支持编号前缀 + 【】前缀 + 名称紧跟数字(崇左综述"(1)超重25.7 BMI")
    m = None
    for variant in (lines[idx], re.sub(r"【[^】]*】[:：]?", "", lines[idx], count=1)):
        # 2026-09-14: 先试"名称：值"(冒号分隔)形式 —— 防名称尾部缩写数字("载脂蛋白-A1")
        # 被当结果("1")(华山常逢龙小结)。冒号形式下名称可含尾部数字/字母, 值须在冒号后。
        m = re.match(
            # 2026-09-22: 值支持半定量("2+"/"+1") —— 结论文本"(1)维生素C(尿):2+↑(阴性)"
            # 曾只捕获"2", 与表格行"2+"并存成两行(第四批陈诚)
            r"^(?:[（(]\d+[)）、.。]?)?([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,24}?)[：:]\s*([\d.]+\+?|[+]?[\d.]+)",
            variant,
        )
        if not m:
            m = re.match(
                r"^(?:[（(]\d+[)）、.。]?)?([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,20}?)[\s:：]*([\d.]+)",
                variant,
            )
        if m:
            break
    if m and not _skip_name(m.group(1)):
        # 2026-10-06: 值后紧跟量词 = 标题内数量("(2)多肿瘤标志物12项（男）：血清铁蛋白：
        # 437.71↑" 小结标签行)—— "12" 不是结果(陈霓 多肿瘤标志物|12 假黄)
        if signal == "arrow" and variant[m.end(2):m.end(2) + 1] in (
                "项", "个", "号", "次", "种", "例", "类", "条", "支"):
            m = None
        else:
            name = _clean_name_tail(m.group(1))
            if name and not _skip_name(name):
                return {"item_name": name, "result": m.group(2), "unit": "", "signal": signal}
    # 2026-09-07: 信号行本身是"名称行"(红名/行首名, 无数字) → 名=自身, 直接向下找值,
    # 不得向上找名(日照: 红名"天门冬氨酸氨基转移酶"向上 2 行撞到上一指标"谷丙/谷草"
    # 造成错配 0.92 假黄)。仅对 非名称形态 的信号行才向上找名。
    if signal == "red" and _NAME_RE.match(lines[idx]) and not _skip_name(lines[idx]) \
            and not _is_value_line(lines[idx]):
        name0 = _clean_name_tail(lines[idx].lstrip("★*＊▲△"))
        if name0 and not _skip_name(name0):
            for j in range(idx + 1, min(idx + 4, len(lines))):
                v = lines[j]
                if _ARROW_RE.match(v) or _FLAG_TEXT_RE.match(v) or _UNIT_RE.match(v) \
                        or _RANGE_RE.match(v):
                    continue
                m2 = _VALUE_RE.match(v)
                if m2:
                    return {"item_name": name0, "result": m2.group(1),
                            "unit": m2.group(2).strip(), "signal": signal}
                nv = _norm_value_cell(v)
                if nv:
                    return {"item_name": name0, "result": nv, "unit": "", "signal": signal}
                if _QUAL_RE.match(v) or _QUAL_ANNOT_RE.match(v):
                    return {"item_name": name0, "result": v, "unit": "", "signal": signal}
                if _NAME_RE.match(v):
                    break
            return None
    # 向上找名称行(跳过箭头/单位/范围/序号行; 中间夹英文括号行如
    # "(A-TPO)" 并入名称 —— 马鞍山表头/红字名称行与值间隔简称行)
    # 2026-09-14: 仅对**独立箭头行**("↑"/"↓")放宽窗口 3→5 —— 中医院曹嘉冰/防城港中
    # 表格 dump=名称/值/参考/单位/箭头, 名称在箭头前 4 行(参考/单位占 2 行)。
    # 非独立箭头行(小结/综述行内嵌 ↑, 如"总胆固醇偏高【6.29 ↑】")维持窗口 3:
    # 放宽会让小结行向上跨到上一指标(防城港中 HDL/谷草/RDW 假黄回归)。
    if _PAREN_ONLY_RE.match(ln) and re.search(r"[A-Za-z]", ln):
        _self_paren = ln  # 信号行自身是英文简称括号行(红字)
    else:
        _self_paren = ""
    _name_win = 6 if _ARROW_RE.match(ln.strip()) else 4
    for k in range(1, _name_win):
        if idx - k < 0:
            break
        cand = lines[idx - k]
        if _PAREN_ONLY_RE.match(cand):
            continue  # 纯括号行("(定量)/(A-TPO)")不是名称候选(并入由下方 bj 拼接处理)
        if _is_value_line(cand) or (_UNIT_RE.match(cand) and not _NAME_RE.match(cand)) \
                or _RANGE_RE.match(cand) or _ARROW_RE.match(cand):
            continue
        if _NAME_RE.match(cand) and not _skip_name(cand):
            name = cand.lstrip("★*＊▲△")
            # 名称与信号行之间夹的英文括号行并入名称(信号行自身为括号行时也并入)
            if _self_paren:
                name += _self_paren.strip()
            for bj in range(idx - k + 1, idx):
                if _PAREN_ONLY_RE.match(lines[bj]) and re.search(r"[A-Za-z]", lines[bj]):
                    name += lines[bj].strip()
            # 向下找值行
            for j in range(idx - k + 1, min(idx + 3, len(lines))):
                v = lines[j]
                # 2026-09-15: 值+箭头同行("17.6 ↓") → 值取数字, 标志已由 signal 承担
                _av, _aflag = _value_with_arrow(v)
                if _aflag:
                    return {"item_name": name, "result": _av, "unit": "", "signal": signal}
                m2 = _VALUE_RE.match(v)
                if m2:
                    return {"item_name": name, "result": m2.group(1), "unit": m2.group(2).strip(), "signal": signal}
                nv = _norm_value_cell(v)
                if nv:
                    return {"item_name": name, "result": nv, "unit": "", "signal": signal}
                if _QUAL_RE.match(v) or _QUAL_ANNOT_RE.match(v):
                    return {"item_name": name, "result": v, "unit": "", "signal": signal}
                # 2026-09-15: 结果单元格被 fitz 拆成两行(">1000[阳性反应" + "（+）]")——
                # 单行归一失败, 拼下一行(非纯箭头)再试(日照 乙肝表面抗体标志丢失回归)
                if j + 1 < len(lines) and not _ARROW_RE.match(lines[j + 1].strip()):
                    nv2 = _norm_value_cell(v + lines[j + 1])
                    if nv2:
                        return {"item_name": name, "result": nv2, "unit": "", "signal": signal}
            return {"item_name": name, "result": "", "unit": "", "signal": signal}
    return None


# 2026-09-14: 多对小结行("载脂蛋白-A1：0.80g/L ↓；高密度…：0.93mmol/L ↓；低密度…：3.45 ↑；")
# 逐对提取 —— 原 _pair_from_lines 只取第一对, 漏同行的后续异常(华山常逢龙)。
_SUMMARY_SEG_RE = re.compile(
    r"^[\s（(]*([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9()（）%·\-/ ]{0,29}?)[：:]\s*"
    r"([<>≤≥]?\s*[\d.]+)\s*([^\d↑↓；;]*?)\s*([↑↓])$")


def _pair_summary_line(line: str, signal: str) -> Optional[list[dict]]:
    """多对小结行逐对提取; 无 ";；" 分隔时返回 None(交 _pair_from_lines 单对处理)。
    各段须自带箭头才算异常。"""
    if "；" not in line and ";" not in line:
        return None
    rows: list[dict] = []
    for seg in re.split(r"[；;]", line):
        seg = seg.strip().rstrip("。")
        if not seg:
            continue
        m = _SUMMARY_SEG_RE.match(seg)
        if not m:
            continue
        nm = _clean_name_tail(m.group(1))
        if not nm or _skip_name(nm):
            continue
        rows.append({"item_name": nm, "result": m.group(2).strip(),
                     "unit": m.group(3).strip(), "signal": signal})
    return rows or None


def _pair_arrow_by_row(lines: list[str], meta: list[tuple], idx: int,
                       result_hint: str | None = None,
                       signal: str = "arrow") -> list[dict]:
    """纯箭头行("↑"/"↓")按**同一视觉行(y)**配对名称/结果。

    兼容"提示列箭头在文本流中排在下一行名称之前"的版面(高帅 血常规/血脂: 箭头与
    名称同排, 但文本序是 …上一行ref/arrow/本行名称/值) —— 按文本顺序向上找名会把
    箭头错配给上一行(血小板压积/甘油三酯 假黄)。
    result_hint: "值+箭头"同格行("26.16 ↑")传入本行值, 结果不再向右扫描(董涛 体格表
    文本流乱序: 文本序向上找名会把 26.16 错配给上一行"舒张压")。
    """
    page0, y0, ax = meta[idx]
    same = [(i, meta[i][2], lines[i]) for i in range(len(lines))
            if i != idx and meta[i][0] == page0 and abs(meta[i][1] - y0) <= 3.0
            and lines[i]]
    if not same:
        return []
    name = None
    name_x = None
    for _i, _x, t in sorted(same, key=lambda z: z[1]):
        if _NAME_RE.match(t) and not _skip_name(t) and not _is_value_line(t):
            name = t.lstrip("★*＊▲△").strip()
            name_x = _x
            break
    if not name:
        return []
    if result_hint:
        return [{"item_name": name, "result": result_hint, "unit": "",
                 "signal": signal, "_coord": True}]
    result, unit = None, ""
    for _i, x, t in sorted(same, key=lambda z: z[1]):
        # 2026-09-15: 名称左侧的单元格是"序号"列(马鞍山 9/21/23 被当结果) —— 跳过;
        # 结果只可能在名称右侧(至箭头 x 之间)。
        if name_x is not None and x <= name_x:
            continue
        if x > ax:
            break
        m2 = _VALUE_RE.match(t)
        if m2:
            result, unit = m2.group(1), m2.group(2).strip()
            break
        nv = _norm_value_cell(t)
        if nv:
            result = nv
            break
        if _QUAL_RE.match(t) or _QUAL_ANNOT_RE.match(t):
            result = t
            break
    if not result:
        return []
    # _coord: 该信号由同排坐标配对得出(可靠) —— 调用方不再套用 _SIGNAL_ONLY_SKIP_RE
    # 黑名单(腰围等被列式路径依赖的项, 若被黑名单拒收会丢标志)
    return [{"item_name": name, "result": result, "unit": unit,
             "signal": signal, "_coord": True}]


def extract_abnormal_signals(pdf_path: str, skip_text: str = "",
                             red_marks: bool = False,
                             red_ref: bool = False) -> list[dict]:
    """异常信号提取: 红字行 + 箭头行 + 异常词行 → (名称, 结果) 候选。

    信号行在**全文行**中定位, 以便向上配对黑色名称行/向下找值行。
    噪声过滤: 弃检/未检类提示(红字)剔除。
    skip_text: 结论段原文 —— 结论段内的行(↑/偏高 等)由解读回填负责, 不得再作为
    指标信号(陈东海"淋巴细胞绝对值：3.27 10E9/L(1.1-3.2)↑"被配成假指标行;
    陈刚"本次检验科提示：…272.75(mIU/L)↑"参考值当结果重复提取)。
    red_marks: 档案声明(恩施州中心医院)时启用红字通道 —— 该院仅以红色字体标注异常
    (血压 118/84mmHg、尿潜血 "+- Ca10"、EB 抗体 弱阳性(±) 无箭头), 红字值单元格与
    名称同排(y), 走同排配对(见 _pair_arrow_by_row)。2026-09-08 全局删除红字判黄后
    按院恢复。
    """
    import fitz
    _skip_norm = set()
    if skip_text:
        _skip_norm = {re.sub(r"\s+", "", x) for x in skip_text.splitlines()
                      if len(x.strip()) >= 4}
    doc = fitz.open(pdf_path)
    # 2026-10-19(李承龙反馈): 历次对比表**跨页** —— 标题页 + 后续"纯日期单元格"
    # 续表页的信号整体跳过(否则表内 ↑ 把当前行标黄/生成"碳呼气试验"等假指标)
    _hist_pages: set = set()
    try:
        _seen_hist = False
        for _pi, _pg in enumerate(doc):
            _tx = _pg.get_text() or ""
            if not _seen_hist and _HIST_CMP_TITLE_RE.search(_tx):
                _seen_hist = True
                _hist_pages.add(_pi)
                continue
            if _seen_hist:
                _dl = sum(1 for _l in _tx.splitlines()
                          if re.fullmatch(
                              r"\s*\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}\s*", _l))
                if _dl >= 2:
                    _hist_pages.add(_pi)
                elif _pi > max(_hist_pages) + 1:
                    _seen_hist = False
    except Exception:
        _hist_pages = set()
    all_lines: list[str] = []
    line_meta: list[tuple] = []  # (page_no, y0, x0) —— 同页同排(y)配对箭头用(高帅)
    signal_idx: list[tuple[int, str]] = []
    for page_no, page in enumerate(doc):
        if page_no in _hist_pages:
            continue  # 2026-10-19: 历次对比表(含跨页续表)不进信号通道
        d = page.get_text("dict")
        block_lines = []
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                t = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if t:
                    block_lines.append((t, line.get("spans", []), line.get("bbox")))
        # 2026-09-05: "历次体检结果比对/上次·本次体检结论"表(百色) = 结论对比文本,
        # 非检查项表格 → 标题行起(含)的该页内容不进信号通道。
        # 2026-10-06: 非指标表页(陈霓"结论词/级别"表等)整页不进信号通道
        if _is_non_indicator_table_page([t for t, _s, _b in block_lines]):
            continue
        # 2026-10-13: 历年对比区也覆盖"历次检查异常结果比对"(石硕: ↓ 标志在对比表
        # 内, 信号通道把上次值 0.22 当本次异常信号 —— 与文本路径剥离同口径)
        skip_from = next((i for i, (t, _s, _b) in enumerate(block_lines)
                          if _HIST_CMP_TITLE_RE.search(t)), len(block_lines))
        for line_idx, (text, spans, bbox) in enumerate(block_lines):
            if line_idx >= skip_from:
                continue
            if not text:
                continue
            if re.search(r"(弃检|未检|放弃|拒检|无法完成)", text):
                continue
            _in_skip = False
            if _skip_norm:
                _tn = re.sub(r"\s+", "", text)
                if len(_tn) >= 4 and (_tn in _skip_norm
                                      or any(_tn in _s for _s in _skip_norm
                                             if len(_tn) >= 6)):
                    _in_skip = True
            all_lines.append(text)
            line_meta.append((page_no,
                              float(bbox[1]) if bbox else 0.0,
                              float(bbox[0]) if bbox else 0.0))
            idx = len(all_lines) - 1
            # 2026-10-07: 结论段行**保留为配对上下文**且正常注册信号, 仅以 _csrc 标记;
            # 消费侧对 _csrc 信号"只标黄既有行、不新建行"(见调用方)。直接整行剔除会让
            # 其他区域箭头向上越过被删行错配到更远条目(护栏 21 例假黄)
            # 2026-09-07(口径确认): 红字样式不单独作为异常标志 —— 异常行均有
            # ↑↓/提示列字母(H/L/A)/提示文字/*等标志, 由下方分支/表格标志列承担。
            if "▲" in text or "△" in text:
                signal_idx.append((idx, "mark", _in_skip))
            elif "↑" in text or "↓" in text:
                signal_idx.append((idx, "arrow", _in_skip))
            elif _FLAG_TEXT_RE.match(text):
                # 2026-10-19: 单位折行尾行("mmol/" 后的 "L", 韩先锋)不是标志
                _prev_txt = block_lines[line_idx - 1][0].strip() if line_idx > 0 else ""
                if text.strip() in ("L", "l", "H", "h") and _prev_txt.endswith("/"):
                    continue  # 单位续行已由上面的 all_lines.append 收录, 不重复登记
                # 2026-09-05: 表格提示列文字独立行("偏高" 值后, 滨州 FPSA/TPSA 行尾)
                signal_idx.append((idx, "flagtext", _in_skip))
            elif (red_marks or red_ref) and any(_is_red_color(sp.get("color", 0))
                                                for sp in spans):
                # 2026-10-13: 档案声明红字通道(恩施) —— 红字行=异常值单元格
                # ("118/84mmHg"/"+-    Ca10"/"弱阳性(±)"; 有 ↑↓ 的行已由上面分支接管)
                # 2026-10-19: red_ref(望京) —— 红字仅作"样式", 结果超数值参考才判黄;
                # red_marks(恩施)维持既有"红字=报告方异常标注"强制判黄
                signal_idx.append((idx, "redref" if red_ref else "red", _in_skip))
    doc.close()

    # 2026-09-28: rev/dual 块表区内的信号行不参与 —— 块表(齐鲁异常标识列/弘爱本次+
    # 上次结果)由列式整块接管(权威); 否则信号通道把"上次结果"列值也当异常信号
    # (弘爱双值表 总胆固醇 5.73/5.80 双双判黄、AG 9.2 错配假黄)
    _block_wins = _block_windows(all_lines)
    if _block_wins:
        signal_idx = [(i, s, c) for i, s, c in signal_idx
                      if not any(a <= i < b for a, b, _m in _block_wins)]

    seen: dict = {}
    out = []
    for idx, sig, _csrc in signal_idx:
        # 2026-09-15: 纯箭头行优先按同排(y)配对(高帅); 失败再走文本序
        rows = []
        if sig == "arrow" and _ARROW_RE.match(all_lines[idx].strip()):
            rows = _pair_arrow_by_row(all_lines, line_meta, idx)
        if not rows and sig == "arrow":
            # 2026-09-28: "值+箭头"同格行("26.16 ↑", 董涛 体格表文本流乱序) ——
            # 值取本行, 名称按同排(y)取; 文本序向上找名会错配给上一行(舒张压/总胆固醇)
            _av, _aflag = _value_with_arrow(all_lines[idx].strip())
            if _aflag and _av:
                rows = _pair_arrow_by_row(all_lines, line_meta, idx, result_hint=_av)
        if not rows and sig in ("red", "redref"):
            # 同排(y)配对: 红字值单元格与名称单元格同一视觉行(恩施/望京)
            # 2026-10-13: hint 尾缀 "CaN"(半定量计数, "+-  Ca10" = ±10/μL)剥除 ——
            # 与表格行值 "+-" 归一后标志才能合并到既有行(尿潜血/尿白细胞曾因
            # "+- Ca10"≠"+-" 键不中, 标志落在重复行被去重丢弃)
            _hint = re.sub(r"\s+Ca\d+\s*$", "", 
                           re.sub(r"\s+", " ", all_lines[idx].strip())).strip()
            rows = _pair_arrow_by_row(
                all_lines, line_meta, idx, result_hint=_hint,
                signal="redref" if sig == "redref" else "arrow")
        if not rows:
            multi = _pair_summary_line(all_lines[idx], sig)
            if multi is not None:
                rows = multi
            else:
                single = _pair_from_lines(all_lines, idx, sig)
                rows = [single] if single else []
        for row in rows:
            # result 为空 = 弃检/未检/配对失败, 不是异常信号, 剔除
            if not (row and row["item_name"] and row["result"]):
                continue
            # 2026-10-07: 结论段来源信号 —— 只允许对既有指标行"标黄"(同 key 合并),
            # 不新建行(否则结论里的碎片/参考值会变成假指标行; 但陈美杉"体重指数"
            # 类结论行箭头仍需给表格行上色)
            if _csrc:
                row["_conclusion_src"] = True
            # 2026-09-05: 配对值 = 阴性词("齿 正常"红字/整行标红的正常项)不是异常信号
            if re.fullmatch(r"(正常|未见异常|未见明显异常|无异常|未触及|未肿大|无肿大|未见|无|无明显异常)", row["result"]):
                continue
            if not row.get("_coord") and _SIGNAL_ONLY_SKIP_RE.match(row["item_name"]):
                continue
            # 2026-09-07: 检验科小结/建议句式残名("本次体检发现血甘油三酯:2.06↑")
            if row["item_name"].startswith("本次体检发现"):
                continue
            # 2026-09-07: 综述折行残名(厦门华西"甘油三\n酯:2.06mmol/L↑" → "酯")——
            # 信号通道按 pdf 行扫, 不经过 para 折行合并, 单/短汉字名一律拒收
            # (2026-09-15: 同排坐标配对的结果可靠, 豁免此过滤 —— "钾(K)" 只有 1 汉字
            # 但确是合法指标名, 钦州二)
            if not row.get("_coord") \
                    and len(re.findall(r"[\u4e00-\u9fa5]", row["item_name"])) <= 1 \
                    and len(row["item_name"]) <= 4:
                continue
            # 2026-09-07: 名称残留定性括号("(阴性(-)/(+)或…)" 莆田检验科小结) → 拒
            if re.search(r"[（(]\s*[+-]\s*[)）]", row["item_name"]) \
                    or re.match(r"^(阴性|阳性|弱阳性)[（(]-?[)）]?", row["item_name"]):
                continue
            key = (row["item_name"], row["result"])
            if key not in seen:
                seen[key] = True
                out.append(row)
    return out


# === Phase 2 接线(2026-09-07): 布局优先, 布局不可用(空/异常/图片型)回退旧列式 ===
def _pdf_text_layer_ok(pdf_path: str) -> bool:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = sum(len(p.get_text().strip()) for p in doc)
        doc.close()
        return total > 200
    except Exception:
        return False


def col_rows_with_fallback(pdf_path: str, text: str) -> list[dict]:
    # 2026-09-15: 坐标主路径已修(两联排表按角色最小重复周期分组, 高帅/白玮衡体格表
    # "身高|179.5cm 收缩压|121mmHg" 不再并成一名) → 重新优先启用; 空/异常回退文本列式。
    # 2026-10-14: 历年对比页(标题变体)页码 → 布局整页跳过(石硕)
    _skip_pages = None
    try:
        _cur: Optional[int] = None
        _pages: set = set()
        for _ln in (text or "").splitlines():
            _m = re.match(r"--- Page (\d+) ---", _ln.strip())
            if _m:
                _cur = int(_m.group(1))
            elif _cur and (_HIST_CMP_TITLE_RE.search(_ln)
                           or _RISK_ASSESS_LINE_RE.search(_ln)):
                # 2026-10-16: 心脑血管病风险评估页(prose/等级/建议)整页跳过
                _pages.add(_cur)
        _skip_pages = _pages or None
    except Exception:
        _skip_pages = None
    try:
        rows = col_rows_via_layout(pdf_path, skip_pages=_skip_pages)
        if rows:
            return rows
    except TypeError:
        try:
            rows = col_rows_via_layout(pdf_path)
            if rows:
                return rows
        except Exception:
            pass
    except Exception:
        pass
    # 扫描件无文本层 → 信号通道为空, 裸箭头仍按列式文本序作标志; 文本件交信号通道同排配对
    keep = not _pdf_text_layer_ok(pdf_path)
    return extract_column_table_rows(text, keep_bare_arrow=keep)


# === Phase 2(2026-09-07): 列语义组装(col_rows_via_layout) ===
# region(坐标检测) + 表头行列角色(x 区间) → 数据行按列归位取 名称/结果/参考/单位/标志。
# 统一覆盖: 常规列式表(广西)、华西三列表、弘爱双值表(表头"本次结果"直接给出结果列)、
# 齐鲁反序表(名称/值/参考/单位/异常标识 = 五列表头, 无需 rev 特判)。
# 值/参考的单元格判定复用本模块既有 helper(_norm_value_cell/_range_with_unit/_parse_ref)。
# ⚠ 未接线: 由 service 组装在验证通过后切换; 行式通道不变。
def col_rows_via_layout(pdf_path: str, skip_pages: Optional[set] = None) -> list[dict]:
    """列语义组装(Phase 2 v6, 表头分段 + 段内独立列谱):
    - region 内每个表头行 → 新段; 段内数据行独立做 x0 列聚簇(容差 30);
    - 列数 > 角色数时剔除"窄标志列"(该列 ≥60% cell 为 ↑↓HL*/'-' 等短标记, 如华西 ↑ 列);
    - 等长 → 角色序 zip; 数据 cell 就近归列(容差 30), 归不进且近表头 x(≤60)也取角色;
    - region 首段包含表头前的页首块(华西上半血脂块)。
    ⚠ 未接线; 验证通过后由 service 组装切换。
    """
    from app.modules.report import layout as L

    regions = L.detect_table_regions(pdf_path)
    out: list[dict] = []
    _last_rx: Optional[list] = None
    for reg in regions:
        # 2026-10-14: 历年对比页整页跳过(石硕"历次检查异常结果比对": 上次值/上次标志
        # 被坐标列路径当本次提取 → 绿项错判黄; 页码由文本页标记块定位)
        if skip_pages and getattr(reg.rows[0], "page", None) in skip_pages:
            continue
        # 2026-10-19: 历次对比表(本次/去年/前年 多日期列, 李承龙"历次部分结果对比")
        # 整段跳过 —— 标题只在首页, 跨页续表按"≥2 个日期列"识别
        try:
            _dt = re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}")
            # 2026-10-19: 仅"纯日期单元格"(表头日期列, 李承龙 "2025-07-10")计入 ——
            # 页脚"检查日期：2026-06-09"标签不触发(安周常规化验表曾整段被误跳)
            _date_cols = sum(1 for c in reg.rows[:60]
                             if _dt.fullmatch((c.text or "").strip()))
            if _date_cols >= 2:
                continue
        except Exception:
            pass
        # 2026-10-06: 非指标表 region 整段跳过(陈霓"结论词/级别"表、微量元素解读表、
        # 历次档案表、"强烈建议下次体检项目"表 —— 该表头行无 result 角色, 会被当数据行
        # 采出 "项目|临床意义" 等垃圾)
        if _is_non_indicator_table_page([r.text for r in reg.rows]):
            continue
        lines = L.logical_lines(reg.rows)
        # 切段: [(roles_xs, start_idx, end_idx)]; region 首段起点回拨到 0(页首块)
        segs: list = []
        cur_start = 0
        cur_roles = None
        for idx, cells in enumerate(lines):
            roles = L.header_roles(cells)
            if roles:
                if cur_roles is not None:
                    segs.append((cur_roles, cur_start, idx, False))
                cur_roles = [(r, c.x0) for r, c in zip(roles, cells)]
                cur_start = idx
        if cur_roles is not None:
            segs.append((cur_roles, cur_start, len(lines), False))
            _last_rx = cur_roles
        elif _last_rx is not None and lines:
            # 跨页/跨 region 续表(德宏毕建国化验大表): 继承最近表头谱(继承段)
            segs.append((_last_rx, 0, len(lines), True))
        # 处理: 段 s0 的表头行之前若有数据(页首块), 归该段
        if segs:
            segs[0] = (segs[0][0], 0, segs[0][2], segs[0][3])
        for roles_xs, s0, s1, inherited in segs:
            seg_rows: list[dict] = []
            seg_data = []
            for idx in range(s0, s1):
                cells = lines[idx]
                if L.header_roles(cells):
                    continue
                seg_data.append(cells)
            cells_flat = [c for cl in seg_data for c in cl if c.text.strip()]
            if not cells_flat:
                continue
            xs = sorted(c.x0 for c in cells_flat)
            col_xs: List[float] = []
            for x in xs:
                if col_xs and x - col_xs[-1] <= 30.0:
                    col_xs[-1] = (col_xs[-1] + x) / 2
                else:
                    col_xs.append(x)
            pass
            # 窄标志列剔除(仅当 列数 > 角色数)
            while len(col_xs) > len(roles_xs):
                best_i = None
                best_ratio = 0.0
                for i, cx in enumerate(col_xs):
                    cs = [c.text.strip() for c in cells_flat
                          if abs(c.x0 - cx) <= 30.0]
                    if not cs:
                        continue
                    short = sum(1 for t in cs if len(t) <= 2
                                or t in ("↑", "↓", "H", "L", "*", "-", "异常"))
                    if short / len(cs) > best_ratio:
                        best_ratio = short / len(cs)
                        best_i = i
                if best_i is None or best_ratio < 0.6:
                    break
                col_xs.pop(best_i)
            # 2026-09-15: 两联排/多联排表(项目名称|检查结果 成对重复, 高帅/白玮衡体格表
            # "身高|179.5cm  收缩压|121mmHg")—— 角色序列取**最小重复周期**分组, 每组各产
            # 一行; 常规五列表周期=列数 → 单行, 行为不变。
            ncol = len(roles_xs)
            period = ncol if ncol else 1
            for _p in range(1, ncol):
                if ncol % _p == 0 and all(roles_xs[i][0] == roles_xs[i % _p][0]
                                          for i in range(ncol)):
                    period = _p
                    break

            def _col_index(c):
                if not col_xs:
                    return None
                col = min(range(len(col_xs)), key=lambda k: abs(c.x0 - col_xs[k]))
                if len(col_xs) == len(roles_xs) and abs(c.x0 - col_xs[col]) <= 30.0:
                    return col
                if roles_xs:
                    # 2026-09-28: 数据左对齐、表头居中 → 表头中心 x0 恒 >= 数据左缘 x0。
                    # 空标志列(col_xs 比 roles 少一列)时旧"最近表头中心"会把 ref 数据
                    # (343.3) 误配给 flag 表头(308.9, 因 ref 列宽), 使"<10%阳性…"文本
                    # 参考范围落入 flag 列判黄(付立光 FPSA/TPSA)。改为取">=数据x0 的
                    # 最小表头中心"(单调映射): ref 数据 → ref 表头, 空 flag 列自然跳过。
                    # 2026-09-28(二次修正): 单调映射**仅在最近表头是 flag 且单元格是长
                    # 文本**时启用 —— 无条件单调会把序号列(无角色表头在其左)吃成名称
                    # (山东省立序号"14"/"20" 变成黄区名)。短标记(↑↓HL*)与序号等
                    # 无角色列维持旧"最近表头"行为。
                    k = min(range(len(roles_xs)),
                            key=lambda i: abs(c.x0 - roles_xs[i][1]))
                    if roles_xs[k][0] == "flag" and len(c.text.strip()) > 4:
                        cands = [i for i in range(len(roles_xs))
                                 if roles_xs[i][1] >= c.x0 - 3.0]
                        if cands:
                            k = min(cands, key=lambda i: roles_xs[i][1])
                    if abs(c.x0 - roles_xs[k][1]) <= 90.0:
                        return k
                return None

            for cells in seg_data:
                by_group: dict = {}
                for c in cells:
                    col = _col_index(c)
                    if col is None:
                        # 2026-10-19: 无表头的窄标志格(↑↓HL*)也要用作本行标志
                        # (陈阳 标志列在结果后/单位前, 表头无标志列 → 曾整批丢标志)
                        _t0 = c.text.strip()
                        if _t0 in ("↑", "↓", "H", "L", "*", "＊"):
                            float_flags.append(_t0)
                        continue
                    by_group.setdefault(col // period, []).append((col, c))
                for _gi, cps in by_group.items():
                    name_parts, result_parts, ref_parts, unit_parts, flag_parts = [], [], [], [], []
                    flag2_parts: List[str] = []
                    float_flags: List[str] = []
                    # 2026-10-19: 名称折行按阅读序(y, x)拼(陈阳 "学法)"+ "*总胆红素(化"
                    # 顺序被按 x0 排反 → "学法)*总胆红素(化")
                    for col, c in sorted(cps, key=lambda z: (round(z[1].y0 / 5), z[1].x0)):
                        role = roles_xs[col][0] if col < len(roles_xs) else None
                        t = c.text.strip()
                        if role is None:
                            continue
                        # 窄标志(↑↓HL)不应落非 flag 角色(体格表值后 ↑ 会被 ref 列吸走)
                        if t in ("↑", "↓", "H", "L") and role != "flag":
                            float_flags.append(t)
                            continue
                        if role == "name":
                            if _REPEAT_CHAR_JUNK_RE.match(t) or _HOSPITAL_HEADER_RE.match(t):
                                continue  # 2026-09-23 重复字装饰 / 2026-09-28 机构抬头不并名
                            name_parts.append(t)
                        elif role == "result":
                            result_parts.append(t)
                        elif role == "ref":
                            ref_parts.append(t)
                        elif role == "unit":
                            unit_parts.append(t)
                        elif role == "flag":
                            flag_parts.append(t)
                        elif role == "flag2":
                            flag2_parts.append(t)
                    name = "".join(name_parts).strip()
                    # 2026-10-16: 双栏表错位 —— 名称含"标签:日期+下一标签"("末次月经:
                    # 2026-4-23孕产史", 陈婷婷) → 拆成两行: 前一标签配日期, 剩余标签
                    # 配本行结果。
                    _md = re.match(
                        r"^([^:：]{1,10})[:：]\s*(\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}日?)"
                        r"(.+)$", name)
                    if _md and result_parts:
                        out.append({"item_name": _md.group(1).strip(),
                                    "result": _md.group(2), "unit": "",
                                    "ref_low": None, "ref_high": None,
                                    "signal_flag": 0})
                        name = _md.group(3).strip()
                        name_parts = [name]
                    # 2026-09-15: 名称列混入序号单元格("21"+"血小板分布宽度", 马鞍山) →
                    # 丢首纯数字部(仅当同组还有含汉字名称部, 防误删"25羟基维生素D"整体名)
                    if len(name_parts) > 1 and re.fullmatch(r"\d{1,4}", name_parts[0]) \
                            and any(re.search(r"[\u4e00-\u9fa5]", p) for p in name_parts[1:]):
                        name_parts = name_parts[1:]
                        name = "".join(name_parts).strip()
                    # 2026-10-19: 序号格竖直居中于折行名称中间(陈阳 "总胆红素(化2学法)")
                    # → 有汉字名时剔除任意纯数字段
                    if len(name_parts) > 1 and any(
                            re.search(r"[\u4e00-\u9fa5]", p) for p in name_parts):
                        name_parts = [p for p in name_parts
                                      if not re.fullmatch(r"\d{1,4}", p)]
                        name = "".join(name_parts).strip()
                    result_txt = "".join(result_parts).strip()
                    # 2026-10-19(韩先锋): 双值表(检测值 2+ / 结果 50) —— 结果取半定量,
                    # 计数值列非空 = 报告方异常标注 → 判黄
                    _dual_flag = False
                    _sq_parts = [p for p in result_parts if re.search(r"[+±＋]", p)]
                    if _sq_parts and any(re.fullmatch(r"[\d.]+", p.strip())
                                         for p in result_parts):
                        result_txt = "".join(_sq_parts).strip()
                        _dual_flag = True
                    # 2026-09-28: 结果前缀装饰标记剥除(单吉飞"◆19.3 ↓" —— ◆ 分隔符
                    # cell 被并入结果列, 与行式"19.3"重复且绿区名字带◆)
                    result_txt = _strip_result_marker(result_txt)
                    if not name or not result_txt or _skip_name(name):
                        continue
                    # 2026-09-15: 弃检/未检行不入库(步新宇 眼压"弃检"曾被坐标路径产出判黄)
                    if _FLAG_SKIP_RE.match(result_txt):
                        continue
                    # 2026-09-15: 坐标层不打 __auth —— __auth 只给文本列式的 rev/dual
                    # 块表做抑制; 坐标行打上会把信号通道(箭头/提示文字)对该行的标志
                    # 全部压掉(桂林 干化学酮体 flagtext 曾因此未判黄)。
                    # 2026-09-15: 剥名称前缀标记后再 strip —— "lstrip(标记)"只去标记字符,
                    # "* 总胆红素" → " 总胆红素" 会留空格(百色 护栏名对不上)
                    _name0 = name.lstrip("★*＊▲△").strip()
                    # 2026-10-19: 单位折行 "mmol/" + "L"(陈阳/韩先锋) —— 单位尾 token
                    # 曾作为窄标志进 float_flags → 并回单位, 不当标志
                    _unit_tails = {"L", "l", "ml", "mL", "mL", "dL", "dl", "μL", "ul", "uL",
                                   "g", "s", "min", "h", "U", "mg", "mmol", "μmol"}
                    while (float_flags and "".join(unit_parts).strip().endswith("/")
                           and float_flags[-1] in _unit_tails):
                        unit_parts.append(float_flags.pop())
                    _unit0 = "".join(unit_parts).strip()
                    if _junk_row_cell(_name0, _unit0, result_txt):
                        continue
                    row = {"item_name": _name0, "result": result_txt,
                           "unit": _unit0,
                           "ref_low": None, "ref_high": None, "signal_flag": 0}
                    tail_flag = ""
                    m = re.search(r"[（(](↑|↓|\*)[)）]$", result_txt)
                    if m:
                        tail_flag = m.group(1)
                        result_txt = result_txt[:m.start()]
                    # 2026-09-15: 结果与箭头同格("17.6 ↓"/"阳性 ↑", 4列无提示列, 陈镜霓)
                    # —— 剥离箭头并置标志; 否则箭头留在 result, 且归一化去重会顶掉
                    # 信号通道产出的正确行("17.6" flag=2), 导致标志丢失
                    _av, _aflag = _value_with_arrow(result_txt)
                    if _aflag:
                        result_txt = _av
                    else:
                        # 2026-09-16: 等级值+箭头(同上, 坐标路径专用)
                        _gv, _gflag = _grade_value_with_arrow(result_txt)
                        if _gflag:
                            result_txt = _gv
                            _aflag = True
                    nv = _norm_value_cell(result_txt)
                    result_txt = nv if nv is not None else result_txt.strip()
                    _qv, _qual = _split_qual_tail(result_txt)
                    if _qual:
                        result_txt = _qv
                    row["result"] = result_txt
                    for rt in reversed(ref_parts):
                        ru = _range_with_unit(rt)
                        if ru:
                            rlo, rhi, runit = ru
                            row["ref_low"], row["ref_high"] = rlo, rhi
                            if not row["unit"] and runit:
                                row["unit"] = runit
                            break
                    else:
                        for rt in ref_parts:
                            # 2026-09-28: 风险分层目标值("中、高危人群:<2.6；", 董涛 低密度
                            # 脂蛋白)不是本行通用参考 → 不落 ref(用户口径: 宁可不显示)
                            if re.search(r"人群|风险等级|危险分层", rt):
                                continue
                            lo, hi = _parse_ref(rt)
                            if lo or hi:
                                row["ref_low"], row["ref_high"] = lo, hi
                                break
                    flag_txt = "".join(flag_parts)
                    flag2_txt = "".join(flag2_parts)
                    if (_dual_flag or (flag2_txt.strip() and flag2_txt.strip() not in
                                       ("-", "–", "—", "／", "/", "正常", "阴性", "无"))
                            or _aflag or tail_flag or float_flags
                            or _qual in ("阳性", "弱阳性", "±")
                            or flag_txt in ("↑", "↓", "H", "L", "*", "异常")
                            or "*" in flag_txt
                            or re.search(r"(偏高|升高|增高|降低|偏低|阳性|异常)", flag_txt)):
                        row["signal_flag"] = 3
                    seg_rows.append(row)
            # 2026-09-15: **段级**结果形态门(取代原报告级整包弃用) —— 表区里常混入
            # 非化验叙述块(影像"检查所见"/体检须知编号/页眉元信息, 广西人民), 其
            # result 是长叙述; 只弃这些段, 保留化验/体格式表段(否则好段被连坐丢弃)。
            if seg_rows:
                bad = 0
                for r in seg_rows:
                    res = str(r["result"]).strip()
                    if not (re.match(r"^[<>≤≥]?\s*[\d.]+", res)
                            or re.match(r"^(阴性|阳性|弱阳性|正常|未见|未检出|无|未及|1\+|2\+|[1-5]\+)", res)
                            or _norm_value_cell(res) is not None
                            # 2026-09-16: 等级值/纯加号/单破折号("-"=阴性简写, 行式
                            # _QUAL_RE 同口径)也算合法结果形态 —— 否则白带常规类表
                            # ("Ⅲ级/++/-"多) 整段被形态门丢弃(陈镜霓 阴道分泌物表)。
                            or _GRADE_VAL_RE.match(res)
                            or _PLUS_ONLY_VAL_RE.match(res)
                            or res in ("-", "–", "—")):
                        # 2026-09-28: 只把**长叙述**(影像描述/须知文本)计为坏; 短定性/
                        # 颜色值("未提示"/"normal"/"清亮"/"稻黄色"/"+-")不算 —— 董涛
                        # 血脂4行与尿常规同段, 短定性超 20% 曾整段被弃(低/高密度脂蛋白
                        # 胆固醇绿区缺失)。
                        if len(res) >= 8:
                            bad += 1
                if bad / len(seg_rows) <= 0.2:
                    out.extend(seg_rows)
    return out
