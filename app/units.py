"""One spelling per unit everywhere (owner, 2026-10-25): "points", not "pts"; "FPS", not "fps". Results already
saved keep what they were written with; they are read with the one spelling (the database's queries, the library
index), so the same test is never split in two by its unit's spelling."""

from typing import Optional

ALIASES = {"pts": "points", "fps": "FPS"}


def canonical(unit: Optional[str]) -> Optional[str]:
    return ALIASES.get(unit, unit) if isinstance(unit, str) else unit


def sql(column: str) -> str:
    """The same in a query: CASE <column> WHEN 'pts' THEN 'points' ... ELSE <column> END."""
    whens = " ".join(f"WHEN '{a}' THEN '{b}'" for a, b in ALIASES.items())
    return f"CASE {column} {whens} ELSE {column} END"
