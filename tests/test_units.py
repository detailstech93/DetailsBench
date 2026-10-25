"""One spelling per unit (owner, 2026-10-25)."""

import sqlite3

from app.units import canonical, sql


def test_one_spelling_in_python_and_in_a_query():
    assert [canonical(u) for u in ("pts", "fps", "points", "FPS", "MIPS", None)] == \
        ["points", "FPS", "points", "FPS", "MIPS", None]
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE r (unit TEXT)")
    db.executemany("INSERT INTO r VALUES (?)", [("pts",), ("fps",), ("s",), (None,)])
    assert [u for (u,) in db.execute(f"SELECT {sql('unit')} FROM r")] == ["points", "FPS", "s", None]
