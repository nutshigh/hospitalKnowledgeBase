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
    # 2026-09-12: 弱切分(多发现行/多方向行候选)按院声明 —— 默认关闭(新医院默认
    # 保守净度优先), 仅已验证存在"一行多发现"排版且需规则补漏的医院启用:
    # 池州(多方向行)/福建第二(编号行多发现)/广西人民(编号行多发现)
    "multi_findings": False,
    # 2026-10-20: 无表头/表头合并的表格按数据列内容推断角色(常海毅/姜兆龙型)
    "infer_columns": False,
    # 2026-10-20: 文本层 Kangxi 部首/兼容汉字归一(爱康报告 "⾎"/"⾼")
    "nfkc": False,
    # 2026-10-20: 仅用坐标列路径(文本行式会产出历史列/垃圾行; 常海毅)
    "layout_only": False,
    # 2026-10-21: 化验小结行("1.小圆细胞偏高：3.1")给既有指标行标黄
    "lab_summary_marks": False,
    # 2026-10-21: 爱康建议标题 01/02/… 序号归位
    "numbered_advice": False,
    # 2026-10-21: 同济 PDF 同层叠字折叠 / 裸序号归位 / 肥胖→BMI 标黄
    "collapse_repeats": False,
    "attach_leading_numbers": False,
    "bmi_marks": False,
    # 2026-10-21: 爱康结论首行清理
    "clean_important_head": False,
    # 2026-10-22: summary_marks 从全文取(结论段只取建议段时, 前半摘要仍需标黄)
    "summary_marks_from_text": False,
    # 2026-10-22: 单位列误并 ↑↓ 标志 / VLM 误标项目清理
    "strip_unit_flags": False,
    "clear_flags": [],
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
    # 2026-09-14: keyword 统一用**医院名**(原"出入镜"是单位名笔误/文本词); 华西报告
    # 文本含"华西" → 仍命中本档案(厦门华西_H004_11)。"
    dict(keywords=["华西"],
         anchor_only=True,
         extra_anchor=[r"^1[.、]\s*高甘油三酯血症及高密度脂蛋白降低"],
         extra_break=["检查项目", r"^[（(]?1?\s*[）)]?\s*吸烟", r"^[（(]?2?\s*[）)]?\s*饮酒"]),  # 21 华西: 列表尾到 9.超重, 吸烟/饮酒=个人史不入段
    # 2026-09-14: keyword 必须是**医院名**而非受检人单位名 —— 原用"出入境边防检查总站"
    # 会误命中所有边检职工在其它医院(华山/中医院等)的报告, anchor_only 劫持其结论段。
    dict(keywords=["弘爱"],
         anchor_only=True, table_conclusion=True,
         extra_anchor=["三、体检异常结果及医学建议"]),  # 22 厦门弘爱(表格结论, 需行重组)
    # 2026-09-15: 嘉兴市中医医院(陈镜霓)结论页无标题, 发现标题形如"◆总胆固醇增高…";
    # 其余医院用 ◆ 作科室分组(华山"◆ 血常规") —— 故仅按院声明 ◆ 为结论锚点, 避免
    # 把通用锚点表扩大误伤已适配报告。
    dict(keywords=["嘉兴市中医医院"], extra_anchor=[r"^◆"]),
    # === 2026-09-22: 第四批(新地区) ===
    # 台州市中心医院: 结论标题"科室医生结论汇总:"不在通用词表 → 起点被后文锚点挤掉,
    # 结论从半截【建议】开始; 且该页文本流乱序(标题/条目交错) → 视觉坐标重排
    dict(keywords=["台州市中心医院"], visual_sort=True,
         extra_anchor=[r"^科室医生结论汇总"]),
    # 南通市第一人民医院: 结论标题"本次体检总结"不在通用词表; 2026-09-22 用户验收:
    # 只取"健康指导建议"段(体检总结为过程罗列, 不入结论) → anchor_only
    dict(keywords=["南通市第一人民医院"], anchor_only=True,
         extra_anchor=[r"^健康指导建议"]),
    # 郑州市第一人民医院: 健康建议段中夹"放射影像查询+感谢信"通知块(跨页, 后半段
    # 起于句中无法逐行跳过) → 通知块起点**段尾断点**, 其后"健康建议"(第二处)是锚点
    # 自动重启收集(二、血脂异常…)。skip 保留兜底。
    dict(keywords=["郑州市第一人民医院"],
         extra_break=[r"^放射影像查询", r"^尊敬的受检者"],
         extra_skip=[r"^首先感谢您", r"^查询放射影像资料", r"^提示进行操作",
                     r"^体检人ID", r"^第\d+次体检"]),
    # 河南中医药大学第一附属医院(鲍海宽): 结论段只取"【本次体检建议】"(其后的
    # 【各分科检查结果】是分科明细, 用户 2026-09-23 验收口径)
    dict(keywords=["河南中医药大学第一附属医院"], anchor_only=True,
         extra_anchor=[r"^【本次体检建议】"],
         extra_break=[r"^【各分科检查结果】"]),
    # 镇江市第一人民医院(陈海波): 结论段只取"健康建议"(其前"体检汇总"为罗列,
    # 用户 2026-09-23 验收口径)
    dict(keywords=["镇江市第一人民医院"], anchor_only=True,
         extra_anchor=[r"^健康建议"],
         # 健康建议段后接分科彩超表(首页标题"彩超（前列腺、膀胱）"), 需段尾断点,
         # 否则分科表格行被收进结论(陈海波 2026-09-23 复验)
         extra_break=[r"^彩超（前列腺、膀胱）"]),
    # 大连大学附属中山医院(薄楠): 结论为编号清单(1-21), 第 21 点"乙肝免疫学检查全阴性"
    # 属清单一项, 声明 keep_normal_numbered 使编号正常行不被当噪声跳过
    dict(keywords=["大连大学附属中山医院"], keep_normal_numbered=True),
    # 福州大学附属省立医院: 结论页"主检结论及建议"(签名)在前、"体检结论汇总"(真内容,
    # 图片页 OCR)在后 → 声明 keep_post_sig 保留签名后的强标题段(默认关闭, 不影响他院)
    dict(keywords=["福州大学附属省立医院"], keep_post_sig=True),
    # 温州医科大学附属第一医院: 结论页"发现(左栏)|建议(右栏)"同行两栏, 块级文本流
    # 把右栏建议排到左栏发现之前 → 声明 visual_lines(行级 y,x 重排)
    # (2026-09-23: 曾加 merge_wrapped 合并折行片段, 用户复验效果不好 → 已回滚)
    dict(keywords=["温州医科大学附属第一医院"], visual_lines=True),
    # 2026-09-23: keyword 由裸"日照"改具体医院名 —— 薄楠报告结论含"增加日照"
