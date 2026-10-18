"""Validation and resolution helpers for benchmark tool specifications (data/tools/*.json).

Drafted by the local agent, reviewed.
"""

import copy
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Tuple

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "id",
        "display_name",
        "executable",
        "args",
        "timeout_seconds",
        "success_exit_codes",
        "parser",
        "verified",
        "license",
        "notes",
        "requires_elevation",
    }
)

_PLACEHOLDER_RE = re.compile(r"%([A-Za-z0-9_()]+)%")


@dataclass(frozen=True)
class ToolSpec:
    id: str
    display_name: str
    executable: Dict
    args: Tuple[str, ...]
    timeout_seconds: int
    success_exit_codes: Tuple[int, ...]
    parser: Dict
    verified: bool
    license: str
    notes: str
    requires_elevation: bool


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_spec(data: object) -> Tuple[Optional[ToolSpec], List[str]]:
    errors: List[str] = []

    if not isinstance(data, dict):
        return (None, ["spec is not an object"])

    for key in data:
        if key not in _ALLOWED_TOP_LEVEL_KEYS:
            errors.append(f"unknown key: {key}")

    # id
    if "id" not in data:
        errors.append("id is required")
    else:
        id_value = data["id"]
        if not isinstance(id_value, str):
            errors.append("id must be a string")
        elif not re.fullmatch(r"[a-z0-9_]{2,40}", id_value):
            errors.append("id must match ^[a-z0-9_]{2,40}$")

    # display_name
    if "display_name" not in data:
        errors.append("display_name is required")
    else:
        display_name = data["display_name"]
        if not isinstance(display_name, str):
            errors.append("display_name must be a string")
        elif display_name == "":
            errors.append("display_name must be non-empty")

    # executable
    executable: Optional[Dict] = None
    if "executable" not in data:
        errors.append("executable is required")
    else:
        executable_value = data["executable"]
        if not isinstance(executable_value, dict):
            errors.append("executable must be an object")
        else:
            executable = executable_value
            has_python = "python" in executable
            has_candidates = "candidates" in executable

            if has_python and has_candidates:
                errors.append("executable must contain exactly one of 'python' or 'candidates'")
            elif not has_python and not has_candidates:
                errors.append("executable must contain exactly one of 'python' or 'candidates'")

            if has_python and executable["python"] is not True:
                errors.append("executable.python must be true")

            if has_candidates:
                candidates = executable["candidates"]
                if not isinstance(candidates, list) or len(candidates) == 0:
                    errors.append("executable.candidates must be a non-empty list of strings")
                else:
                    for item in candidates:
                        if not isinstance(item, str) or item == "":
                            errors.append("executable.candidates must be a non-empty list of strings")
                            break

            if "path_names" in executable:
                if not has_candidates:
                    errors.append("executable.path_names is only allowed with candidates")
                else:
                    path_names = executable["path_names"]
                    if not isinstance(path_names, list):
                        errors.append("executable.path_names must be a list of strings")
                    else:
                        for item in path_names:
                            if not isinstance(item, str):
                                errors.append("executable.path_names must be a list of strings")
                                break

            allowed_executable_keys = {"python", "candidates", "path_names"}
            for key in executable:
                if key not in allowed_executable_keys:
                    errors.append(f"unknown executable key: {key}")

    # args
    args: Tuple[str, ...] = ()
    if "args" in data:
        args_value = data["args"]
        if not isinstance(args_value, list):
            errors.append("args must be a list of strings")
        else:
            for item in args_value:
                if not isinstance(item, str):
                    errors.append("args must be a list of strings")
                    break
            args = tuple(args_value)

    # timeout_seconds
    timeout_seconds: Optional[int] = None
    if "timeout_seconds" not in data:
        errors.append("timeout_seconds is required")
    else:
        timeout_value = data["timeout_seconds"]
        if not _is_int(timeout_value):
            errors.append("timeout_seconds must be an integer")
        elif timeout_value < 1 or timeout_value > 86400:
            errors.append("timeout_seconds must be between 1 and 86400")
        else:
            timeout_seconds = timeout_value

    # success_exit_codes
    success_exit_codes: Tuple[int, ...] = (0,)
    if "success_exit_codes" in data:
        success_value = data["success_exit_codes"]
        if not isinstance(success_value, list) or len(success_value) == 0:
            errors.append("success_exit_codes must be a non-empty list of integers")
        else:
            for item in success_value:
                if not _is_int(item):
                    errors.append("success_exit_codes must be a non-empty list of integers")
                    break
            success_exit_codes = tuple(success_value)

    # parser
    parser: Optional[Dict] = None
    if "parser" not in data:
        errors.append("parser is required")
    else:
        parser_value = data["parser"]
        if not isinstance(parser_value, dict):
            errors.append("parser must be an object")
        else:
            parser = parser_value
            parser_type = parser.get("type")

            if parser_type not in ("regex", "json", "last_number"):
                errors.append("parser.type must be one of 'regex', 'json', 'last_number'")
            else:
                if parser_type == "regex":
                    pattern = parser.get("pattern")
                    if not isinstance(pattern, str):
                        errors.append("parser.pattern must be a string")
                    else:
                        try:
                            compiled = re.compile(pattern)
                        except re.error:
                            errors.append("parser.pattern is not a valid regular expression")
                        else:
                            if "score" not in compiled.groupindex:
                                errors.append("parser.pattern must contain a named group 'score'")

                elif parser_type == "json":
                    path = parser.get("path")
                    if not isinstance(path, str) or path == "":
                        errors.append("parser.path must be a non-empty string")

            if "unit" in parser and not isinstance(parser["unit"], str):
                errors.append("parser.unit must be a string")

            if "higher_is_better" in parser and not isinstance(parser["higher_is_better"], bool):
                errors.append("parser.higher_is_better must be a boolean")

    # verified
    verified: Optional[bool] = None
    if "verified" not in data:
        errors.append("verified is required")
    else:
        verified_value = data["verified"]
        if not isinstance(verified_value, bool):
            errors.append("verified must be a boolean")
        else:
            verified = verified_value

    # license
    license_value = ""
    if "license" in data:
        if not isinstance(data["license"], str):
            errors.append("license must be a string")
        else:
            license_value = data["license"]

    # notes
    notes_value = ""
    if "notes" in data:
        if not isinstance(data["notes"], str):
            errors.append("notes must be a string")
        else:
            notes_value = data["notes"]

    # requires_elevation
    requires_elevation = False
    if "requires_elevation" in data:
        elevation_value = data["requires_elevation"]
        if not isinstance(elevation_value, bool):
            errors.append("requires_elevation must be a boolean")
        else:
            if elevation_value is True:
                errors.append("requires_elevation tools are not supported by the CLI adapter")
            requires_elevation = elevation_value

    if errors:
        return (None, errors)

    spec = ToolSpec(
        id=data["id"],
        display_name=data["display_name"],
        executable=copy.deepcopy(executable),
        args=args,
        timeout_seconds=timeout_seconds,
        success_exit_codes=success_exit_codes,
        parser=copy.deepcopy(parser),
        verified=verified,
        license=license_value,
        notes=notes_value,
        requires_elevation=requires_elevation,
    )
    return (spec, [])


