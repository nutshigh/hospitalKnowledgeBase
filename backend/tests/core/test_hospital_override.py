"""X-Hospital-Id 覆盖逻辑:doctor/admin 命中激活医院则覆盖;未知回退;user 忽略。"""
import pytest

from app.core.dependencies import get_current_user
from app.core.security import create_access_token


class _Row:
    def __init__(self, hid):
        self.hid = hid

    def fetchone(self):
        return (self.hid,) if self.hid else None


class _FakeDB:
    def __init__(self, active=("H001", "H002")):
        self.active = set(active)
        self.executed_sql = []

    def execute(self, sql, params=None):
        hid = (params or {}).get("hid")
        self.executed_sql.append(str(sql))
        return _Row(hid) if hid in self.active else _Row(None)


def _token(role="doctor", hospital_id="H001"):
    return create_access_token({
        "user_id": 1, "role": role, "hospital_id": hospital_id,
        "id_card_suffix": None, "name": None,
    })


async def _call(role="doctor", jwt_hospital="H001", header=None, db=None):
    return await get_current_user(
        authorization=f"Bearer {_token(role, jwt_hospital)}",
        x_hospital_id=header,
        db=db if db is not None else _FakeDB(),
    )


@pytest.mark.asyncio
async def test_doctor_header_overrides_token_hospital():
    u = await _call(role="doctor", jwt_hospital="H001", header="H002")
    assert u.role == "doctor"
    assert u.hospital_id == "H002"


@pytest.mark.asyncio
async def test_doctor_header_unknown_falls_back_to_token():
    u = await _call(role="doctor", jwt_hospital="H001", header="Z999")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_doctor_header_inactive_hospital_falls_back():
    u = await _call(role="doctor", jwt_hospital="H001", header="H003")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_user_header_ignored():
    u = await _call(role="user", jwt_hospital="H001", header="H002")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_no_header_uses_token_hospital():
    u = await _call(role="doctor", jwt_hospital="H001")
    assert u.hospital_id == "H001"


@pytest.mark.asyncio
async def test_active_lookup_sql_guards_is_active():
    """激活医院查询必须带 is_active = 1,防止漏配导致任意医院可覆盖。"""
    db = _FakeDB()
    u = await _call(role="doctor", jwt_hospital="H001", header="H002", db=db)
    assert u.hospital_id == "H002"
    assert db.executed_sql, "active 医院校验必须真正发 SQL"
    assert "is_active" in db.executed_sql[0]
    assert "= 1" in db.executed_sql[0]