# (骨质疏松建议)误命中本档案(anchor_only+医学建议)导致结论整段丢失
    dict(keywords=["日照市人民医院"],
         anchor_only=True, extra_anchor=["医学建议"]),  # 24 日照人民
    # 贵港东晖(用户 2026-09-12 口径): 结论段只取"异常指标"+"健康建议"两段,
    # "检查汇总"是体检项目明细(项目名非结果)不提取
    dict(keywords=["东晖医院"],
         anchor_only=True, extra_anchor=["异常指标"]),
    dict(keywords=["医 生 建 议"],
         anchor_only=True,
         extra_anchor=[r"医\s*生\s*建\s*议"],
         review_block=True),  # 25 茂名人民(分散字标题; 切段后剥离中段
         # "检 查 综 述"★枚举页 —— 综述条目与医生建议重复, 且该页插在建议区中间
    # 滨州人民(董延广): "体检综述"实为 2025/2026 两年对比表(非结论), 且文本流
    # 后接"疫苗接种健康提示"; 真正结论段 = "▍异常指标解读"(1.超重 … 7.右肾囊肿)。
    # anchor_only 只取该段(段尾=总检医师签名断点)。
    dict(keywords=["滨州市人民医院"],
         anchor_only=True,
         extra_anchor=["异常指标解读"]),
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
         multi_findings=True,  # 多方向行候选(尿比重偏高 酸碱度偏低 …)
         extra_anchor=[r"^体\s*检\s*综\s*述\s*$", r"^健康指导建议"],
         extra_break=["温馨提醒"]),
    # 2026-09-12: 弱切分声明(编号行多发现 → 规则补漏; 用户验收依赖:
    # 福建第二"牙石堆积/尿隐血微量/双髋…"、广西人民"十二指肠球部溃疡/
    # 心室早期复极 T波改变/水平椎管狭窄")
    dict(keywords=["福建省第二人民医院"], multi_findings=True),
    dict(keywords=["广西壮族自治区人民医院"], multi_findings=True),
    # === 2026-09-28: 第五批(新地区) ===
    # 中国医科大学附属盛京医院(厚柏刚/毕惠丽): 结论段无固定标题, 以个性化问候语
    # "尊敬的{姓名}先生/女士您好,您本次体检的建议信息如下:" 起段; 其后 1-17 编号
    # 条目, 尾接"其余已做检查未见异常"(其后"温馨提示/汇总医生"是免责声明/签名块)。
    # 通用锚点词表拼不出问候语整行 → 声明专用锚点 + 段尾断点。
    # 2026-09-28(用户验收反馈): 毕惠丽第 3 页文本流乱序(正文 9-13 点排在免责声明/
    # 签名块**之后**), 线性收集在签名处截断 → 漏 9-13 点; 声明 visual_sort 按页内
    # 视觉坐标重排(页 3 重排后: 9-13 点 → 免责声明 → 签名), 免责声明断点仍生效。
    dict(keywords=["盛京医院"], visual_sort=True,
         extra_anchor=[r"^尊敬的.{2,6}(?:先生|女士)您好,您本次体检的建议信息如下"],
         # 厚柏刚尾接"其余已做检查未见异常"; 毕惠丽无此行, 条目直接接免责声明
         # ("您本次体检选择的检查项目并未涵盖所有脏器…") → 两者都作段尾断点。
         extra_break=[r"其余已做检查未见异常", r"您本次体检选择的检查项目"]),
    # 三亚市人民医院(曾繁鲁): 结论标题"专家建议及健康指导:"("专家"不在通用锚点词表);
    # 其后 1-9 编号条目, 尾接"总检医生:"签名(通用 BREAK 已断), 再后"科室医生结论汇总:"
    # 是分科明细 → 声明专用锚点。
    dict(keywords=["三亚市人民医院"],
         extra_anchor=[r"^专家建议及健康指导"]),
    # 复旦大学附属华山医院福建医院(=福建医科大学附属第一医院滨海院区, 付立光):
    # 结论="体检结论分析"(通用锚点已命中), 但段尾"疾病解析"是逐条科普(尿酸增高/动脉硬化
    # 等)且后接"综合健康指导"检查结果表 —— 用户 2026-09-28 验收: 只取到"其他阳性结果"止。
    dict(keywords=["复旦大学附属华山医院福建医院", "福建医科大学附属第一医院"],
         extra_break=[r"^疾病解析"]),
    # 丹东市中心医院(蔡丰泽/生明月): 结论只取健康指导段 —— 蔡丰泽的"本次体检结论"是
    # 医生签名表格(疾病诊断+检查医生, 无建议)不入段; 生明月标题为"本次健康建议"。
    # 用户 2026-09-28 口径(蔡丰泽): 只需提取"健康指导建议"后的内容。
    dict(keywords=["丹东市中心医院"], anchor_only=True,
         extra_anchor=[r"^健康指导建议", r"^本次健康建议"]),
    # 江西中医药大学附属医院(蔡明): 报告为"文字标题 + 图片条带正文"混合型 —— 文本层
    # 只有条目名/表格标签, 检查结果/疾病解释/主检建议在宽条内容图里 → 声明
    # ocr_content_pages, 让 _extract_pdf_text(hybrid) 对宽条图页补 OCR。
    dict(keywords=["江西中医药大学附属医院"], ocr_content_pages=True),
    # 河南科技大学第二附属医院(单吉飞): 结论"【指导与建议】"在"终审医师/初审医师"签名块
    # **之后**还有续页条目(第4页: 肾小球滤过率增高/高密度脂蛋白胆固醇偏高/脉搏减慢/
    # 腰臀围比值偏低, 用户 2026-09-28 反馈"跨页后内容未提取到") → 签名行改跳过
    # (extra_skip 优先于通用断点, 同日实现); 页脚地址/页码碎片跳过; 段尾由第5页
    # 检查表的"检查项目"(通用 BREAK_RE2) 收口。
    dict(keywords=["河南科技大学第二附属医院"],
         extra_skip=[r"^(?:终审|初审|复审|主审)(?:医师|医生)?[:：]?$",
                     r"^总检评估意见[:：]?$", r"^审核（盖章）$",
                     r"^洛阳市西工区", r"^健康专线", r"^(?:共|页第|页|第)$"]),
    # === 2026-10-06: 第六批(新地区) ===
    # 吉林大学中日联谊医院(安周): 结论标题"主检结论和建议："(通用锚点词表拼不出整行 ——
    # "和"不在词表), 段尾"主检助理（汇总）："签名; 否则锚点落到正文"建议：…"行,
    # 前 6 条发现丢失。
    dict(keywords=["吉林大学中日联谊医院"],
         anchor_only=True,
         extra_anchor=[r"^主检结论和建议"],
         extra_break=[r"^主检助理"]),
    # 海南慈铭奥亚/儋州美年(蔡克田/蔡国炜, 同一模板): 结论="主检报告-一般健康问题:
    # [发现|建议]"表, 锚点取"一般健康问题:"(通用锚点落"医师建议"丢标题行); 段尾两断点:
    # "医学科普"(科普区, 用户 2026-10-07 反馈不应提取)与"分项报告"(各科明细表)。
    dict(keywords=["海南慈铭奥亚健康体检中心", "儋州美年大健康体检中心"],
         anchor_only=True,
         extra_anchor=[r"^一般健康问题"],
         extra_break=[r"^医学科普", r"^分项报告$"]),
    # 中科体检(陈霓): 结论="本次检查发现【…】"逐条(医学解释/健康建议), 每条尾
    # "如有疑问，请扫码提交解读报告"是页脚(跳过); 段尾接检后专家简介
    # (专家姓名+副主任医师简介), 以简介行作断点。
    dict(keywords=["中科体检"],
         extra_skip=[r"^如有疑问"],
         extra_break=[r"^\S{2,4}\s+副主任医师"]),
    # 八所出入境边防检查站(陈东海): 报告前半"体检结果汇总"是逐项罗列(双文本层已由
    # _dedup_double_layer 去重, 但仍多折行碎片如"同型半胱氨同型半胱氨/酸："),
    # 结论本体 = "一、诊断及建议"起(含 二、其他阳性结果及建议)。用户 2026-10-06
    # 拍板只取诊断及建议段。
    # 2026-10-07: 双文本层 PDF 的线性文本序把"各条目标题"甩到内容之后(视觉版面
    # 是"标题在前、内容随后") → 用户要求标题与内容归位(b)且补全 1-3 条详细内容(c):
    # 声明 visual_sort, 双文本层页去重后按 y 排序重排(见 _extract_pdf_text)。
    dict(keywords=["八所出入境边防检查"],
         visual_sort=True,
         anchor_only=True,
         extra_anchor=[r"^一、诊断及建议"]),

    # === 2026-10-12: 第七批(新地区) ===
    # 恩施州中心医院(郭小平/黄佳军, 同一模板): PDF 汉字间逐字空格("幽 门 螺杆菌")
    # 且页眉/表头带 ☑☑☑ 水印(☑ 已在 _extract_pdf_text 全局剥除) → despace 压缩写回
    # (展示+提取输入一并归一, 见 _compress_letter_spaced); 结论两段:
    # "体检异常结果汇总/体检综述"与"健康指导建议"(该行被电话号/表头粘连, 通用锚点
    # 拼不出整行 → 专用锚点); 段间"生活方式问卷"(是否吸烟/戒烟等选项题)作断点丢弃。
    # 2026-10-13(用户验收): 结论只取"健康指导建议"段(此前含异常结果汇总/体检综述
    # 被要求剔除) → anchor_only; 段内"重要疾病/阳性结果"(值"无")/疾病诊断/阳性发现
    # 三部分都要保留(曾整段 skip 被要求补回); 红字判黄按院声明(该院仅红字标注异常:
    # 血压/尿潜血/尿白细胞/EB 抗体, 无箭头)。
    dict(keywords=["恩施州中心医院"],
         despace=True,
         no_colon_strip=True,
         anchor_only=True,
         red_marks=True,
         # 2026-10-13(黄佳军验收): 只取"本次健康建议"(郭小平为"健康指导建议") ——
         # 去掉"体检结论与健康指导/本次体检总结"(黄佳军的总结段被要求剔除)
         extra_anchor=[r"^健康指导建议", r"^本次健康建议"],
         extra_break=[r"^生活方式问卷"],
         extra_skip=[r"^姓名：", r"^恩施州中心医院", r"^\d{4}-\d{7,8}"]),
    # 漳州市医院健康管理中心(石硕/陈雪婷): 石硕结论页(体检结论汇总/体检结论分析建议)
    # 整页为一张大图(文本层只剩页眉) → ocr_content_pages + 大图条件补 OCR;
    # 陈雪婷结论为"本次体检*室检查提示:…"逐块(通用锚点落段中"建议:"行) → 专用锚点
    # 取段首。注: 陈刚(第六批/福建省漳州市医院)同品牌, 锚点落于其段内不改变既有结论
    # (H004 三件套+结论重提取护栏守)。
    dict(keywords=["漳州市医院健康管理中心"],
         ocr_content_pages=True,
         extra_anchor=[r"^本次体检"],
         # 2026-10-13(石硕验收): 只取"体检结论分析建议" → 汇总段(体检结论汇总起)断开;
         # 分析建议跨页被签名块(总检医师/总审医师/总检时间)截断 → 签名行改跳过
         extra_break=[r"^体检结论汇总"],
         extra_skip=[r"^总检医师", r"^总审医师", r"^总检时间"]),
    # 漳州第三医院(陈炜/胡月): 结论"主要问题："清单在"医生建议："详解之前, 通用锚点
    # 只命中"医生建议"(段中) → 专用锚点取"主要问题"段首(词表不加"问题", 防存量漂移)。
    dict(keywords=["漳州第三医院"],
         # "亚健康"健康处方页(合理膳食/戒烟限酒/适当运动/心理方面)是"目前…亚健康状态"
         # 条目的正文(用户 2026-10-13 反馈"条目 5 下的内容缺失") → 加段锚点
         extra_anchor=[r"^主要问题", r"^通过本次体检，发现您已出现异常结果"],
         # 2026-10-13: 亚健康处方块(OCR 读取换序到页首)归位 —— 插到"5、定期复查"之前
         # (处方第 5 条; 用户验收: 复查应在 1-4 之后/末尾)
         tail_anchor=r"^通过本次体检，发现您已出现异常结果",
         tail_before=r"^\d+、定期复查",
         # 2026-10-13: 段跨页混入页眉("陈炜  2605210656"/"第"/"页"/医院抬头)
         extra_skip=[r"^漳州第三医院", r"^\S{2,4}\s+\d{8,}$", r"^第$", r"^页$"]),
    # 敦煌康美综合医院(纪卓武): 结论标题"【总检结论】"括号内无发现特征词, 通用括号
    # 锚点不认(且不得全局放行"总检", 单吉飞"【总检汇总】"会漂移) → 档案专用锚点。
    dict(keywords=["敦煌康美"],
         # 2026-10-13(用户验收): 只取"【异常结果解读】"段(此前取【总检结论】摘要段)。
         # 该段在"分析医师/总检医师"签名块之后 → anchor_only + keep_post_sig
         # (签名后强标题段保留, 见 _strong_sec 含"解读")。
         anchor_only=True,
         keep_post_sig=True,
         extra_anchor=[r"^【异常结果解读】"],
         # 2026-10-14: 段尾"敦煌康美综合医院祝您身体健康！"之后是分检附录 → 断点。
         extra_break=[r"^敦煌康美", r"^.{0,12}祝您.{0,6}健康"],
         # 2026-10-12: ◎分科条目内"；"分号连串多发现("◎血分析：血红蛋白升高；血小板数
         # 偏高；…")由弱切分候选确定性补漏(LLM 波动时漏整段).
         multi_findings=True),
    # 兰州大学第二医院(白瑞雪): 报告四部分结构, 用户验收只取"第三部分 体检健康建议"
    # (各条建议含条目名; 此前全抓导致前面综述/列表混入且建议段因重复标题被切碎)。
    dict(keywords=["兰州大学第二医院"],
         anchor_only=True,
         # 锚点必须带分隔符("第三部分:体检健康建议") —— 目录行"第三部分 体检健康建议。"
         # (空格)曾先命中, 把综述/重点问题列表整段拖进结论。
         extra_anchor=[r"^第三部分[:：]", r"^体检健康建议$"],
         extra_skip=[r"^兰州大学第二医院"]),
    # 漳州正兴医院(杨钧予): 用户验收只取"医学建议"段(体检汇总段剔除)
    dict(keywords=["漳州正兴医院"],
         anchor_only=True,
         extra_anchor=[r"^医学建议"]),
    # 哈密红星医院(邹佶博, 纯扫描): 用户验收只取"本次体检诊断及建议"段(体检综述剔除)
    dict(keywords=["红星医院"],
         anchor_only=True,
         extra_anchor=[r"^本次体检诊断及建议"]),
    # 江西出入境边防检查总站(任晓光): 结论"一、科室：/(N)条目/体检报告分析"行列式,
    # 冒号拆分把 (1)-(19) 条目行整列拆没(LLM 输入 836→208, 丢条目还产"体检彩超室"
    # 碎名) → 声明 no_colon_strip 发全文。
    dict(keywords=["江西出入境边防检查总站"],
         no_colon_strip=True,
         # 2026-10-14: 化验表 OCR 导出为倒序四元组([值][↓][参考][单位][名]), 通用
         # 行式把"名"配到下一行值(谷草 27/165 错位) → 按院启用倒序四元组解析。
         reversed_dump=True),
    # 崇左市人民医院(滕寿权, 2026-10-15 用户反馈): 结论段**只取"医生建议:"下内容** ——
    # 报告前半为分科检查结果汇总(【血脂四项】/【肝功能】/【心电图】/【B超】…), 不属
    # 总检结论; 通用锚点曾把汇总段一并收进结论。
    dict(keywords=["崇左市人民医院"],
         anchor_only=True,
         extra_anchor=[r"^医生建议"]),
    # === 2026-10-16: 第八批(11 份) ===
    # 临沂市中医医院(李承龙): 与日照中医同模板, 结论标题为"医学建议"(无冒号), 通用锚点
    # 不命中 → 专用锚点; 段尾由签名/页码断点收口。
    dict(keywords=["临沂市中医医院"], anchor_only=True,
         extra_anchor=[r"^医学建议"]),
    # 昆明医科大学第一附属医院(陈婷婷): 结论 = "体检阳性结果分析及建议"下 1-21 条;
    # 总检医生/日期签名插在第 13/14 条之间(跨页) → 跳过(extra_skip 优先于通用断点)。
    dict(keywords=["昆明医科大学第一附属医院"], anchor_only=True,
         extra_anchor=[r"^体检阳性结果分析及建议"],
         # 总检医生/日期签名插在第 13/14 条之间; "主检医师温馨提示"是正文标题(非签名)
         extra_skip=[r"^总检医生[:：]", r"^总检日期[:：]", r"^主检医师温馨提示"]),
    # 温州医科大学附属第二医院(包强): 结论 = "主要健康问题与建议/次要健康问题与建议"
    # (异常结果汇总表格与指标线重复, 不入结论); 段尾 = 体力活动问卷科普。
    # 2026-10-16 用户反馈: "8、总体力活动低"的说明(IPAQ 段)属于该条内容 → 不截断,
    # 段尾由主检医生签名收口; "PID：" 页眉字段跳过。
    dict(keywords=["温州医科大学附属第二医院"], anchor_only=True,
         extra_anchor=[r"^主要健康问题与建议"],
         extra_skip=[r"^PID[:：]"]),
    # 复旦大学附属中山医院厦门医院(陈佳漩): 结论 = "结论汇总"(一、一般检查…六、检验)
    # + "结论分析"(逐项建议); 段尾 = 医学名词解析(科普)。
    # 2026-10-16 用户口径: 只取"结论分析"段(结论汇总不入结论)
    # no_colon_strip: 该段是"条目名:建议"标签式结构 —— 冒号剥离会把条目名全删掉
    # (LLM 提取 0 条, 与恩施/江西边检同因), 发全文。
    dict(keywords=["复旦大学附属中山医院"], anchor_only=True,
         extra_anchor=[r"^结论分析"],
         extra_break=[r"^医学名词解析"],
         no_colon_strip=True),
    # 联勤保障部队第910医院(陈坤铭): 结论 = "医生建议"下 1-11 条; 段尾签名/装订字段。
    dict(keywords=["910医院"], anchor_only=True,
         extra_anchor=[r"^医生建议[:：]"],
         # 2026-10-16 用户反馈: 跨页页眉(日期/第N页/预约号/医院名)混入结论 → 跳过
         extra_skip=[r"^审核时间[:：]", r"^装订", r"^日期[:：]",
                     r"^第\s*\d*$", r"^\d+\s*页$", r"^预约号[:：]",
                     r"^[/／]$", r"^联勤保障部队第910医院$"]),
    # 开滦总医院(韩先锋): 结论 = "阳性结果汇总"段(逐条发现+建议); "合理建议"为模板
    # 套话、其后为分科结果。目录行("2. 阳性结果汇总")不命中(用整行锚点)。
    dict(keywords=["开滦总医院"], anchor_only=True,
         extra_anchor=[r"^阳性结果汇总$"],
         extra_break=[r"^合理建议", r"^补充说明"],
         keep_bare_numbers=True,  # 2026-10-19: 条目序号(裸"1/2/3…")保留
         # 2026-10-19: 名称折行 = 中文名 + 纯拉丁缩写("单核细胞百分数"/"MON%") → 合并
         latin_wrap=True),
    # 日照市中医医院(付本明/封猛): 跨页页眉(医院名/重复"健康建议：")混入结论 →
    # 逐行跳过(用户反馈封猛: 跨页"健康建议："把句子割断)
    dict(keywords=["日照市中医医院"],
         extra_skip=[r"^健康建议[:：]$", r"^日照市中医医院(?:健康管理[（(]体检[)）]中心)?$",
                     r"^美好人生", r"^登记号[:：]?"],
         # 2026-10-19(用户口径): 结果非数字/文字参考且表内无标志的项(尿红细胞(隐血)+1/
         # 尿蛋白弱阳性), 以总结段异常项给既有指标行标黄
         summary_marks=True),
    # 中国中医科学院望京医院(陈阳): 表内无提示列, 异常用红色字体标注 →
    # red_marks_ref(用户 2026-10-19: 红色字体**超参考**才判黄; 无参考/范围内保持绿)
    dict(keywords=["中国中医科学院望京医院"], red_marks_ref=True),
    # 厦门大学附属中山医院(陈宏宏): 文本层字体映射乱码, 生产需 FORCE_OCR_TASKS 强制
    # 逐页 OCR; 结论 = "体检结论分析"段。
    dict(keywords=["厦门大学附属中山医院"], anchor_only=True,
         extra_anchor=[r"^体检结论分析"]),
    # 厦门大学附属心血管病医院(陈行富): 扫描混合件(hybrid OCR); 结论 = "体检结论分析"
    # 段; 段尾裸日期页脚("2026-07-07")跳过(裸日期全局跳过会误伤存量(79 锁定漂移),
    # 故按院声明)。
    dict(keywords=["厦门大学附属心血管病医院"], anchor_only=True,
         extra_anchor=[r"^体检结论分析"],
         extra_skip=[r"^\d{4}-\d{1,2}-\d{1,2}$"],
         # 2026-10-19: 结论跨页续段(前列腺增生/尿酮体/…/乙肝两对半)是图片块,
         # 该页文本 135 字 > hybrid 阈值(100) 未 OCR → ocr_content 补 OCR
         ocr_content_pages=True),

    # === 2026-10-20: 第九批(新地区) ===
    # 葫芦岛市第二人民医院(刘振宇): 结论 = "体检结论与健康指导"下"本次体检总结"
    # + "本次健康建议"两段; 通用锚点只命中后者会漏掉总结清单 → 专用锚点。
    dict(keywords=["葫芦岛市第二人民医院"],
         anchor_only=True,
         extra_anchor=[r"^本次健康建议"]),
    # 江门五邑中医院(周业潮): 结论 = "总检建议与结论" + "异常解读"; 段尾"检查详情"
    # 起为分科明细, 明确断开。
    dict(keywords=["江门五邑中医院"],
         anchor_only=True,
         extra_anchor=[r"^异常解读"],
         extra_break=[r"^检查详情"]),
    # 三亚边检模板(姜兆龙): PDF 无医院名, 仅有受检单位可作稳定标识; 该模板化验表
    # 不带箭头/提示, 异常只在"总检结论与建议"列出 → summary_marks 给既有指标行标黄。
    # 仅启用 summary_marks(不设 anchor/break), 误命中他院报告也只影响黄标、不改结论。
    dict(keywords=["三亚出入境边防检查站"], summary_marks=True),
    # 临沧市人民医院(宋文军, 纯扫描): VLM 误标 RDW-SD/MPV/MCV(无标志/提示) → 清标志;
    # 其余 HBsAb/脂蛋白(a) 等真实异常保留。
    dict(keywords=["临沧市人民医院"],
         clear_flags=["红细胞分布宽度SD值", "平均血小板体积", "平均红细胞体积"]),
    # 牡丹江医科大学附属红旗医院(常海毅): 化验表无表头(文本层逐列 dump), 需按数据
    # 列内容推断角色; 结论从第二次"◆主要健康问题"的【N】摘要起, 到第 7 页明细前止。
    dict(keywords=["牡丹江医科大学附属红旗医院"],
         infer_columns=True,
         layout_only=True,
         summary_marks=True,
         lab_summary_marks=True,
         visual_sort=True,
         anchor_only=True,
         extra_anchor=[r"^◆主要健康问题"],
         # 第 7 页起为身高/体重等"未做"明细(页首 cm); 医生签名/日期先跳过
         extra_break=[r"^cm$"],
         extra_skip=[r"^\d{4}-\d{2}-\d{2}$"]),
    # 山东阳光融和医院(甄元): 结论标题"主检结论与建议"不在通用词表 → 专用锚点;
    # 跨页重复表头/登记号行/引导句跳过, 汇总医生签名收口。
    dict(keywords=["山东阳光融和医院"],
         strip_unit_flags=True,
         extra_anchor=[r"^主检结论与建议"],
         extra_skip=[r"^\s*主检结论与建议\s+主检报告\s*$", r"^\s*登记号[:：]",
                     r"^本次体检项目中"],
         extra_break=[r"^汇总医生"]),
    # 武汉市中心医院后湖院区(胡艳): 结论 = "总检建议与结论" + "异常解读";
    # 段尾"检查详情"起为分科明细。
    dict(keywords=["武汉市中心医院后湖院区"],
         anchor_only=True,
         extra_anchor=[r"^异常解读"],
         extra_break=[r"^检查详情"]),
    # 爱康国宾/爱康卓悦(高亮): 文本层为康熙部首兼容字("⾎"/"⾼"), 需 NFKC
    # 归一; 用户口径(2026-10-21): 只取"2.专家建议与指导"段 —— 前半"1.体检重要
    # 异常结果"里的 01 便隐血/结果说明 是错位摘要, 不提取。
    dict(keywords=["爱康国宾", "爱康卓悦"],
         nfkc=True,
         layout_only=True,
         summary_marks=True,
         numbered_advice=True,
         keep_bare_numbers=True,
         bmi_marks=True,
         summary_marks_from_text=True,
         anchor_only=True,
         extra_anchor=[r"^2\.专家建议与指导"],
         extra_break=[r"^3\.健康体检结果"],
         extra_skip=[r"^下载爱康APP$", r"^查看彩色报告$",
                     r"^全国统一报告解读专线", r"^[0-9A-F ]{40,}$",
                     r"^爱康卓悦", r"^司[）)]$",
                     r"^\s*\d+\s*/\s*\d+\s*$"]),
    # 同济医院健康管理中心(龚文怡): 疾病诊断/阳性发现两张表按行输出, 标题行
    # "疾病诊断："与"阳性发现："不在通用词表 → 专用锚点 + 视觉重排(标题在内容前);
    # 段尾"温馨提示"后是未完成项目/签名块。
    dict(keywords=["同济医院健康管理中心"],
         visual_sort=True,
         anchor_only=True,
         keep_bare_numbers=True,
         collapse_repeats=True,
         attach_leading_numbers=True,
         extra_anchor=[r"^疾病诊断", r"^阳性发现"],
         extra_skip=[r"^\d{5,}$"],
         extra_break=[r"^温馨提示"]),

]


