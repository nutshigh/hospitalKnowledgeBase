import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.batch_sweeper import _sweep_once
from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def sweeper_db(engine):
    """Patch get_hospital_db so _sweep_once runs against the in-memory SQLite.

    Each call yields a fresh session bound to the shared StaticPool connection,
    so the sweeper closing its session does not affect the test's own session.
    """
    Session = sessionmaker(bind=engine)

    def _gen(hospital_id):
        s = Session()
        try:
            yield s
        finally:
            s.close()

    patcher = patch("app.core.batch_sweeper.get_hospital_db", side_effect=_gen)
    patcher.start()
    test_session = Session()
    try:
        yield test_session
    finally:
        test_session.close()
        patcher.stop()


def _past(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


class TestSweep:

    def test_sweep_advances_stuck_parsing(self, sweeper_db):
        b = BatchImport(
            id="b1", hospital_id="H001", user_id="u", filename="x.zip",
            archive_path="/tmp/x.zip", status="parsing", total=2, parsed_ok=2,
            interp_ok=0, failed=0,
        )
        b.updated_at = _past(2000)
        sweeper_db.add(b)
        sweeper_db.commit()

        _sweep_once()

        sweeper_db.refresh(b)
        assert b.status == "completed"
        assert b.completed_at is not None

    def test_sweep_republishes_stuck_extracting(self, sweeper_db):
        b = BatchImport(
            id="b2", hospital_id="H001", user_id="u", filename="x.zip",
            archive_path="/tmp/x.zip", status="extracting", total=2,
        )
        b.updated_at = _past(2000)
        sweeper_db.add(b)
        sweeper_db.commit()

        with patch("app.core.batch_sweeper.BatchService.publish_extract_task") as mock_pub:
            _sweep_once()

        assert mock_pub.call_count == 1
        assert mock_pub.call_args.args[0] == "b2"

    def test_sweep_reaper_uploads_orphan(self, sweeper_db):
        tmp = tempfile.mkdtemp()
        storage_dir = os.path.join(tmp, "H001", "batch")
        os.makedirs(storage_dir, exist_ok=True)
        archive_path = os.path.join(storage_dir, "b3.zip")
        part_path = os.path.join(storage_dir, "b3.part0")
        with open(part_path, "wb") as f:
            f.write(b"chunk")

        b = BatchImport(
            id="b3", hospital_id="H001", user_id="u", filename="x.zip",
            archive_path=archive_path, status="uploading",
        )
        b.updated_at = _past(8000)
        sweeper_db.add(b)
        sweeper_db.commit()

        assert os.path.exists(part_path)
        _sweep_once()

        assert sweeper_db.query(BatchImport).filter_by(id="b3").count() == 0
        assert sweeper_db.query(BatchImportFile).count() == 0
        assert not os.path.exists(part_path)

    def test_sweep_skips_cancelled(self, sweeper_db):
        b = BatchImport(
            id="b4", hospital_id="H001", user_id="u", filename="x.zip",
            archive_path="/tmp/x.zip", status="cancelled", total=2,
            parsed_ok=2,
        )
        b.updated_at = _past(2000)
        sweeper_db.add(b)
        sweeper_db.commit()

        with patch("app.core.batch_sweeper.BatchService.publish_extract_task") as mock_pub:
            _sweep_once()

        sweeper_db.refresh(b)
        assert b.status == "cancelled"
        assert mock_pub.call_count == 0

    def test_sweep_skips_recent(self, sweeper_db):
        b = BatchImport(
            id="b5", hospital_id="H001", user_id="u", filename="x.zip",
            archive_path="/tmp/x.zip", status="parsing", total=2, parsed_ok=1,
        )
        b.updated_at = datetime.now(timezone.utc)
        sweeper_db.add(b)
        sweeper_db.commit()

        with patch("app.core.batch_sweeper.BatchService.publish_extract_task") as mock_pub:
            _sweep_once()

        sweeper_db.refresh(b)
        assert b.status == "parsing"
        assert mock_pub.call_count == 0