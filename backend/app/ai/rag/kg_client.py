"""Neo4j 知识图谱客户端。

封装 Neo4j 连接、CM3KG 导入、实体检索。
供 KGRetriever 和 import_cm3kg.py 脚本使用。
"""
import ast
import csv
import logging
from typing import Optional

from neo4j import GraphDatabase
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class KGResult(BaseModel):
    entity: str
    entity_type: str
    description: str
    neighbors: list[dict]
    score: float
    text: str


def _parse_list(val: str) -> list:
    """解析 CSV 中的 Python literal list 字段，如 "['紫绀', '胸痛']"。"""
    if not val or not val.strip():
        return []
    try:
        parsed = ast.literal_eval(val)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if x]
        return [str(parsed).strip()]
    except (ValueError, SyntaxError):
        return [val.strip()] if val.strip() else []


class KGClient:
    """Neo4j 知识图谱客户端，模块级单例。"""

    _instance: Optional["KGClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._driver = None
        self._available = False
        self._initialized = True

    def _get_driver(self):
        if self._driver is None:
            try:
                self._driver = GraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                )
                self._driver.verify_connectivity()
                self._available = True
                logger.info("Neo4j connected: %s", settings.NEO4J_URI)
            except Exception as e:
                self._available = False
                logger.warning("Neo4j unavailable: %s", e)
        return self._driver

    def is_available(self) -> bool:
        if not settings.KG_ENABLED:
            return False
        try:
            driver = self._get_driver()
            if not driver:
                return False
            with driver.session() as session:
                result = session.run("MATCH (n) RETURN count(n) as cnt")
                cnt = result.single()["cnt"]
                return cnt > 0
        except Exception:
            return False

    def import_cm3kg(self, csv_path: str) -> dict:
        """从 CM3KG CSV 文件批量导入节点和关系到 Neo4j。

        返回 {"diseases": N, "symptoms": N, ...} 统计。
        """
        driver = self._get_driver()
        if not driver:
            raise RuntimeError("Neo4j not connected")

        stats = {"diseases": 0, "symptoms": 0, "drugs": 0,
                 "checks": 0, "departments": 0, "treatments": 0, "relations": 0}

        with driver.session() as session:
            # 清空已有数据
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("Cleared existing Neo4j data")

            # 创建索引
            for label, prop in [("Disease", "name"), ("Symptom", "name"),
                                ("Drug", "name"), ("Check", "name"),
                                ("Department", "name"), ("Treatment", "name")]:
                session.run(
                    f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
                )

            # 批量导入
            batch = []
            batch_size = 500
            with open(csv_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    batch.append(row)
                    if len(batch) >= batch_size:
                        stats = self._import_batch(session, batch, stats)
                        batch = []
                if batch:
                    stats = self._import_batch(session, batch, stats)

        self._available = True
        logger.info("CM3KG import done: %s", stats)
        return stats

    def _import_batch(self, session, rows: list[dict], stats: dict) -> dict:
        """导入一批疾病行。"""
        for row in rows:
            name = row.get("name", "").strip()
            if not name:
                continue

            # Disease 节点
            desc = (row.get("desc") or "").strip()
            prevent = (row.get("prevent") or "").strip()
            cause = (row.get("cause") or "").strip()
            yibao = (row.get("yibao_status") or "").strip()
            cure_lasttime = (row.get("cure_lasttime") or "").strip()
            cured_prob = (row.get("cured_prob") or "").strip()
            cost_money = (row.get("cost_money") or "").strip()
            get_prob = (row.get("get_prob") or "").strip()
            get_way = (row.get("get_way") or "").strip()

            session.run(
                """
                MERGE (d:Disease {name: $name})
                SET d.desc = $desc, d.prevent = $prevent, d.cause = $cause,
                    d.yibao_status = $yibao, d.cure_lasttime = $cure_lasttime,
                    d.cured_prob = $cured_prob, d.cost_money = $cost_money,
                    d.get_prob = $get_prob, d.get_way = $get_way
                """,
                name=name, desc=desc, prevent=prevent, cause=cause,
                yibao=yibao, cure_lasttime=cure_lasttime, cured_prob=cured_prob,
                cost_money=cost_money, get_prob=get_prob, get_way=get_way,
            )
            stats["diseases"] += 1

            # Symptom
            for sym in _parse_list(row.get("symptom", "")):
                if not sym:
                    continue
                session.run(
                    "MERGE (s:Symptom {name: $name})",
                    name=sym,
                )
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (s:Symptom {name: $sname})
                    MERGE (d)-[:HAS_SYMPTOM]->(s)
                    """,
                    dname=name, sname=sym,
                )
                stats["symptoms"] += 1
                stats["relations"] += 1

            # Drug (common_drug + recommand_drug)
            seen_drugs = set()
            for drug in (_parse_list(row.get("common_drug", "")) +
                         _parse_list(row.get("recommand_drug", ""))):
                if not drug or drug in seen_drugs:
                    continue
                seen_drugs.add(drug)
                session.run("MERGE (dr:Drug {name: $name})", name=drug)
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (dr:Drug {name: $drname})
                    MERGE (d)-[:TREAT_WITH]->(dr)
                    """,
                    dname=name, drname=drug,
                )
                stats["drugs"] += 1
                stats["relations"] += 1

            # Check
            for chk in _parse_list(row.get("check", "")):
                if not chk:
                    continue
                session.run("MERGE (c:Check {name: $name})", name=chk)
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (c:Check {name: $cname})
                    MERGE (d)-[:RECOMMEND_CHECK]->(c)
                    """,
                    dname=name, cname=chk,
                )
                stats["checks"] += 1
                stats["relations"] += 1

            # Department
            for dept in _parse_list(row.get("cure_department", "")):
                if not dept:
                    continue
                session.run("MERGE (dep:Department {name: $name})", name=dept)
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (dep:Department {name: $depname})
                    MERGE (d)-[:BELONGS_TO]->(dep)
                    """,
                    dname=name, depname=dept,
                )
                stats["departments"] += 1
                stats["relations"] += 1

            # Treatment (cure_way)
            for treat in _parse_list(row.get("cure_way", "")):
                if not treat:
                    continue
                session.run("MERGE (t:Treatment {name: $name})", name=treat)
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (t:Treatment {name: $tname})
                    MERGE (d)-[:TREATMENT_METHOD]->(t)
                    """,
                    dname=name, tname=treat,
                )
                stats["treatments"] += 1
                stats["relations"] += 1

            # Accompany (并发症, Disease -> Disease)
            for acomp in _parse_list(row.get("acompany", "")):
                if not acomp:
                    continue
                session.run("MERGE (a:Disease {name: $name})", name=acomp)
                session.run(
                    """
                    MATCH (d:Disease {name: $dname}), (a:Disease {name: $aname})
                    MERGE (d)-[:ACCOMPANIED_BY]->(a)
                    """,
                    dname=name, aname=acomp,
                )
                stats["relations"] += 1

        return stats

    def search_entities(self, query: str, top_k: int = 3) -> list[KGResult]:
        """关键词匹配实体 → 查 1-hop 邻居 → 返回 KGResult 列表。

        两个匹配通道并行(对每个分词 term):
        1. Disease 通道:疾病名 CONTAINS term → 1-hop 邻居(Symptom/Drug/Check/...)
        2. Check 通道:检验项名 CONTAINS term → 反查 (Disease)-[:RECOMMEND_CHECK]->(Check)
           用来覆盖"指标名"类 query,如"淋巴细胞百分数"/"血清丙氨酸氨基转移酶"——
           KG 里这类概念挂在 Check 节点上而不在 Disease 名里,旧版只查 Disease 会全空命中。

        结果按 degree 降序、按 entity 去重后取前 top_k 条。
        """
        driver = self._get_driver()
        if not driver:
            return []

        # 分词
        try:
            import jieba
            terms = [t.strip() for t in jieba.cut(query) if len(t.strip()) >= 2]
        except ImportError:
            # fallback:无 jieba 时退化为按标点/空格切分
            terms = [w.strip() for w in query.replace(",", " ").replace("，", " ").replace("?", " ").replace("？", " ").replace("的", " ").split() if len(w.strip()) >= 2]

        if not terms:
            terms = [query.strip()]

        results = []
        # Check 通道过采:degree 列宽易把"通用化验"(degree 高、语义不专一)顶上来
        # 所以这里 LIMIT 拉到 5×top_k,Python 端再按 query↔entity 字符相似度 + degree 综合排序
        check_oversample = max(top_k * 5, 15)
        try:
            with driver.session() as session:
                for term in terms:
                    # --- Disease 通道:疾病名 CONTAINS term → 邻居 ---
                    cypher_disease = """
                    MATCH (d:Disease)
                    WHERE d.name CONTAINS $term
                    WITH d, COUNT { (d)--() } AS degree
                    ORDER BY degree DESC
                    LIMIT $top_k
                    OPTIONAL MATCH (d)-[r]->(neighbor)
                    RETURN d.name AS entity, d.desc AS desc,
                           collect(DISTINCT {
                             name: neighbor.name,
                             type: labels(neighbor)[0],
                             relation: type(r)
                           }) AS neighbors,
                           degree
                    """
                    for rec in session.run(cypher_disease, term=term, top_k=top_k):
                        entity = rec["entity"]
                        if not entity:
                            continue
                        neighbors = [
                            n for n in (rec["neighbors"] or [])
                            if n and n.get("name")
                        ]
                        desc = rec["desc"] or ""
                        degree = rec["degree"] or 0

                        text_lines = [f"实体: {entity} (疾病)"]
                        if desc:
                            text_lines.append(f"描述: {desc[:200]}")
                        if neighbors:
                            text_lines.append("相关知识:")
                            for n in neighbors[:8]:
                                text_lines.append(
                                    f"  - {n['relation']} → {n['name']} ({n['type']})"
                                )
                        text = "\n".join(text_lines)

                        results.append(KGResult(
                            entity=entity,
                            entity_type="Disease",
                            description=desc,
                            neighbors=neighbors,
                            score=float(degree),
                            text=text,
                        ))

                    # --- Check 通道:检验项名 CONTAINS term → 反查疾病 ---
                    cypher_check = """
                    MATCH (c:Check)
                    WHERE c.name CONTAINS $term
                    WITH c, COUNT { (c)--() } AS degree
                    ORDER BY degree DESC
                    LIMIT $cap
                    OPTIONAL MATCH (d:Disease)-[:RECOMMEND_CHECK]->(c)
                    RETURN c.name AS entity,
                           collect(DISTINCT d.name) AS diseases,
                           degree
                    """
                    for rec in session.run(cypher_check, term=term, cap=check_oversample):
                        entity = rec["entity"]
                        if not entity:
                            continue
                        diseases = [d for d in (rec["diseases"] or []) if d]
                        degree = rec["degree"] or 0

                        text_lines = [f"实体: {entity} (检验项)"]
                        if diseases:
                            text_lines.append("相关疾病:")
                            for d in diseases[:8]:
                                text_lines.append(f"  - {d}")
                        text = "\n".join(text_lines)

                        results.append(KGResult(
                            entity=entity,
                            entity_type="Check",
                            description="",
                            neighbors=[],
                            score=float(degree),
                            text=text,
                        ))

            # 综合 score = query↔entity 字符相似度 boost + degree。
            # SequenceMatcher.ratio() 通常 0.1~0.5, *1000 = 100~500,
            # 远大于 degree(KG node degree 极少 >100),
            # 即"与 query 字面贴近的实体"优先于"邻居数多的热门实体"。
            from difflib import SequenceMatcher
            for r in results:
                boost = SequenceMatcher(None, query, r.entity).ratio() * 1000
                r.score = r.score + boost

            # 去重 + 排序
            seen = set()
            unique = []
            for r in results:
                if r.entity not in seen:
                    seen.add(r.entity)
                    unique.append(r)
            unique.sort(key=lambda x: x.score, reverse=True)
            return unique[:top_k]

        except Exception as e:
            logger.warning("KG search failed: %s", e)
            return []


kg_client = KGClient()
