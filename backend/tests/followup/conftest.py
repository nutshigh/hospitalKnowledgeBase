"""Test infrastructure for followup tests — 复制 user_profile/conftest 的
BigInteger→INTEGER(SQLite autoincrement)hack。"""
from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):
    return compiler.visit_INTEGER(element, **kw)
