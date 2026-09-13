"""总检建议/结论展示链路回归样本集(确定性层)。

背景(2026-09-03): 结论段落定位/重排规则散在 app.modules.report.service,
历史教训: 每修一家新模板的补丁都可能破坏已验收报告。本文件把已验收样本的
关键语义固化为期望断言; 规则改动后跑
    pytest tests/modules/report/test_conclusion_regression.py
即知是否回归。

字段说明:
- rel: 样本 PDF 相对仓库根 ./体检报告样例/ 的路径
- visual: True 表示 PDF 多栏混排, 需按页视觉(y,x)坐标重排文本流(防城港市中医院)
- ocr: True 表示纯图片 PDF(无文本层), 需 OCR 服务在线, 默认跳过
- must_contain: 重排后文本必须包含的片段(来自逐份验收点)
- forbidden: 重排后**任何一行**都不得包含的子串(页眉/分检/签名/装饰行)
- expected_titles: 确定性编号/标题解析(_parse_numbered_titles)必须产出的异常名
  (取真实解析名, 避免臆造; 空列表 = 该样本不查此项)
"""
from pathlib import Path

SAMPLES_ROOT = Path(__file__).resolve().parents[3].parent / "体检报告样例"

SAMPLES = [
    dict(key="liuzhou_shikun", rel="广西体检报告测试/广西柳州市人民医院.pdf",
         indicator_expected=['视力(左)', '总胆固醇', '低密度脂蛋白胆固醇', '血尿酸', 'γ-谷氨酰转移酶'],
         must_contain=["体检结果分析与建议", "2.轻度肥胖", "(1)合理控制饮食", "请到眼科验光矫正"],
         forbidden=["异常体检结果汇总", "终审医生", "一般检查", "初审", "第 2 页"],
         expected_titles=["轻度肥胖", "脂肪肝", "血尿酸升高"]),
    dict(key="guilin_zhangya", rel="广西体检报告测试/广西桂林市南溪山医院.pdf",
         indicator_expected=['血小板分布宽度', '二氧化碳结合力', '高密度脂蛋白', '低密度脂蛋白'],
         must_contain=["体检综述", "甲状腺左叶结节", "甲状腺右叶结节", "肝内钙化点", "健康指导建议"],
         forbidden=["0773", "终审", "初审"],
         expected_titles=[],
         # 小结标题(五、【甲状腺彩超】等)下的分行条目 —— 确定性兜底 A 步
         expected_summary_titles=["甲状腺左叶结节", "甲状腺右叶结节", "肝内钙化点", "肝囊肿"]),
    dict(key="wuzhou_xieguobin", rel="广西体检报告测试/广西梧州市中医医院.pdf",
         indicator_expected=['乙肝表面抗体(发光法)', '癌胚抗原定量', '尿酸', '平均血小板体积(MPV)', '甘油三脂'],
         must_contain=["体检结论分析", "胆囊息肉样病变", "右肾肾盂旁囊肿", "前列腺囊肿"],
         forbidden=["体检结论汇总", "身高", "健康体检报告"],
         expected_titles=["超重", "肝小囊肿", "胆囊息肉样病变", "二尖瓣返流"]),
    dict(key="chongzuo_tengshouquan", rel="广西体检报告测试/广西崇左市人民医院.pdf",
         indicator_expected=['身高体重指数', '低密度脂蛋白胆固醇', '甘油三酯', '丙氨酸氨基转移酶', 'L-γ-谷氨酰基转移酶'],
         must_contain=["【血脂四项】", "低密度脂蛋白增高4.73mmol/L", "脂肪肝声像"],
         forbidden=["请关注您", "您的健康", "0773"],
         expected_titles=["血脂异常", "右肾囊肿", "甲状腺双侧叶囊性病灶"]),
    dict(key="baise_liudan", rel="广西体检报告测试/广西百色市右江民族医学院附属医院.pdf",
         indicator_expected=['总胆固醇', '低密度脂蛋白胆固醇', '总胆红素', '载脂蛋白B', '血小板分布宽度'],
         must_contain=["体检结论分析", "幽门螺旋杆菌感染", "胆红素偏高"],
         forbidden=["咨询电话", "您的健康", "第 页"],
         expected_titles=["幽门螺旋杆菌感染", "血脂异常"]),
    dict(key="guigang_yinjianming", rel="广西体检报告测试/广西贵港市东晖医院.pdf",
         indicator_expected=['淋巴细胞绝对数', '单核细胞绝对数', '尿酸', '乙型肝炎病毒表面抗体'],
         must_contain=["多因素共同作用的结果", "限制饮酒而减轻，必要时可药物治疗",
                       "1.甲状腺右叶囊实混合性回声团", "健康建议"],
         forbidden=["请您仔细阅读体检报告", "向专家咨询", "初审", "体检报告送达温馨提示", "免费向专家"],
         expected_titles=["甲状腺右叶囊实混合性回声团", "双肺下叶微小结节", "前列腺钙化", "肥胖"],
         # 2026-09-12 用户口径: 贵港结论段只取"异常指标"+"健康建议", "检查汇总"
         # (【彩超…】小结区)不提取 → summary 断言清空
         expected_summary_titles=[]),
    dict(key="guangxirenmin_zhupinlong", rel="广西体检报告测试/广西壮族自治区人民医院.pdf",
         indicator_expected=['尿酸（UA）', '总胆固醇（TC）', '甘油三酯（TG）', '血红蛋白（HGB）', '乙型肝炎核心抗体'],
         must_contain=["原因有：生理性因素、前列腺增生", "8:前列腺稍大伴局部钙化", "13:慢性咽炎",
                       "颈动脉粥样硬化"],
         forbidden=["2.内痔", "3.梨状窝隆起性质待查", "地址：南宁市桃源路", "邮编：530021",
                    "打印日期"],
         expected_titles=["慢性萎缩性胃炎", "混合性高脂血症", "前列腺稍大伴局部钙化"]),
    dict(key="fcg1_xuweixiang", rel="广西体检报告测试/广西防城港市第一人民医院.pdf",
         indicator_expected=['谷丙转氨酶(ALT)', '尿酸(UA)', '乙肝表面抗体(HBsAb)', '红细胞平均体积(MCV)'],
         must_contain=["体检结论及建议", "● 谷丙转氨酶偏高", "以下是您本次异常结果的主要部分汇总"],
         forbidden=["健康热线", "第3页", "0770", "体检编号"],
         expected_titles=["谷丙转氨酶偏高", "腹型肥胖"]),
    dict(key="fcg_tcm_hupeng", rel="广西体检报告测试/广西防城港市中医医院.PDF", visual=True,
         indicator_expected=['总胆固醇(TCHO)', '甘油三酯(TG)', '低密度脂蛋白胆固醇(LDL-C)', '肌酸激酶(CK)', '尿酸(UA)'],
         must_contain=["右肺上叶尖段钙化灶", "多为肺部感染性疾病炎症后形成的陈旧性病灶",
                       "甲状腺左侧叶低回声结节", "肌酸激酶（CK）升高"],
         forbidden=["本次体检总结", "健康指导建议：", "****", "体检编号", "年龄："],
         expected_titles=["甲状腺左侧叶低回声结节", "右肺上叶尖段钙化灶", "前列腺钙化灶", "超重"]),
    dict(key="qinzhou2_panghaifeng", rel="广西体检报告测试/广西钦州市第二人民医院.PDF",
         indicator_expected=['裸眼视力（右）', '裸眼视力（左）', '钾(K)'],
         must_contain=["针对您本次体检结果", "右肾强光团", "牙面较多色素沉着",
                       "【1】 胸部:平扫:右肺中叶内侧段微小结节"],
         forbidden=["钦州市第二人民医院健康管理中心", "初检", "体格检查", "0777-2873333"],
         expected_titles=["右肾强光团", "牙面较多色素沉着", "左肾结石"]),
    dict(key="beijing_chenmeishan", rel="陈美杉_H003_10.pdf",
         indicator_expected=['体重指数', '血小板压积'],
         must_contain=["体重指数>24", "窦性心律不齐", "胆囊结节"],
         forbidden=["体检编号", "姓名：", "第 页"],
         expected_titles=["体重指数>24", "肺结节", "窦性心律不齐"]),
    dict(key="beijing_buxinyu", rel="步新宇_H004_11.pdf",
         indicator_expected=['游离前列腺特异性抗原', '血清同型半胱氨酸', '肌酸激酶'],
         must_contain=["血肌酸激酶偏高", "甲状腺双叶多发囊性结节", "外耳道耵聍"],
         forbidden=["体检编号", "姓名：", "第 页"],
         expected_titles=["血肌酸激酶偏高", "外耳道耵聍"],
         # 2026-09-10: "2、胸部CT 平扫：右肺尖…" 半截方法名曾被当标题兜底填入
         # (真名靠后续文本/LLM), 用户报告后加禁产断言。
         forbidden_titles=["胸部CT", "甲状腺B", "腹部B"]),
    # 纯图片 PDF(钦州中/欧阳庆): 无文本层, 需 OCR 服务, 默认跳过
    dict(key="qinzhou_tcm_ouyangqing", rel="广西体检报告测试/广西钦州市中医医院.pdf", ocr=True,
         must_contain=["左肾内强回声团", "窦性心律不齐"],
         forbidden=["体检编号", "第 页"],
         expected_titles=[]),

    # === 各地汇总(2026-09-04 验收): 精修级 ===
    dict(key="maanshan_chentian", rel="各地汇总/马鞍山人民_H003_10.pdf",
         must_contain=["检查综述", "医生建议", "[肥胖]", "[胆囊结石]"],
         forbidden=["体检号", "健康档案号"],
         expected_titles=["肥胖", "胆囊结石", "脂肪肝"]),
    dict(key="dehong_bijianguo", rel="各地汇总/德宏州人民_H003_10.pdf",
         must_contain=["1.血脂异常", "5.轻度阻塞性肺通气功能障碍", "总胆固醇(CHOL)偏高"],
         forbidden=["检查所见：", "总检建议："],
         expected_titles=["血脂异常", "轻度阻塞性肺通气功能障碍"]),
    # === 各地汇总(2026-09-04 验收): 冒烟级(能定位/够长/无脏行, 未逐字精修) ===
    dict(key="xiamen_huaxi", rel="各地汇总/厦门华西_H004_11.pdf", smoke=True, min_len=800),
    dict(key="shandong_lisheng", rel="各地汇总/山东省立_H004_11.pdf", smoke=True, min_len=400),
    dict(key="rizhao_renmin", rel="各地汇总/日照人民_H004_11.pdf", smoke=True, min_len=1500),
    dict(key="chizhou_renmin", rel="各地汇总/池州人民_H003_10.pdf", smoke=True, min_len=400),
    dict(key="binzhou_renmin", rel="各地汇总/滨州人民_H003_10.PDF", smoke=True, min_len=300),
    dict(key="chaozhou_diyi", rel="各地汇总/潮州第一_H003_10.pdf", smoke=True, min_len=300),
    dict(key="qilu_qingdao", rel="各地汇总/齐鲁青岛_H004_11.pdf", smoke=True, min_len=100),
    dict(key="xiamen_hongai", rel="各地汇总/厦门弘爱_H004_11.pdf", smoke=True, min_len=400),
    dict(key="putian_jiushiwu", rel="各地汇总/莆田九十五_H004_11.pdf", smoke=True,
         visual=True, min_len=400),
    # 茂名人民: ★ 标记异常 + 分散排版标题("医 生 建 议"/"检 查 综 述")
    dict(key="maoming_chencanming", rel="各地汇总/茂名人民_H004_11.pdf",
         must_contain=["检 查 综 述", "★  血压偏低", "载脂蛋白B偏高"],
         forbidden=["咨询电话", "主审日期"],
         expected_titles=["血压偏低", "总胆固醇偏高", "载脂蛋白B偏高"]),
]
