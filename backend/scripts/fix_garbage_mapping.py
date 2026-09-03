"""一次性: 修复 H003/H004 的垃圾映射/脏std/disease_hit。
1. 删除 LOCAL 垃圾自映射(句子式/【】检查项/碎片/指标自映射)
2. 结论行 item_name_standard 按新规则重算(term_normalizer 或原文)
3. 清空 disease_hit 并按当前 judgment 重算
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sqltext

from app.core.database import get_session
from app.modules.report.service import normalize_item_name

DBS = ["hospital_H003", "hospital_H004"]

# LOCAL 垃圾自映射特征(结论名不会含这些)
_LOCAL_JUNK_RE = re.compile(
    r"【|】|改善|符合|不佳|可评估|可发展|部分患者|高危人群|建议|多饮水|为缺血|斑块形成|"
    r"动脉狭窄|管紧张素|已属|未见|未扩张|分流|液性暗区|肛门不适|梨状窝隆起|"
    r"颈\d+/\d+|^慢性$|^溃疡$|^脂$|^部肿瘤$|^尿常规$|^钙化改变$|肝功能检查提示|"
    r"肾脏损害|90↑|50↑|/3类|肾功\d+项|收缩期瓣口|心包腔内|房、室水平|大动脉水平|"
    r"十二指肠溃$|^肌酸激酶$|^γ一谷氨酰转移酶$"
)


def main():
    for db in DBS:
        s = get_session(db)
        try:
            # ---- 1. 删 LOCAL 垃圾 ----
            rows = s.execute(sqltext(
                f"SELECT id, item_name_standard, disease_name FROM {db}.disease_mapping "
                f"WHERE source='LOCAL'"
            )).fetchall()
            del_ids = [
                r[0] for r in rows
                if r[1] == r[2] and _LOCAL_JUNK_RE.search(r[1])
            ]
            for rid in del_ids:
                s.execute(sqltext(f"DELETE FROM {db}.disease_mapping WHERE id=:id"), {"id": rid})
            s.commit()
            print(f"[{db}] 删除 LOCAL 垃圾映射 {len(del_ids)} 条", flush=True)

            # ---- 2. 结论行 std 重算(term_normalizer 或原文) ----
            concl = s.execute(sqltext(
                f"SELECT id, item_name, item_name_standard FROM {db}.report_indicator "
                f"WHERE raw_text IS NOT NULL"
            )).fetchall()
            fixed = 0
            for cid, cname, cstd in concl:
                tn, _ = normalize_item_name(cname)
                want = tn if tn and tn != cname.replace(" ", "").replace("　", "") else cname
                if want != cstd:
                    s.execute(sqltext(
                        f"UPDATE {db}.report_indicator SET item_name_standard=:w WHERE id=:id"
                    ), {"w": want, "id": cid})
                    fixed += 1
            s.commit()
            print(f"[{db}] 修正结论行 std {fixed} 条", flush=True)

            # ---- 3. 清 disease_hit 重算 ----
            s.execute(sqltext(f"DELETE FROM {db}.disease_hit"))
            s.commit()
            from app.modules.risk.worker import compute_and_store
            report_ids = [r[0] for r in s.execute(
                sqltext(f"SELECT id FROM {db}.report_info")
            ).fetchall()]
            for rid in report_ids:
                n = compute_and_store(s, rid)
                print(f"   report {rid}: {n} hits", flush=True)
        finally:
            s.close()
    print("完成", flush=True)


if __name__ == "__main__":
    main()