def expand_vars(text: str, env: Mapping) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(text, str):
        return (None, "text must be a string")

    lowered_env: Dict[str, object] = {}
    for key, value in env.items():
        lowered_env[str(key).lower()] = value

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        lowered_name = name.lower()
        if lowered_name not in lowered_env:
            raise ValueError(name)
        return str(lowered_env[lowered_name])

    try:
        expanded = _PLACEHOLDER_RE.sub(_replace, text)
    except ValueError as exc:
        return (None, f"missing variable {exc}")

    return (expanded, None)


def resolve_executable(
    spec: ToolSpec,
    env: Mapping,
    exists: Callable[[str], bool],
    which: Callable[[str], Optional[str]],
    python_executable: str,
) -> Tuple[Optional[str], str]:
    executable = spec.executable

    if executable.get("python") is True:
        if not python_executable:          # the packaged app (PyInstaller) has no separate Python
            return (None, "needs Python, which the packaged app does not include")
        return (python_executable, "python interpreter")

    candidates = executable.get("candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            expanded, error = expand_vars(candidate, env)
            if error is not None:
                continue
            if exists(expanded):
                return (expanded, "candidate")

    path_names = executable.get("path_names", [])
    if isinstance(path_names, list):
        for name in path_names:
            if not isinstance(name, str):
                continue
            resolved = which(name)
            if resolved is not None:
                return (resolved, "PATH")

    return (None, "not found")


def build_command(spec: ToolSpec, executable: str, output_dir: str) -> List[str]:
    command: List[str] = [executable]
    for arg in spec.args:
        command.append(arg.replace("{output_dir}", output_dir))
    return command
