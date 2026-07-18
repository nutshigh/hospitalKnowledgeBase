import pytest
from unittest.mock import MagicMock, call

from app.modules.tenant.schemas import TenantCreateRequest, TenantCreateResponse
from app.modules.tenant.service import create_tenant


def _make_existing_row(*, active=1):
    row = MagicMock()
    row.hospital_id = "H002"
    row.hospital_name = "示例医院"
    row.db_name = "hospital_H002"
    row.is_active = active
    return row


def test_create_idempotent_returns_existing():
    """已在 hospital_tenant 登记 -> 不调 CALL,不 INSERT,直接返回 created=false 与原数据。"""
    db = MagicMock()
    db.execute.return_value = MagicMock(fetchone=lambda: _make_existing_row(active=1))

    req = TenantCreateRequest(hospital_id="H002", hospital_name="示例医院")
    resp = create_tenant(req, db)

    assert isinstance(resp, TenantCreateResponse)
    assert resp.created is False
    assert resp.hospital_id == "H002"
    assert resp.db_name == "hospital_H002"
    assert resp.hospital_name == "示例医院"
    assert resp.is_active == 1
    # 仅 SELECT 一次,不应 CALL 也不应 INSERT/commit
    assert db.execute.call_count == 1
    db.commit.assert_not_called()


def test_create_idempotent_returns_existing_inactive():
    """已登记但 is_active=0 -> 仍返回旧记录,不自动激活。"""
    db = MagicMock()
    db.execute.return_value = MagicMock(fetchone=lambda: _make_existing_row(active=0))

    req = TenantCreateRequest(hospital_id="H002", hospital_name="示例医院")
    resp = create_tenant(req, db)

    assert resp.created is False
    assert resp.is_active == 0
    db.commit.assert_not_called()


def test_create_calls_then_inserts_then_commits():
    """正常流程:SELECT(空) -> CALL -> INSERT -> commit -> created=true。"""
    db = MagicMock()
    # 第一次 execute(SELECT) -> fetchone=None;第二次(CALL) -> 第三次(INSERT) 无 fetchone 调用
    db.execute.side_effect = [
        MagicMock(fetchone=lambda: None),  # SELECT hospital_tenant
        MagicMock(),                         # CALL create_hospital_database
        MagicMock(),                         # INSERT hospital_tenant
    ]

    req = TenantCreateRequest(hospital_id="H002", hospital_name="示例医院")
    resp = create_tenant(req, db)

    assert resp.created is True
    assert resp.hospital_id == "H002"
    assert resp.db_name == "hospital_H002"
    assert resp.hospital_name == "示例医院"
    assert resp.is_active == 1
    assert db.execute.call_count == 3
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_create_insert_failure_warns_and_reraises():
    """CALL 成功但 INSERT 抛错 -> rollback 被调,异常 reraise。"""
    db = MagicMock()
    db.execute.side_effect = [
        MagicMock(fetchone=lambda: None),
        MagicMock(),
        RuntimeError("duplicate key"),  # INSERT 出错
    ]

    req = TenantCreateRequest(hospital_id="H002", hospital_name="示例医院")

    with pytest.raises(RuntimeError, match="duplicate key"):
        create_tenant(req, db)

    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_create_call_failure_reraises_without_rollback_or_commit():
    """CALL 抛错 -> 异常 reraise,不 rollback(CALL 不可回滚)也不 commit。"""
    db = MagicMock()
    db.execute.side_effect = [
        MagicMock(fetchone=lambda: None),
        RuntimeError("proc not found"),
    ]

    req = TenantCreateRequest(hospital_id="H002", hospital_name="示例医院")

    with pytest.raises(RuntimeError, match="proc not found"):
        create_tenant(req, db)

    db.rollback.assert_not_called()
    db.commit.assert_not_called()


# ---- schema 校验 ----

def test_validator_rejects_underscore():
    """hospital_id 含下划线会被拒绝 —— 避免破坏 batch_import_file 的姓名_医院号_用户号约定。"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="H_002", hospital_name="x")


def test_validator_rejects_too_long():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="A" * 17, hospital_name="x")


def test_validator_rejects_too_short():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="A", hospital_name="x")


def test_validator_rejects_empty_or_long_name():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="H002", hospital_name="")
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="H002", hospital_name="x" * 101)


def test_validator_strips_name_whitespace():
    """hospital_name 前后空白 strip 后保留,但纯空白被视为空。"""
    req = TenantCreateRequest(hospital_id="H002", hospital_name="  示例  ")
    assert req.hospital_name == "示例"

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TenantCreateRequest(hospital_id="H002", hospital_name="   ")