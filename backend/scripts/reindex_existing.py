"""一次性迁移脚本：将 MySQL 中的知识库条目重新索引到 LlamaIndex MilvusVectorStore。

用途：旧 collection schema（手写）与 LlamaIndex schema 不兼容，需 drop 后从 MySQL 重建。
运行：cd backend && uv run python scripts/reindex_existing.py [hospital_id]
不传 hospital_id 则重建所有医院的 knowledge_entry。
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.config import settings
from app.core.database import get_hospital_db
from app.ai.config import ensure_milvus_started
from app.ai import rag as ai_rag
from app.modules.knowledge.models import KnowledgeEntry


def reindex_hospital(hospital_id: str):
    print(f"Reindexing hospital {hospital_id}...")
    ensure_milvus_started()

    db = next(get_hospital_db(hospital_id))
    try:
        entries = db.query(KnowledgeEntry).filter(KnowledgeEntry.status == 1).all()
        entry_dicts = [
            {"id": e.id, "title": e.title, "content": e.content,
             "category_id": e.category_id, "source_file": e.source_file}
            for e in entries
        ]
    finally:
        db.close()

    if not entry_dicts:
        print(f"  Hospital {hospital_id}: no entries, skipping")
        return

    ai_rag.reindex_hospital(hospital_id, entry_dicts)
    print(f"  Hospital {hospital_id}: reindexed {len(entry_dicts)} entries")


def main():
    if len(sys.argv) > 1:
        reindex_hospital(sys.argv[1])
    else:
        from app.core.database import get_all_hospital_ids
        for hid in get_all_hospital_ids():
            reindex_hospital(hid)
    print("Done.")


if __name__ == "__main__":
    main()
