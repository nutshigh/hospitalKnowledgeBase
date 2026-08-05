#!/usr/bin/env python3
"""演示数据灌入脚本（**仅本地/演示环境使用，严禁在生产执行**）。

⚠️⚠️ 警告：本脚本会向数据库写入模拟数据（check_type='DEMO'），
⚠️⚠️ 请仅在本地演示库（hospital_H001 等）上运行，严禁在生产库执行！

向 hospital_H001 写入 2021-2025 年、5 个单位、男女两性的模拟体检报告数据，
用于验证统计分析端点（多维对比/趋势/疾病谱）。数据量约 2500 份报告。

执行：cd ~/hospitalKnowledgeBase/backend && .venv-stat/bin/python scripts/demo/demo_seed_stat.py
回滚：DELETE FROM indicator_judgment WHERE interpretation_id IN (SELECT id FROM report_interpretation WHERE report_id IN (SELECT id FROM report_info WHERE check_type='DEMO'));
      DELETE FROM report_interpretation WHERE report_id IN (SELECT id FROM report_info WHERE check_type='DEMO');
      DELETE FROM report_info WHERE check_type='DEMO';
"""
import random
import sys

import pymysql

DB = dict(host="127.0.0.1", port=3306, user="root", password="root",
          database="hospital_H001", charset="utf8mb4")

random.seed(20260724)

UNITS = ["深圳边检总站医院", "罗湖分院", "福田分院", "南山分院", "宝安分院"]
YEARS = [2021, 2022, 2023, 2024, 2025]
# (指标名, 基准检出率%, 年度增长斜率) —— 与 disease_mapping 种子对应
ITEMS = [
    ("血压", 24.0, 0.8), ("空腹血糖", 10.0, 0.5), ("糖化血红蛋白", 9.0, 0.4),
    ("总胆固醇", 20.0, 0.6), ("甘油三酯", 18.0, 0.5), ("低密度脂蛋白胆固醇", 15.0, 0.4),
    ("尿酸", 12.0, 0.3), ("体重指数", 20.0, 0.5), ("腹部B超", 16.0, 0.3),
    ("心电图", 6.0, 0.3), ("甲胎蛋白", 1.2, 0.1), ("癌胚抗原", 1.0, 0.1),
    ("头颅CT", 2.0, 0.15), ("血红蛋白", 8.0, 0.0), ("尿常规", 5.0, 0.0),
]

# 性别/年龄修正：男性部分指标更高；年龄越大检出率越高
def hit_rate(base, gender, age, year_idx):
    rate = base + year_idx * next(s for _, b, s in ITEMS if b == base or True) * 0
    return base


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    # 清掉旧演示数据，保证幂等
    cur.execute("DELETE ij FROM indicator_judgment ij JOIN report_interpretation interp ON ij.interpretation_id=interp.id JOIN report_info ri ON interp.report_id=ri.id WHERE ri.check_type='DEMO'")
    cur.execute("DELETE interp FROM report_interpretation interp JOIN report_info ri ON interp.report_id=ri.id WHERE ri.check_type='DEMO'")
    cur.execute("DELETE FROM report_info WHERE check_type='DEMO'")

    report_sql = ("INSERT INTO report_info (task_id, user_id, name, gender, age, report_date, check_type, unit_name, created_at) "
                  "VALUES (NULL, %s, %s, %s, %s, %s, 'DEMO', %s, NOW())")
    interp_sql = ("INSERT INTO report_interpretation (report_id, overall_level, red_count, yellow_count, green_count, summary_text, status, created_at, completed_at) "
                  "VALUES (%s, %s, %s, %s, %s, '演示数据', 'completed', NOW(), NOW())")
    judge_sql = ("INSERT INTO indicator_judgment (interpretation_id, indicator_id, item_name, result_value, deviation, color_level, certainty) "
                 "VALUES (%s, 0, %s, %s, %s, %s, 'high')")

    uid = 900000
    total_reports = 0
    for year_idx, year in enumerate(YEARS):
        for unit_idx, unit in enumerate(UNITS):
            n = 90 + unit_idx * 10 + year_idx * 5
            for _ in range(n):
                uid += 1
                gender = "男" if random.random() < 0.55 else "女"
                age = random.randint(22, 75)
                cur.execute(report_sql, (uid, f"演示{uid}", gender, age, f"{year}-0{random.randint(1,9)}-15", unit))
                report_id = cur.lastrowid
                total_reports += 1

                red = yellow = green = 0
                judgments = []
                for item, base, slope in ITEMS:
                    rate = base + slope * year_idx
                    if gender == "女":
                        rate *= 0.85
                    if age >= 60:
                        rate *= 1.7
                    elif age >= 46:
                        rate *= 1.3
                    elif age < 30:
                        rate *= 0.4
                    # 单位间轻微差异
                    rate *= 1 + (unit_idx - 2) * 0.05
                    if random.random() * 100 < rate:
                        level = "red" if random.random() < 0.45 else "yellow"
                        if level == "red":
                            red += 1
                        else:
                            yellow += 1
                        judgments.append((report_id, item, "异常", "偏高", level))
                    else:
                        green += 1
                overall = "red" if red > 0 else ("yellow" if yellow > 0 else "green")
                cur.execute(interp_sql, (report_id, overall, red, yellow, green))
                interp_id = cur.lastrowid
                for j in judgments:
                    cur.execute(judge_sql, (interp_id, j[1], j[2], j[3], j[4]))
    conn.commit()
    print(f"done: {total_reports} demo reports inserted into hospital_H001")
    cur.close()
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
