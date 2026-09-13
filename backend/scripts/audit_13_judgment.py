"""13 份(广西11+北京2)判定链盘点: 当前代码提取+triage vs DB 验收黄红名单。"""
import os
import sys

sys.path.insert(0, "/home/wjyy2/hospitalKnowledgeBase/backend")
sys.path.insert(0, "/home/wjyy2/hospitalKnowledgeBase/backend/scripts")
from audit_display_sampling import pipeline, judge_level  # noqa: E402
from app.modules.interpretation.rules_engine import RulesEngine  # noqa: E402
import pymysql  # noqa: E402

SAMPLES = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"
GX = os.path.join(SAMPLES, "广西体检报告测试")

CASES = [
    ("hospital_H003", 1, os.path.join(GX, "广西崇左市人民医院.pdf")),
    ("hospital_H003", 2, os.path.join(GX, "广西壮族自治区人民医院.pdf")),
    ("hospital_H003", 3, os.path.join(GX, "广西百色市右江民族医学院附属医院.pdf")),
    ("hospital_H003", 4, os.path.join(GX, "广西贵港市东晖医院.pdf")),
    ("hospital_H003", 5, os.path.join(GX, "广西防城港市中医医院.PDF")),
    ("hospital_H003", 6, os.path.join(GX, "广西防城港市第一人民医院.pdf")),
    ("hospital_H003", 7, os.path.join(SAMPLES, "陈美杉_H003_10.pdf")),
    ("hospital_H004", 1, os.path.join(GX, "广西柳州市人民医院.pdf")),
    ("hospital_H004", 2, os.path.join(GX, "广西桂林市南溪山医院.pdf")),
    ("hospital_H004", 3, os.path.join(GX, "广西梧州市中医医院.pdf")),
    ("hospital_H004", 4, os.path.join(GX, "广西钦州市中医医院.pdf")),
    ("hospital_H004", 5, os.path.join(GX, "广西钦州市第二人民医院.PDF")),
    ("hospital_H004", 6, os.path.join(SAMPLES, "步新宇_H004_11.pdf")),
]


def old_yellow(db, rid):
    c = pymysql.connect(host="127.0.0.1", user="root", password="root", database=db, charset="utf8mb4")
    cur = c.cursor()
    cur.execute(
        f"""SELECT ij.item_name FROM {db}.indicator_judgment ij
            JOIN {db}.report_interpretation i ON ij.interpretation_id = i.id
            JOIN {db}.report_indicator ri ON ij.indicator_id = ri.id
            WHERE i.report_id=%s AND ri.raw_text IS NULL
              AND ij.color_level IN ('yellow','red') ORDER BY ij.id""", (rid,))
    out = [r[0] for r in cur.fetchall()]
    c.close()
    return out


def main():
    for db, rid, pdf in CASES:
        tag = os.path.basename(pdf).replace(".pdf", "").replace(".PDF", "")
        old = old_yellow(db, rid)
        if not os.path.exists(pdf):
            print(f"### {tag}: 样本缺失"); continue
        inds = pipeline(pdf)
        if not inds:
            print(f"### {tag}: 文本提取 0(图片型?) 旧黄红={old}")
            continue
        eng = RulesEngine()
        hosp = db.replace("hospital_", "")
        new = [i["item_name"] for i in inds
               if judge_level(i, eng, hosp) in ("yellow", "red")]
        gone = [n for n in old if n not in new]
        added = [n for n in new if n not in old]
        print(f"### {tag}: 旧{len(old)} 新{len(new)}")
        if gone:
            print("   旧有新增无:", gone)
        if added:
            print("   新增:", added)
        print("   新名单:", " | ".join(new[:16]))


if __name__ == "__main__":
    main()
