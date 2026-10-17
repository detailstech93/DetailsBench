import pytest

from app.telemetry.lhm_files import (
    URL_PREFIX,
    WEB_USER,
    LhmAsset,
    ConfigParse,
    select_lhm_asset,
    parse_config,
    render_config,
    with_web_server,
    web_auth,
    web_port,
)


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _asset(name: str, tag: str = "v0.9.6", url: str = None, size: int = 123, digest: str = None) -> dict:
    if url is None:
        url = URL_PREFIX + tag + "/" + name
    if digest is None:
        digest = _digest()
    return {
        "name": name,
        "browser_download_url": url,
        "size": size,
        "digest": digest,
    }


def _release(tag: str = "v0.9.6", assets: list = None, draft: bool = False, prerelease: bool = False) -> dict:
    if assets is None:
        assets = [_asset("LibreHardwareMonitor.zip", tag=tag)]
    return {
        "tag_name": tag,
        "assets": assets,
        "draft": draft,
        "prerelease": prerelease,
    }


def test_newest_stable_release_chosen():
    newer = _release("v0.9.7", [_asset("LibreHardwareMonitor.zip", tag="v0.9.7")])
    older = _release("v0.9.6", [_asset("LibreHardwareMonitor.zip", tag="v0.9.6")])

    result = select_lhm_asset([newer, older])

    assert result.error is None
    assert result.version == "0.9.7"
    assert result.tag == "v0.9.7"
    assert result.name == "LibreHardwareMonitor.zip"
    assert result.url == URL_PREFIX + "v0.9.7/LibreHardwareMonitor.zip"
    assert result.size == 123
    assert result.sha256 == "a" * 64


def test_prerelease_skipped_by_default_and_chosen_when_allowed():
    prerelease = _release(
        "v0.9.8",
        [_asset("LibreHardwareMonitor.zip", tag="v0.9.8")],
        prerelease=True,
    )
    stable = _release("v0.9.7", [_asset("LibreHardwareMonitor.zip", tag="v0.9.7")])

    default_result = select_lhm_asset([prerelease, stable])
    allowed_result = select_lhm_asset([prerelease, stable], allow_prerelease=True)

    assert default_result.version == "0.9.7"
    assert allowed_result.version == "0.9.8"


def test_draft_skipped():
    draft = _release("v0.9.8", [_asset("LibreHardwareMonitor.zip", tag="v0.9.8")], draft=True)
    stable = _release("v0.9.7", [_asset("LibreHardwareMonitor.zip", tag="v0.9.7")])

    result = select_lhm_asset([draft, stable])
    only_draft = select_lhm_asset([draft])

    assert result.version == "0.9.7"
    assert only_draft.error == (
        "no LibreHardwareMonitor release with a .NET Framework zip and a published SHA-256"
    )


def test_preferred_name_order_prefers_plain_zip():
    assets = [
        _asset("LibreHardwareMonitor-net472.zip"),
        _asset("LibreHardwareMonitor.zip"),
    ]
    release = _release("v0.9.6", assets)

    result = select_lhm_asset([release])

    assert result.name == "LibreHardwareMonitor.zip"


def test_only_net472_zip_is_chosen():
    release = _release("v0.9.6", [_asset("LibreHardwareMonitor-net472.zip")])

    result = select_lhm_asset([release])

    assert result.name == "LibreHardwareMonitor-net472.zip"


def test_dotnet10_zip_alone_is_not_chosen():
    release = _release("v0.9.6", [_asset("LibreHardwareMonitor.NET.10.zip")])

    result = select_lhm_asset([release])

    assert result.error == (
        "no LibreHardwareMonitor release with a .NET Framework zip and a published SHA-256"
    )


def test_missing_digest_falls_back_to_older_release():
    bad_asset = _asset("LibreHardwareMonitor.zip", tag="v0.9.7")
    bad_asset["digest"] = None
    good_asset = _asset("LibreHardwareMonitor.zip", tag="v0.9.6")

    result = select_lhm_asset(
        [
            _release("v0.9.7", [bad_asset]),
            _release("v0.9.6", [good_asset]),
        ]
    )

    assert result.version == "0.9.6"


def test_wrong_url_prefix_rejected():
    bad_asset = _asset(
        "LibreHardwareMonitor.zip",
        tag="v0.9.7",
        url="https://example.com/v0.9.7/LibreHardwareMonitor.zip",
    )
    good_asset = _asset("LibreHardwareMonitor.zip", tag="v0.9.6")

    result = select_lhm_asset(
        [
            _release("v0.9.7", [bad_asset]),
            _release("v0.9.6", [good_asset]),
        ]
    )

    assert result.version == "0.9.6"


def test_bool_size_rejected():
    bad_asset = _asset("LibreHardwareMonitor.zip", tag="v0.9.7", size=True)
    good_asset = _asset("LibreHardwareMonitor.zip", tag="v0.9.6")

    result = select_lhm_asset(
        [
            _release("v0.9.7", [bad_asset]),
            _release("v0.9.6", [good_asset]),
        ]
    )

    assert result.version == "0.9.6"


