"""创建测试用户脚本。

用途：在 hospital_template.platform_user 表插入一个测试账号（bcrypt 哈希密码）。
运行：cd backend && uv run python scripts/create_test_user.py [username] [password] [role] [hospital_id]
默认：username=testuser password=testpass123 role=user hospital_id=NULL
role 取值：user / doctor / admin
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from sqlalchemy import text

from app.config import settings
from app.core.database import get_session
from app.core.security import hash_password

VALID_ROLES = ("user", "doctor", "admin")


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "testuser"
    password = sys.argv[2] if len(sys.argv) > 2 else "testpass123"
    role = sys.argv[3] if len(sys.argv) > 3 else "user"
    hospital_id = sys.argv[4] if len(sys.argv) > 4 else None

    if role not in VALID_ROLES:
        print(f"Invalid role: {role}. Must be one of {VALID_ROLES}")
        return 1

    db = get_session(settings.MYSQL_TEMPLATE_DB)
    try:
        existing = db.execute(
            text("SELECT id FROM platform_user WHERE username = :un"),
            {"un": username},
        ).fetchone()
        if existing:
            print(f"User '{username}' already exists (id={existing[0]}), skip.")
            return 0

        db.execute(
            text(
                "INSERT INTO platform_user (username, password_hash, role, hospital_id) "
                "VALUES (:un, :ph, :r, :hid)"
            ),
            {
                "un": username,
                "ph": hash_password(password),
                "r": role,
                "hid": hospital_id,
            },
        )
        db.commit()

        row = db.execute(
            text("SELECT id, username, role, hospital_id, is_active FROM platform_user WHERE username = :un"),
            {"un": username},
        ).fetchone()
        print("Created test user:")
        print(f"  id          : {row[0]}")
        print(f"  username    : {row[1]}")
        print(f"  role        : {row[2]}")
        print(f"  hospital_id : {row[3]}")
        print(f"  is_active   : {row[4]}")
        print(f"  password    : {password} (plaintext, only shown here)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
