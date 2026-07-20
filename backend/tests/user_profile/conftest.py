"""Test infrastructure for user_profile integration tests.

in-memory SQLite is used to avoid a live MySQL dependency. SQLite only
auto-increments ``INTEGER PRIMARY KEY`` columns; the ORM models declare
``BigInteger`` primary keys (correct for MySQL). Compile ``BigInteger`` as
``INTEGER`` on the SQLite dialect so auto-increment works under tests. This
hook never fires on MySQL, so production DDL is unaffected.
"""
from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):
    return compiler.visit_INTEGER(element, **kw)