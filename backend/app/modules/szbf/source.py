from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.modules.szbf.normalize import parse_date
from app.modules.szbf.transformer import SourceExam

_KEYS_BATCH_SIZE = 200
_USER_BATCH_SIZE = 500

_DETAIL_SQL = """
SELECT d.user_id, d.exam_code, d.exam_date, d.name, d.sex, d.birthday,
       d.item_name, d.item_code, d.result, d.item_unit, d.reference_range,
       d.exception_flag, d.exception_tips, d.dept_name, d.is_combine,
       d.delete_flag, d.update_time
FROM szbf_exam_detail d
JOIN ba_user_basic b ON b.user_id = d.user_id
WHERE b.org_id = :org_id AND d.is_combine = '0' AND d.delete_flag = '0'
"""

_USER_SQL = """
SELECT b.user_id, b.sex, b.birthday, u.real_name, u.user_nick, u.user_name,
       o.org_name
FROM ba_user_basic b
LEFT JOIN ba_user u ON u.user_id = b.user_id
LEFT JOIN ba_organization o ON o.org_id = b.org_id
WHERE b.org_id = :org_id
"""

_CONCLUSION_SQL = """
SELECT c.user_id, c.con_suggest, c.conclusion, c.update_time
FROM szbf_exam_conclusion_record c
JOIN ba_user_basic b ON b.user_id = c.user_id
WHERE b.org_id = :org_id
"""

_LATEST_SQL = """
SELECT DISTINCT d.user_id, d.exam_code, d.exam_date
FROM szbf_exam_detail d
JOIN ba_user_basic b ON b.user_id = d.user_id
WHERE b.org_id = :org_id AND d.is_combine = '0' AND d.delete_flag = '0'
  AND d.user_id IN ({placeholders})
"""


def _chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


