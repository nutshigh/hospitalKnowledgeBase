"""user_identity helper 单测:角色/锚定派生,防单锚定泄露。"""

from app.core.dependencies import user_identity, CurrentUser


def test_user_with_suffix_uses_dual_anchor():
    u = CurrentUser(user_id=1, role="user", hospital_id="H001",
                    id_card_suffix="12345X", name="张三")
    assert user_identity(u) == ("12345X", "张三")


def test_legacy_user_without_suffix_returns_none_none():
    u = CurrentUser(user_id=1, role="user", hospital_id="H001")
    assert user_identity(u) == (None, None)


def test_doctor_uses_platform_user_id():
    u = CurrentUser(user_id=7, role="doctor", hospital_id="H001")
    assert user_identity(u) == ("7", None)


def test_admin_uses_platform_user_id():
    u = CurrentUser(user_id=9, role="admin", hospital_id="H001")
    assert user_identity(u) == ("9", None)
