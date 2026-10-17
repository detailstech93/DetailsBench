"""Tests for app.automation.tool_spec."""

from app.automation.tool_spec import (
    ToolSpec,
    build_command,
    expand_vars,
    resolve_executable,
    validate_spec,
)


def _valid_python_spec():
    return {
        "id": "python_tool",
        "display_name": "Python Tool",
        "executable": {"python": True},
        "args": ["--out", "{output_dir}/out.txt"],
        "timeout_seconds": 30,
        "success_exit_codes": [0, 1],
        "parser": {"type": "last_number", "unit": "ms", "higher_is_better": False},
        "verified": True,
        "license": "MIT",
        "notes": "note",
        "requires_elevation": False,
    }


def _valid_candidates_spec():
    return {
        "id": "cand_tool",
        "display_name": "Cand Tool",
        "executable": {
            "candidates": ["%ProgramFiles(x86)%/tool.exe", "/usr/bin/tool"],
            "path_names": ["tool"],
        },
        "args": [],
        "timeout_seconds": 10,
        "success_exit_codes": [0],
        "parser": {"type": "json", "path": "result.score"},
        "verified": False,
        "license": "",
        "notes": "",
        "requires_elevation": False,
    }


def _make_spec(executable, args=(), parser=None):
    return ToolSpec(
        id="t",
        display_name="T",
        executable=executable,
        args=tuple(args),
        timeout_seconds=1,
        success_exit_codes=(0,),
        parser=parser if parser is not None else {"type": "last_number"},
        verified=True,
        license="",
        notes="",
        requires_elevation=False,
    )


def test_valid_python_spec():
    spec, errors = validate_spec(_valid_python_spec())

    assert errors == []
    assert spec is not None
    assert spec.id == "python_tool"
    assert spec.display_name == "Python Tool"
    assert spec.executable == {"python": True}
    assert spec.args == ("--out", "{output_dir}/out.txt")
    assert spec.timeout_seconds == 30
    assert spec.success_exit_codes == (0, 1)
    assert spec.parser["type"] == "last_number"
    assert spec.verified is True
    assert spec.license == "MIT"
    assert spec.notes == "note"
    assert spec.requires_elevation is False


def test_valid_candidates_spec_with_path_names():
    spec, errors = validate_spec(_valid_candidates_spec())

    assert errors == []
    assert spec is not None
    assert spec.executable == {
        "candidates": ["%ProgramFiles(x86)%/tool.exe", "/usr/bin/tool"],
        "path_names": ["tool"],
    }
    assert spec.args == ()
    assert spec.success_exit_codes == (0,)
    assert spec.parser == {"type": "json", "path": "result.score"}


def test_non_dict_spec():
    spec, errors = validate_spec([])

    assert spec is None
    assert errors == ["spec is not an object"]


def test_bad_id():
    data = _valid_python_spec()
    data["id"] = "Bad ID"

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("id" in error for error in errors)


def test_missing_fields_collects_multiple_errors():
    spec, errors = validate_spec({})

    assert spec is None
    assert len(errors) >= 6
    assert any("id" in error for error in errors)
    assert any("display_name" in error for error in errors)
    assert any("executable" in error for error in errors)
    assert any("timeout_seconds" in error for error in errors)
    assert any("parser" in error for error in errors)
    assert any("verified" in error for error in errors)


def test_both_python_and_candidates_rejected():
    data = _valid_python_spec()
    data["executable"] = {"python": True, "candidates": ["/usr/bin/tool"]}

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("exactly one" in error for error in errors)


def test_path_names_without_candidates_rejected():
    data = _valid_python_spec()
    data["executable"] = {"path_names": ["tool"]}

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("path_names" in error for error in errors)


def test_bool_timeout_rejected():
    data = _valid_python_spec()
    data["timeout_seconds"] = True

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("timeout_seconds" in error for error in errors)


