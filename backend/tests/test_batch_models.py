from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile


def test_create_tables_in_memory_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    b = BatchImport(
        id="b1", hospital_id="H001", user_id="admin",
        filename="x.zip", archive_path="/tmp/x.zip",
    )
    db.add(b); db.commit()

    f = BatchImportFile(
        id="f1", batch_id="b1", file_path="u/x.pdf",
        file_size=1024, crc32="deadbeef",
    )
    db.add(f); db.commit()

    assert db.query(BatchImport).count() == 1
    assert db.query(BatchImportFile).count() == 1
    assert b.status == "uploading"
    assert b.total == 0 and b.parsed_ok == 0 and b.interp_ok == 0 and b.failed == 0
    assert f.status == "queued"
    # unique constraint(batch_id, crc32)
    dup = BatchImportFile(id="f2", batch_id="b1", file_path="u/x2.pdf",
                          file_size=1, crc32="deadbeef")
    db.add(dup)
    try:
        db.commit()
        assert False, "should raise on duplicate (batch_id,crc32)"
    except Exception:
        db.rollback()