def match_profile(text: str) -> dict:
    """按报告全文匹配医院档案; **合并所有命中档案**(2026-09-08) —— 同一报告可能
    命中多个档案(莆田报告同时含"莆田九十五医院"与"出入境边防检查站"字样), 首个命中
    返回会让专用锚点被通用档案顶掉; 合并时 extra_break/skip/anchor 拼接, anchor_only
    取或。未命中返回 DEFAULT。"""
    # 2026-09-12: 必须深拷列表字段 —— dict(DEFAULT) 浅拷贝时
    # merged["extra_*"] 与 DEFAULT 共享同一 list, 后续 setdefault().extend()
    # 会把命中档案的规则**永久写进模块级 DEFAULT**, 污染长驻进程里之后所有
    # 报告(护栏测试暴露: 日照(24)之后柳州切段 1466→2503)。
    merged = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT.items()}
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
        if prof.get("multi_findings"):
            merged["multi_findings"] = True
        # 2026-09-22: 新档案开关必须在此白名单登记 —— 否则 match_profile 不拷贝,
        # 档案声明形同虚设(温州 visual_lines / 福州 keep_post_sig 曾因此未生效)
        if prof.get("visual_lines"):
            merged["visual_lines"] = True
        if prof.get("keep_post_sig"):
            merged["keep_post_sig"] = True
        if prof.get("keep_normal_numbered"):
            merged["keep_normal_numbered"] = True
        # 2026-10-19: keep_bare_numbers(开滦 条目序号) 必须登记白名单
        if prof.get("keep_bare_numbers"):
            merged["keep_bare_numbers"] = True
        if prof.get("latin_wrap"):
            merged["latin_wrap"] = True
        if prof.get("ocr_content_pages"):
            merged["ocr_content_pages"] = True
        # 2026-10-12: 字距空格结论压缩写回(恩施州中心医院)白名单
        if prof.get("despace"):
            merged["despace"] = True
        if prof.get("no_colon_strip"):
            merged["no_colon_strip"] = True
        # 2026-10-13: 红字判黄按院声明(恩施)白名单
        if prof.get("red_marks"):
            merged["red_marks"] = True
        if prof.get("red_marks_ref"):
            merged["red_marks_ref"] = True
        if prof.get("summary_marks"):
            merged["summary_marks"] = True
        # 2026-10-13: tail_anchor 首块后移(漳州第三医院亚健康处方)白名单
        if prof.get("tail_anchor"):
            merged["tail_anchor"] = prof["tail_anchor"]
        if prof.get("tail_before"):
            merged["tail_before"] = prof["tail_before"]
        if prof.get("reversed_dump"):
            merged["reversed_dump"] = True
        # 2026-10-20: 无表头表格列角色推断白名单
        if prof.get("infer_columns"):
            merged["infer_columns"] = True
        if prof.get("nfkc"):
            merged["nfkc"] = True
        if prof.get("layout_only"):
            merged["layout_only"] = True
        if prof.get("lab_summary_marks"):
            merged["lab_summary_marks"] = True
        if prof.get("numbered_advice"):
            merged["numbered_advice"] = True
        if prof.get("collapse_repeats"):
            merged["collapse_repeats"] = True
        if prof.get("attach_leading_numbers"):
            merged["attach_leading_numbers"] = True
        if prof.get("bmi_marks"):
            merged["bmi_marks"] = True
        if prof.get("clean_important_head"):
            merged["clean_important_head"] = True
        if prof.get("summary_marks_from_text"):
            merged["summary_marks_from_text"] = True
        if prof.get("strip_unit_flags"):
            merged["strip_unit_flags"] = True
        if prof.get("clear_flags"):
            merged.setdefault("clear_flags", []).extend(list(prof["clear_flags"]))
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