class SourceReader:
    def __init__(self, engine, org_id: str):
        self.engine = engine
        self.org_id = str(org_id)

    def _users(self):
        with self.engine.connect() as conn:
            rows = conn.execute(text(_USER_SQL), {"org_id": self.org_id}).mappings().all()
        return {str(r["user_id"]): r for r in rows}

    def _conclusions(self, user_ids: set[str]):
        if not user_ids:
            return {}
        with self.engine.connect() as conn:
            rows = conn.execute(text(_CONCLUSION_SQL), {"org_id": self.org_id}).mappings().all()
        result = {}
        for row in rows:
            uid = str(row["user_id"])
            if uid in user_ids:
                result[uid] = row
        return result

    def _latest_keys(self, user_ids: set[str]) -> set[tuple[str, str]]:
        if not user_ids:
            return set()
        latest: dict[str, tuple[tuple[date, str], str]] = {}
        with self.engine.connect() as conn:
            for batch in _chunked(sorted(user_ids), _USER_BATCH_SIZE):
                params = {"org_id": self.org_id}
                placeholders = []
                for i, uid in enumerate(batch):
                    params[f"u{i}"] = uid
                    placeholders.append(f":u{i}")
                rows = conn.execute(
                    text(_LATEST_SQL.format(placeholders=", ".join(placeholders))),
                    params,
                ).mappings().all()
                for row in rows:
                    uid = str(row["user_id"])
                    code = str(row["exam_code"])
                    order = (parse_date(row["exam_date"]) or date.min, code)
                    if uid not in latest or order > latest[uid][0]:
                        latest[uid] = (order, code)
        return {(uid, code) for uid, (_, code) in latest.items()}

    def _build(self, rows, users, conclusions, latest_keys) -> list[SourceExam]:
        grouped = {}
        for row in rows:
            uid = str(row["user_id"])
            key = (uid, str(row["exam_code"]))
            grouped.setdefault(key, []).append(dict(row))
        exams = []
        for (uid, exam_code), details in grouped.items():
            user = users.get(uid, {})
            conclusion = conclusions.get(uid, {}) if (uid, exam_code) in latest_keys else {}
            exams.append(SourceExam(
                user_id=uid,
                name=user.get("real_name") or details[0].get("name"),
                gender=user.get("sex") or details[0].get("sex"),
                birthday=parse_date(user.get("birthday") or details[0].get("birthday")),
                org_id=self.org_id,
                org_name=user.get("org_name") or "",
                exam_code=exam_code,
                exam_date=parse_date(details[0].get("exam_date")),
                details=details,
                con_suggest=conclusion.get("con_suggest"),
                conclusion=conclusion.get("conclusion"),
            ))
        return exams

    def fetch_exams(self, changed_since: str | None = None, limit: int | None = None):
        if changed_since:
            exams = self.fetch_exams_by_keys(self.fetch_changed_keys(changed_since))
        else:
            with self.engine.connect() as conn:
                rows = conn.execute(text(_DETAIL_SQL), {"org_id": self.org_id}).mappings().all()
            user_ids = {str(r["user_id"]) for r in rows}
            users = self._users()
            conclusions = self._conclusions(user_ids)
            exams = self._build(rows, users, conclusions, self._latest_keys(user_ids))
        return exams[:limit] if limit else exams

    def fetch_exams_by_keys(self, keys):
        unique_keys = []
        seen = set()
        for uid, code in keys:
            key = (str(uid), str(code))
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)
        if not unique_keys:
            return []
        rows = []
        with self.engine.connect() as conn:
            for batch in _chunked(unique_keys, _KEYS_BATCH_SIZE):
                clauses = []
                params = {"org_id": self.org_id}
                for i, (uid, code) in enumerate(batch):
                    clauses.append(f"(d.user_id=:u{i} AND d.exam_code=:e{i})")
                    params[f"u{i}"] = uid
                    params[f"e{i}"] = code
                sql = _DETAIL_SQL + " AND (" + " OR ".join(clauses) + ")"
                rows.extend(conn.execute(text(sql), params).mappings().all())
        user_ids = {uid for uid, _ in unique_keys}
        users = self._users()
        conclusions = self._conclusions(user_ids)
        return self._build(rows, users, conclusions, self._latest_keys(user_ids))

    def fetch_changed_keys(self, changed_since: str):
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT d.user_id, d.exam_code FROM szbf_exam_detail d "
                "JOIN ba_user_basic b ON b.user_id=d.user_id "
                "WHERE b.org_id=:org_id AND d.is_combine='0' "
                "AND d.delete_flag='0' AND d.update_time > :changed"
            ), {"org_id": self.org_id, "changed": changed_since}).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def fetch_changed_conclusion_users(self, changed_since: str):
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT c.user_id FROM szbf_exam_conclusion_record c "
                "JOIN ba_user_basic b ON b.user_id=c.user_id "
                "WHERE b.org_id=:org_id AND c.update_time > :changed"
            ), {"org_id": self.org_id, "changed": changed_since}).fetchall()
        return [str(r[0]) for r in rows]

    def fetch_exam(self, user_id: str, exam_code: str):
        exams = self.fetch_exams_by_keys([(str(user_id), str(exam_code))])
        return exams[0] if exams else None

    def fetch_latest_exam_for_user(self, user_id: str):
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT d.exam_code FROM szbf_exam_detail d "
                "JOIN ba_user_basic b ON b.user_id=d.user_id "
                "WHERE b.org_id=:org_id AND d.user_id=:uid "
                "AND d.is_combine='0' AND d.delete_flag='0' "
                "ORDER BY d.exam_date DESC, d.exam_code DESC LIMIT 1"
            ), {"org_id": self.org_id, "uid": user_id}).first()
        if not row:
            return None
        return self.fetch_exam(user_id, str(row[0]))

    def station_name(self) -> str:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT org_name FROM ba_organization WHERE org_id=:org_id LIMIT 1"
            ), {"org_id": self.org_id}).first()
        return str(row[0]) if row and row[0] else str(self.org_id)

    def fetch_conclusion_only_users(self) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            conclusion_rows = conn.execute(text(
                "SELECT c.user_id FROM szbf_exam_conclusion_record c "
                "JOIN ba_user_basic b ON b.user_id=c.user_id "
                "WHERE b.org_id=:org_id"
            ), {"org_id": self.org_id}).fetchall()
            detail_rows = conn.execute(text(
                "SELECT d.user_id FROM szbf_exam_detail d "
                "JOIN ba_user_basic b ON b.user_id=d.user_id "
                "WHERE b.org_id=:org_id GROUP BY d.user_id"
            ), {"org_id": self.org_id}).fetchall()
        conclusion_users = {str(row[0]) for row in conclusion_rows}
        detail_users = {str(row[0]) for row in detail_rows}
        users = self._users()
        result = []
        for user_id in conclusion_users - detail_users:
            user = users.get(user_id, {})
            name = (
                user.get("real_name")
                or user.get("user_nick")
                or user.get("user_name")
                or ""
            )
            result.append((user_id, str(name)))
        return result
