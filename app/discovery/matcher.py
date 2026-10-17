"""Keep only installed programs that match a KnownSoftware record.

Input entries are raw installed-program records ({"name", "version",
"location", "source"}); anything that matches no record is dropped here
and never leaves this function (privacy rule).
"""

import re
from typing import Iterable, List, Mapping, Optional, Sequence

from data.known_software import KNOWN_SOFTWARE, KnownSoftware


def _compile(patterns: Sequence[str]) -> Optional["re.Pattern[str]"]:
    # Empty list -> None: an empty regex would match everything
    return re.compile("|".join(patterns), re.IGNORECASE) if patterns else None


def match_software(entries: Iterable[Mapping],
                   known: Sequence[KnownSoftware] = KNOWN_SOFTWARE) -> List[dict]:
    """Return matched entries as dicts: id, display_name, kind, name, version, path, source."""
    rules = [(k, _compile(k.name_patterns), _compile(k.exclusion_patterns)) for k in known]
    matched: List[dict] = []
    seen = set()
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        for record, include, exclude in rules:
            if include is None or not include.search(name):
                continue
            if exclude is not None and exclude.search(name):
                continue
            path = (entry.get("location") or "").strip() or None
            key = (record.id, name.lower(), (path or "").lower())
            if key not in seen:
                seen.add(key)
                version = (entry.get("version") or "").strip() or None
                matched.append({
                    "id": record.id, "display_name": record.display_name, "kind": record.kind,
                    "name": name, "version": version, "path": path,
                    "source": entry.get("source"),
                })
            break
    return matched
