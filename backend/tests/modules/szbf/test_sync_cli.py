import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import sync_szbf_station as cli
from scripts.sync_szbf_station import build_parser


class FakeDB:
    def __init__(self):
        self.closed = False
        self.rollback_calls = 0

    def close(self):
        self.closed = True

    def rollback(self):
        self.rollback_calls += 1


class FakeReader:
    def __init__(self, exams=(), conclusion_users=(), latest=None, error=None,
                 report_rows=None, station="北京总站"):
        self.exams = list(exams)
        self.conclusion_users = list(conclusion_users)
        self.latest = dict(latest or {})
        self.error = error
        self.report_rows = list(report_rows or [("u9", "王五")])
        self.station = station
        self.exams_calls = []
        self.conclusion_calls = []

    def fetch_exams(self, changed_since=None, limit=None):
        self.exams_calls.append((changed_since, limit))
        if self.error:
            raise self.error
        exams = list(self.exams)
        return exams[:limit] if limit else exams

    def fetch_changed_conclusion_users(self, changed_since):
        self.conclusion_calls.append(changed_since)
        return list(self.conclusion_users)

    def fetch_latest_exam_for_user(self, user_id):
        return self.latest.get(user_id)

    def fetch_conclusion_only_users(self):
        return list(self.report_rows)

    def station_name(self):
        return self.station


def _exam(user_id, exam_code):
    return SimpleNamespace(user_id=user_id, exam_code=exam_code)


def _target(indicators=3, diseases=2):
    return SimpleNamespace(
        indicators=[object()] * indicators,
        conclusion_diseases=[object()] * diseases,
    )


def _base_args(*extra):
    return ["--source-host", "source", "--source-db", "db", *extra]


def _install(monkeypatch, *, reader, db, state=None):
    calls = {
        "session_names": [],
        "deleted": 0,
        "upserts": [],
        "states": [],
        "reads": [],
        "transforms": [],
        "reports": [],
    }

    def fake_session(name):
        calls["session_names"].append(name)
        return db

    def fake_read_state(session, source_key):
        calls["reads"].append((session, source_key))
        return dict(state or {})

    def fake_write_state(session, source_key, **kwargs):
        calls["states"].append((session, source_key, kwargs))

    def fake_delete_all(session):
        calls["deleted"] += 1
        return 7

    def fake_upsert(session, target):
        calls["upserts"].append((session, target))

    def fake_transform(exam, profile, source):
        calls["transforms"].append((exam, profile, source))
        return _target()

    def fake_write_conclusion_report(station, org_id, rows, output_dir):
        calls["reports"].append((station, org_id, rows, output_dir))
        return "report.md"

    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(cli, "_source_engine", lambda args: object())
    monkeypatch.setattr(cli, "SourceReader", lambda engine, org_id: reader)
    monkeypatch.setattr(cli, "get_session", fake_session)
    monkeypatch.setattr(cli, "get_profile", lambda org_id: object())
    monkeypatch.setattr(cli, "read_state", fake_read_state)
    monkeypatch.setattr(cli, "write_state", fake_write_state)
    monkeypatch.setattr(cli, "delete_all_szbf", fake_delete_all)
    monkeypatch.setattr(cli, "upsert_report", fake_upsert)
    monkeypatch.setattr(cli, "transform_exam", fake_transform)
    monkeypatch.setattr(cli, "write_conclusion_only_report", fake_write_conclusion_report)
    monkeypatch.setattr(cli, "_warn_if_no_disease_seed", lambda db: None)
    return calls


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.org_id == "133"
    assert args.target_hospital == "133"
    assert args.full_rebuild is False
    assert args.incremental is False
    assert args.dry_run is False


def test_parser_rejects_full_rebuild_with_incremental():
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--full-rebuild", "--incremental"])
    assert exc_info.value.code == 2


