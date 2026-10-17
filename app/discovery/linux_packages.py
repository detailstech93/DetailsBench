"""Parsers for Linux package-manager output (drafted by the local agent, reviewed).

Pure functions; callers run the commands and pass the output in.
Unverified on real Linux hardware.
"""

from typing import Callable, Dict, Iterable, List, Optional

Record = Dict[str, Optional[str]]


def _make_record(name: str, version: Optional[str], source: str) -> Record:
    return {
        "name": name,
        "version": version,
        "location": None,
        "source": source,
    }


def _as_optional_str(value: str) -> Optional[str]:
    text = value.strip()
    return text if text else None


def parse_dpkg_query(output: str) -> List[Record]:
    results: List[Record] = []

    if not isinstance(output, str):
        return results

    for raw_line in output.splitlines():
        # No strip(): it would drop a trailing empty field ("name<TAB>").
        # splitlines() already removed the line ending.
        line = raw_line
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) != 3:
            continue

        name = parts[0].strip()
        if not name:
            continue

        version = _as_optional_str(parts[1])
        status = parts[2].strip()
        status_words = status.split()

        if not status_words or status_words[-1] != "installed":
            continue

        if "install ok installed" not in status:
            continue

        results.append(_make_record(name, version, "dpkg-query"))

    return results


def parse_rpm_qa(output: str) -> List[Record]:
    results: List[Record] = []

    if not isinstance(output, str):
        return results

    for raw_line in output.splitlines():
        # No strip(): it would drop a trailing empty field ("name<TAB>").
        # splitlines() already removed the line ending.
        line = raw_line
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) != 2:
            continue

        name = parts[0].strip()
        if not name:
            continue

        version = _as_optional_str(parts[1])
        results.append(_make_record(name, version, "rpm"))

    return results


def parse_flatpak_list(output: str) -> List[Record]:
    results: List[Record] = []

    if not isinstance(output, str):
        return results

    first_non_empty = True

    for raw_line in output.splitlines():
        # No strip(): it would drop a trailing empty field ("name<TAB>").
        # splitlines() already removed the line ending.
        line = raw_line
        if not line.strip():
            continue

        parts = line.split("\t", 1)

        if first_non_empty and len(parts) == 2:
            if parts[0].strip() == "Application ID" and parts[1].strip() == "Version":
                first_non_empty = False
                continue

        first_non_empty = False

        if len(parts) != 2:
            continue

        name = parts[0].strip()
        if not name:
            continue

        version = _as_optional_str(parts[1])
        results.append(_make_record(name, version, "flatpak"))

    return results


def parse_snap_list(output: str) -> List[Record]:
    results: List[Record] = []

    if not isinstance(output, str):
        return results

    first_non_empty = True

    for raw_line in output.splitlines():
        # No strip(): it would drop a trailing empty field ("name<TAB>").
        # splitlines() already removed the line ending.
        line = raw_line
        if not line.strip():
            continue

        parts = line.split()

        if first_non_empty and parts and parts[0] == "Name":
            if len(parts) == 1 or parts[1] == "Version":
                first_non_empty = False
                continue

        first_non_empty = False

        if len(parts) < 2:
            continue

        name = parts[0]
        if not name:
            continue

        version = _as_optional_str(parts[1])
        results.append(_make_record(name, version, "snap"))

    return results


def executables_on_path(
    names: Iterable[str],
    which: Callable[[str], Optional[str]],
) -> List[Record]:
    results: List[Record] = []

    if names is None or which is None:
        return results

    try:
        iterator = iter(names)
    except TypeError:
        return results

    for name in iterator:
        if not isinstance(name, str):
            continue

        clean_name = name.strip()
        if not clean_name:
            continue

        try:
            path = which(clean_name)
        except Exception:
            continue

        if path is None:
            continue

        try:
            location = str(path)
        except Exception:
            continue

        if not location:
            continue

        results.append(
            {
                "name": clean_name,
                "version": None,
                "location": location,
                "source": "PATH",
            }
        )

    return results