def test_uppercase_digest_is_lowercased():
    asset = _asset("LibreHardwareMonitor.zip", digest="sha256:" + "A" * 64)
    release = _release("v0.9.6", [asset])

    result = select_lhm_asset([release])

    assert result.sha256 == "a" * 64


def test_bad_tag_skipped():
    bad_release = _release("latest", [_asset("LibreHardwareMonitor.zip")])
    good_release = _release("v0.9.6", [_asset("LibreHardwareMonitor.zip")])

    result = select_lhm_asset([bad_release, good_release])

    assert result.version == "0.9.6"


def test_non_list_input_returns_error():
    result = select_lhm_asset({"releases": []})

    assert result.error == "unexpected release list"


EXAMPLE_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="runWebServerMenuItem" value="false" />
    <add key="listenerPort" value="8085" />
  </appSettings>
</configuration>
"""


def test_parse_example_config():
    result = parse_config(EXAMPLE_CONFIG)

    assert result.error is None
    assert result.settings == {
        "runWebServerMenuItem": "false",
        "listenerPort": "8085",
    }
    assert list(result.settings) == ["runWebServerMenuItem", "listenerPort"]


def test_parse_empty_text():
    result = parse_config("   \n\t  ")

    assert result.error is None
    assert result.settings == {}


def test_parse_invalid_xml():
    result = parse_config("<configuration")

    assert result.error == "settings file is not valid XML"
    assert result.settings == {}


def test_parse_wrong_root():
    text = "<root><appSettings><add key='a' value='b'/></appSettings></root>"
    result = parse_config(text)

    assert result.error == "settings file has no <configuration> root"
    assert result.settings == {}


def test_parse_duplicate_key_last_wins():
    text = (
        "<configuration>"
        "<appSettings>"
        '<add key="k" value="first" />'
        '<add key="k" value="second" />'
        "</appSettings>"
        "<appSettings>"
        '<add key="k" value="third" />'
        "</appSettings>"
        "</configuration>"
    )

    result = parse_config(text)

    assert result.error is None
    assert result.settings == {"k": "third"}


def test_render_parse_round_trip_with_special_characters():
    data = {'a<b&c"d': "x<y>&z\"w'\tline1\nline2\r"}

    rendered = render_config(data)
    parsed = parse_config(rendered)

    assert parsed.error is None
    assert parsed.settings == data
    assert "&lt;" in rendered
    assert "&gt;" in rendered
    assert "&amp;" in rendered
    assert "&quot;" in rendered


def test_with_web_server_keeps_order_appends_new_keys_and_does_not_modify_input():
    original = {
        "zeta": "z",
        "listenerPort": "9999",
        "alpha": "a",
    }
    snapshot = dict(original)

    result = with_web_server(original, "secret", port=9000)

    assert original == snapshot
    assert result is not original
    assert list(result) == [
        "zeta",
        "listenerPort",
        "alpha",
        "runWebServerMenuItem",
        "authenticationEnabled",
        "authenticationUserName",
        "authenticationPassword",
        "startMinMenuItem",
        "minTrayMenuItem",
    ]
    assert result["zeta"] == "z"
    assert result["alpha"] == "a"
    assert result["listenerPort"] == "9000"
    assert result["runWebServerMenuItem"] == "true"
    assert result["authenticationEnabled"] == "true"
    assert result["authenticationUserName"] == WEB_USER
    assert result["authenticationPassword"] == "secret"
    assert result["startMinMenuItem"] == "true"
    assert result["minTrayMenuItem"] == "true"


def test_with_web_server_raises_on_empty_password_and_bad_port():
    with pytest.raises(ValueError):
        with_web_server({}, "")

    with pytest.raises(ValueError):
        with_web_server({}, "secret", port=0)

    with pytest.raises(ValueError):
        with_web_server({}, "secret", port=True)


def test_web_auth_enabled_and_disabled():
    enabled = {
        "authenticationEnabled": "true",
        "authenticationUserName": "alice",
        "authenticationPassword": "hunter2",
    }
    disabled = {
        "authenticationEnabled": "false",
        "authenticationUserName": "alice",
        "authenticationPassword": "hunter2",
    }
    missing = {}

    assert web_auth(enabled) == ("alice", "hunter2")
    assert web_auth(disabled) is None
    assert web_auth(missing) is None
    assert web_auth("not-a-dict") is None


def test_web_auth_enabled_with_missing_user_or_password():
    settings = {"authenticationEnabled": "true"}

    assert web_auth(settings) == ("", "")


def test_web_port_valid_and_invalid_values():
    assert web_port({"listenerPort": "8085"}) == 8085
    assert web_port({"listenerPort": "1"}) == 1
    assert web_port({"listenerPort": "65535"}) == 65535

    assert web_port({}) == 8085
    assert web_port({"listenerPort": "abc"}) == 8085
    assert web_port({"listenerPort": "0"}) == 8085
    assert web_port({"listenerPort": "70000"}) == 8085
    assert web_port({"listenerPort": "８０８５"}) == 8085
    assert web_port({"listenerPort": 8085}) == 8085
    assert web_port("not-a-dict") == 8085