def test_main_full_rebuild_deletes_upserts_and_writes_ok_state(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1"), _exam("u2", "e2")])
    calls = _install(monkeypatch, reader=reader, db=db)

    code = cli.main(_base_args("--full-rebuild"))

    assert code == 0
    assert calls["deleted"] == 1
    assert len(calls["upserts"]) == 2
    assert calls["reads"][0][1] == "szbf:133->133"
    assert reader.exams_calls == [(None, None)]
    assert len(calls["states"]) == 1
    _, source_key, kwargs = calls["states"][0]
    assert source_key == "szbf:133->133"
    assert kwargs["status"] == "ok"
    assert kwargs["last_detail_update"] == kwargs["last_conclusion_update"]
    assert kwargs["message"] == "exams=2 indicators=6 diseases=4"
    assert db.closed is True
    assert db.rollback_calls == 0
    assert len(calls["reports"]) == 1
    assert calls["reports"][0][0] == "北京总站"
    assert calls["reports"][0][1] == "133"
    assert calls["reports"][0][2] == [("u9", "王五")]


def test_main_incremental_dedupes_detail_and_conclusion_changes(monkeypatch):
    db = FakeDB()
    detail_exam = _exam("u1", "e1")
    unseen_exam = _exam("u2", "e2")
    reader = FakeReader(
        exams=[detail_exam],
        conclusion_users=["u1", "u2"],
        latest={"u1": _exam("u1", "e1"), "u2": unseen_exam},
    )
    calls = _install(
        monkeypatch,
        reader=reader,
        db=db,
        state={
            "last_detail_update": "2026-01-01 00:00:00",
            "last_conclusion_update": "2026-01-02 00:00:00",
        },
    )

    code = cli.main(_base_args("--incremental"))

    assert code == 0
    assert calls["deleted"] == 0
    assert reader.exams_calls == [("2026-01-01 00:00:00", None)]
    assert reader.conclusion_calls == ["2026-01-02 00:00:00"]
    transformed_exams = [exam for exam, _, _ in calls["transforms"]]
    assert transformed_exams == [detail_exam, unseen_exam]
    assert len(calls["upserts"]) == 2
    assert calls["reads"][0][1] == "szbf:133->133"
    assert calls["states"][0][2]["status"] == "ok"
    assert db.closed is True


def test_main_dry_run_with_limit_skips_all_writes(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1"), _exam("u2", "e2")])
    calls = _install(monkeypatch, reader=reader, db=db)

    code = cli.main(_base_args("--full-rebuild", "--dry-run", "--limit", "1"))

    assert code == 0
    assert calls["deleted"] == 0
    assert calls["upserts"] == []
    assert calls["states"] == []
    assert len(calls["transforms"]) == 1
    assert reader.exams_calls == [(None, 1)]
    assert db.closed is True


def test_main_failure_rolls_back_and_preserves_previous_watermarks(monkeypatch):
    db = FakeDB()
    reader = FakeReader(error=RuntimeError("boom"))
    calls = _install(
        monkeypatch,
        reader=reader,
        db=db,
        state={
            "last_detail_update": "2026-01-01 00:00:00",
            "last_conclusion_update": "2026-01-02 00:00:00",
        },
    )

    code = cli.main(_base_args("--incremental"))

    assert code == 1
    assert db.rollback_calls == 1
    assert len(calls["upserts"]) == 0
    assert len(calls["states"]) == 1
    _, source_key, kwargs = calls["states"][0]
    assert source_key == "szbf:133->133"
    assert kwargs["status"] == "failed"
    assert kwargs["last_detail_update"] == "2026-01-01 00:00:00"
    assert kwargs["last_conclusion_update"] == "2026-01-02 00:00:00"
    assert "boom" in kwargs["message"]
    assert db.closed is True


def test_main_full_rebuild_failure_after_delete_forces_full_refetch(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1")])
    calls = _install(
        monkeypatch,
        reader=reader,
        db=db,
        state={
            "last_detail_update": "2026-01-01 00:00:00",
            "last_conclusion_update": "2026-01-02 00:00:00",
        },
    )

    def failing_upsert(session, target):
        raise RuntimeError("write failed")

    monkeypatch.setattr(cli, "upsert_report", failing_upsert)

    code = cli.main(_base_args("--full-rebuild"))

    assert code == 1
    assert calls["deleted"] == 1
    assert db.rollback_calls == 1
    assert len(calls["states"]) == 1
    _, source_key, kwargs = calls["states"][0]
    assert source_key == "szbf:133->133"
    assert kwargs["status"] == "failed"
    assert kwargs["last_detail_update"] is None
    assert kwargs["last_conclusion_update"] is None
    assert "full refetch required" in kwargs["message"]
    assert "write failed" in kwargs["message"]
    assert db.closed is True


def test_main_full_rebuild_fetch_failure_does_not_delete(monkeypatch):
    db = FakeDB()
    reader = FakeReader(error=RuntimeError("boom"))
    calls = _install(monkeypatch, reader=reader, db=db)

    code = cli.main(_base_args("--full-rebuild"))

    assert code == 1
    assert calls["deleted"] == 0
    assert calls["upserts"] == []
    assert calls["states"][0][2]["status"] == "failed"
    assert db.closed is True


class _Clock:
    current = datetime(2026, 5, 1, 12, 0, 0)

    @classmethod
    def now(cls):
        return cls.current


class _AdvancingReader(FakeReader):
    def fetch_exams(self, changed_since=None, limit=None):
        exams = super().fetch_exams(changed_since, limit)
        _Clock.current += timedelta(minutes=15)
        return exams


def test_main_ok_watermark_uses_fetch_start_not_end(monkeypatch):
    db = FakeDB()
    _Clock.current = datetime(2026, 5, 1, 12, 0, 0)
    reader = _AdvancingReader(exams=[_exam("u1", "e1")])
    calls = _install(monkeypatch, reader=reader, db=db)
    monkeypatch.setattr(cli, "datetime", _Clock)
    monkeypatch.setattr(cli.settings, "SZBF_SYNC_LOOKBACK_MINUTES", 10)

    code = cli.main(_base_args("--full-rebuild"))

    assert code == 0
    kwargs = calls["states"][0][2]
    assert kwargs["status"] == "ok"
    assert kwargs["last_detail_update"] == "2026-05-01 11:50:00"
    assert kwargs["last_conclusion_update"] == "2026-05-01 11:50:00"


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _FakeDiseaseCountDB:
    def __init__(self, mapping, rules):
        self.values = [mapping, rules]

    def execute(self, sql, params=None):
        return _ScalarResult(self.values.pop(0))


def test_preflight_warns_when_disease_seed_missing(caplog):
    with caplog.at_level(logging.WARNING, logger="app.szbf.sync"):
        cli._warn_if_no_disease_seed(_FakeDiseaseCountDB(0, 0))
    assert "sync_central" in caplog.text


def test_preflight_silent_when_disease_seed_present(caplog):
    with caplog.at_level(logging.WARNING, logger="app.szbf.sync"):
        cli._warn_if_no_disease_seed(_FakeDiseaseCountDB(1, 0))
    assert "sync_central" not in caplog.text


def test_main_rejects_limit_without_dry_run(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1")])
    calls = _install(monkeypatch, reader=reader, db=db)

    code = cli.main(_base_args("--limit", "5"))

    assert code == 2
    assert calls["session_names"] == []
    assert calls["upserts"] == []
    assert calls["states"] == []
    assert db.closed is False


def test_main_rejects_negative_limit(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1")])
    calls = _install(monkeypatch, reader=reader, db=db)

    code = cli.main(_base_args("--dry-run", "--limit", "-1"))

    assert code == 2
    assert calls["session_names"] == []
    assert calls["upserts"] == []
    assert calls["states"] == []
    assert db.closed is False


def test_main_tolerates_non_positive_batch_size(monkeypatch):
    db = FakeDB()
    reader = FakeReader(exams=[_exam("u1", "e1"), _exam("u2", "e2")])
    calls = _install(monkeypatch, reader=reader, db=db)
    monkeypatch.setattr(cli.settings, "SZBF_SYNC_BATCH_SIZE", 0)

    code = cli.main(_base_args("--full-rebuild"))

    assert code == 0
    assert len(calls["upserts"]) == 2
    assert calls["states"][0][2]["status"] == "ok"
    assert db.closed is True
