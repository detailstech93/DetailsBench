from app.frames.release_select import AssetChoice, parse_digest, select_asset


def _asset(name, size, url, digest):
    return {
        "name": name,
        "size": size,
        "browser_download_url": url,
        "digest": digest,
    }


def _release(tag, assets, draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets,
    }


def test_parse_digest_accepts_lowercase_sha256():
    digest = "sha256:" + "a" * 64
    assert parse_digest(digest) == "a" * 64


def test_parse_digest_accepts_uppercase_prefix_and_hex():
    digest = "SHA256:" + "A" * 64
    assert parse_digest(digest) == "a" * 64


def test_parse_digest_rejects_bad_values():
    assert parse_digest(None) is None
    assert parse_digest(123) is None
    assert parse_digest("") is None
    assert parse_digest("sha1:" + "a" * 64) is None
    assert parse_digest("sha256:" + "a" * 63) is None
    assert parse_digest("sha256:" + "a" * 65) is None
    assert parse_digest("sha256:" + "g" * 64) is None
    assert parse_digest("sha256:" + "a" * 64 + "\n") is None


def test_select_x64_real_release():
    url = "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe"
    digest = "sha256:b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
    release = _release(
        "v2.6.0",
        [
            _asset("PresentMon-2.6.0-x64.exe", 980320, url, digest),
            _asset("PresentMon-2.6.0.msi", 1000, "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0.msi", None),
            _asset("ReleaseSymbols.zip", 1000, "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/ReleaseSymbols.zip", None),
        ],
    )

    choice = select_asset([release], "x64")

    assert choice.version == "2.6.0"
    assert choice.tag == "v2.6.0"
    assert choice.name == "PresentMon-2.6.0-x64.exe"
    assert choice.url == url
    assert choice.size == 980320
    assert choice.sha256 == "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
    assert choice.asset_arch == "x64"
    assert choice.emulated is False
    assert choice.error is None


def test_select_arm64_real_release_is_emulated():
    url = "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe"
    digest = "sha256:b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
    release = _release(
        "v2.6.0",
        [
            _asset("PresentMon-2.6.0-x64.exe", 980320, url, digest),
            _asset("PresentMon-2.6.0.msi", 1000, "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0.msi", None),
            _asset("ReleaseSymbols.zip", 1000, "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/ReleaseSymbols.zip", None),
        ],
    )

    choice = select_asset([release], "arm64")

    assert choice.version == "2.6.0"
    assert choice.asset_arch == "x64"
    assert choice.emulated is True
    assert choice.error is None


def test_x86_ignores_newer_x64_only_release():
    newer = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
    )
    older = _release(
        "v2.4.1",
        [
            _asset(
                "PresentMon-2.4.1-x64.exe",
                900000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.4.1/PresentMon-2.4.1-x64.exe",
                "sha256:" + "b" * 64,
            ),
            _asset(
                "PresentMon-2.4.1-x86.exe",
                800000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.4.1/PresentMon-2.4.1-x86.exe",
                "sha256:" + "c" * 64,
            ),
        ],
    )

    choice = select_asset([newer, older], "x86")

    assert choice.version == "2.4.1"
    assert choice.asset_arch == "x86"
    assert choice.emulated is False
    assert choice.error is None


def test_arm64_prefers_native_asset_in_same_release():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            ),
            _asset(
                "PresentMon-2.6.0-arm64.exe",
                970000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-arm64.exe",
                "sha256:" + "b" * 64,
            ),
        ],
    )

    choice = select_asset([release], "arm64")

    assert choice.asset_arch == "arm64"
    assert choice.emulated is False
    assert choice.error is None


def test_arm64_newer_x64_beats_older_arm64():
    newer = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
    )
    older = _release(
        "v2.4.1",
        [
            _asset(
                "PresentMon-2.4.1-arm64.exe",
                900000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.4.1/PresentMon-2.4.1-arm64.exe",
                "sha256:" + "b" * 64,
            )
        ],
    )

    choice = select_asset([newer, older], "arm64")

    assert choice.version == "2.6.0"
    assert choice.asset_arch == "x64"
    assert choice.emulated is True
    assert choice.error is None


def test_missing_digest_reports_checksum_error():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                None,
            )
        ],
    )

    choice = select_asset([release], "x64")

    assert choice.error == "no published checksum for PresentMon-2.6.0-x64.exe"
    assert choice.version is None
    assert choice.tag is None
    assert choice.name is None
    assert choice.url is None
    assert choice.size is None
    assert choice.sha256 is None
    assert choice.asset_arch is None
    assert choice.emulated is False


def test_invalid_digest_reports_checksum_error():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:not-hex",
            )
        ],
    )

    choice = select_asset([release], "x64")

    assert choice.error == "no published checksum for PresentMon-2.6.0-x64.exe"


def test_prerelease_skipped_unless_allowed():
    prerelease = _release(
        "v2.7.0",
        [
            _asset(
                "PresentMon-2.7.0-x64.exe",
                1000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.7.0/PresentMon-2.7.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
        prerelease=True,
    )
    stable = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "b" * 64,
            )
        ],
    )

    skipped = select_asset([prerelease, stable], "x64")
    allowed = select_asset([prerelease, stable], "x64", allow_prerelease=True)

    assert skipped.version == "2.6.0"
    assert allowed.version == "2.7.0"


def test_draft_always_skipped():
    draft = _release(
        "v2.7.0",
        [
            _asset(
                "PresentMon-2.7.0-x64.exe",
                1000,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.7.0/PresentMon-2.7.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
        draft=True,
    )
    stable = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "b" * 64,
            )
        ],
    )

    choice = select_asset([draft, stable], "x64", allow_prerelease=True)

    assert choice.version == "2.6.0"
    assert choice.error is None


def test_wrong_url_host_is_rejected():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://example.com/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
    )

    choice = select_asset([release], "x64")

    assert choice.error == "no PresentMon release for x64"


def test_unknown_arch_error():
    choice = select_asset([], "riscv")

    assert choice.error == "no PresentMon build for architecture riscv"
    assert choice.version is None
    assert choice.tag is None
    assert choice.name is None
    assert choice.url is None
    assert choice.size is None
    assert choice.sha256 is None
    assert choice.asset_arch is None
    assert choice.emulated is False


def test_releases_not_list_error():
    choice = select_asset("not-a-list", "x64")

    assert choice.error == "unexpected release data"


def test_invalid_size_is_rejected():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                0,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
    )

    choice = select_asset([release], "x64")

    assert choice.error == "no PresentMon release for x64"


def test_non_dict_release_is_skipped():
    release = _release(
        "v2.6.0",
        [
            _asset(
                "PresentMon-2.6.0-x64.exe",
                980320,
                "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe",
                "sha256:" + "a" * 64,
            )
        ],
    )

    choice = select_asset([None, "bad", release], "x64")

    assert choice.version == "2.6.0"
    assert choice.error is None


def test_error_choice_keeps_defaults():
    choice = select_asset([], "x64")

    assert choice == AssetChoice(error="no PresentMon release for x64")