def test_timeout_out_of_range_rejected():
    data_zero = _valid_python_spec()
    data_zero["timeout_seconds"] = 0
    spec_zero, errors_zero = validate_spec(data_zero)
    assert spec_zero is None
    assert any("timeout_seconds" in error for error in errors_zero)

    data_too_big = _valid_python_spec()
    data_too_big["timeout_seconds"] = 86401
    spec_too_big, errors_too_big = validate_spec(data_too_big)
    assert spec_too_big is None
    assert any("timeout_seconds" in error for error in errors_too_big)


def test_empty_success_exit_codes_rejected():
    data = _valid_python_spec()
    data["success_exit_codes"] = []

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("success_exit_codes" in error for error in errors)


def test_regex_parser_without_score_group_rejected():
    data = _valid_python_spec()
    data["parser"] = {"type": "regex", "pattern": r"(?P<value>\d+)"}

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("score" in error for error in errors)


def test_invalid_regex_pattern_rejected():
    data = _valid_python_spec()
    data["parser"] = {"type": "regex", "pattern": "("}

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("pattern" in error for error in errors)


def test_unknown_parser_type_rejected():
    data = _valid_python_spec()
    data["parser"] = {"type": "xml"}

    spec, errors = validate_spec(data)

    assert spec is None
    assert any("parser.type" in error for error in errors)


def test_requires_elevation_true_rejected():
    data = _valid_python_spec()
    data["requires_elevation"] = True

    spec, errors = validate_spec(data)

    assert spec is None
    assert "requires_elevation tools are not supported by the CLI adapter" in errors


def test_unknown_top_level_key_rejected():
    data = _valid_python_spec()
    data["bogus"] = 1

    spec, errors = validate_spec(data)

    assert spec is None
    assert "unknown key: bogus" in errors


def test_expand_vars_programfiles_and_case_insensitive_lookup():
    env = {"PROGRAMFILES(X86)": "C:\\PF86"}
    expanded, error = expand_vars("%ProgramFiles(x86)%\\tool.exe", env)

    assert error is None
    assert expanded == "C:\\PF86\\tool.exe"

    env2 = {"MYVAR": "value"}
    expanded2, error2 = expand_vars("%myvar%", env2)

    assert error2 is None
    assert expanded2 == "value"


def test_expand_vars_missing_variable():
    expanded, error = expand_vars("%MISSING%", {})

    assert expanded is None
    assert error == "missing variable MISSING"


def test_resolve_first_existing_candidate_wins():
    spec = _make_spec({"candidates": ["/a", "/b"]})

    def exists(path):
        return path in {"/a", "/b"}

    path, how = resolve_executable(spec, {}, exists, lambda name: None, "/usr/bin/python3")

    assert path == "/a"
    assert how == "candidate"


def test_resolve_skips_candidate_with_missing_variable():
    spec = _make_spec({"candidates": ["%MISSING%/a", "/b"]})

    def exists(path):
        return path == "/b"

    path, how = resolve_executable(spec, {}, exists, lambda name: None, "/usr/bin/python3")

    assert path == "/b"
    assert how == "candidate"


def test_resolve_path_fallback():
    spec = _make_spec({"candidates": ["/missing"], "path_names": ["tool"]})

    def exists(path):
        return False

    def which(name):
        return "/usr/bin/tool" if name == "tool" else None

    path, how = resolve_executable(spec, {}, exists, which, "/usr/bin/python3")

    assert path == "/usr/bin/tool"
    assert how == "PATH"


def test_resolve_not_found():
    spec = _make_spec({"candidates": ["/missing"], "path_names": ["tool"]})

    def exists(path):
        return False

    def which(name):
        return None

    path, how = resolve_executable(spec, {}, exists, which, "/usr/bin/python3")

    assert path is None
    assert how == "not found"


def test_build_command_replaces_output_dir_and_keeps_other_braces():
    spec = _make_spec({"python": True}, args=["--out", "{output_dir}/result.txt", "{x}"])

    command = build_command(spec, "/bin/tool", "/tmp/out")

    assert command == ["/bin/tool", "--out", "/tmp/out/result.txt", "{x}"]
