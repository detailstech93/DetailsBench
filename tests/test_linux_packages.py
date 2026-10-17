from app.discovery.linux_packages import (
    executables_on_path,
    parse_dpkg_query,
    parse_flatpak_list,
    parse_rpm_qa,
    parse_snap_list,
)


def test_parse_dpkg_query_returns_valid_installed_packages():
    output = (
        "coreutils\t9.4\tinstall ok installed\n"
        "bash\t5.2.15-2ubuntu1\tinstall ok installed\n"
    )

    assert parse_dpkg_query(output) == [
        {
            "name": "coreutils",
            "version": "9.4",
            "location": None,
            "source": "dpkg-query",
        },
        {
            "name": "bash",
            "version": "5.2.15-2ubuntu1",
            "location": None,
            "source": "dpkg-query",
        },
    ]


def test_parse_dpkg_query_skips_deinstall_and_malformed_lines():
    output = (
        "removed\t1.0\tdeinstall ok config-files\n"
        "missing\tversion\n"
        "\n"
        "good\t2.0\tinstall ok installed\n"
    )

    assert parse_dpkg_query(output) == [
        {
            "name": "good",
            "version": "2.0",
            "location": None,
            "source": "dpkg-query",
        }
    ]


def test_parse_rpm_qa_returns_valid_packages():
    output = "coreutils\t9.4-3\nbash\t5.2.15-2\n"

    assert parse_rpm_qa(output) == [
        {
            "name": "coreutils",
            "version": "9.4-3",
            "location": None,
            "source": "rpm",
        },
        {
            "name": "bash",
            "version": "5.2.15-2",
            "location": None,
            "source": "rpm",
        },
    ]


def test_parse_rpm_qa_skips_malformed_lines():
    output = "bad\n\nmissing\t1.0\textra\n"

    assert parse_rpm_qa(output) == []


def test_parse_flatpak_list_skips_header_and_handles_empty_version():
    output = (
        "Application ID\tVersion\n"
        "org.gnome.Calculator\t44.0\n"
        "org.mozilla.firefox\t\n"
    )

    assert parse_flatpak_list(output) == [
        {
            "name": "org.gnome.Calculator",
            "version": "44.0",
            "location": None,
            "source": "flatpak",
        },
        {
            "name": "org.mozilla.firefox",
            "version": None,
            "location": None,
            "source": "flatpak",
        },
    ]


def test_parse_flatpak_list_skips_malformed_lines():
    output = "no-tab\n\norg.foo\t1.0\n"

    assert parse_flatpak_list(output) == [
        {
            "name": "org.foo",
            "version": "1.0",
            "location": None,
            "source": "flatpak",
        }
    ]


def test_parse_snap_list_skips_header_and_returns_valid_packages():
    output = (
        "Name\tVersion\tRev\tTracking\tPublisher\tNotes\n"
        "core20\t20230101\t1234\tlatest/stable\tcanonical\t-\n"
        "firefox\t110.0\t5678\tlatest/stable\tmozilla\t-\n"
    )

    assert parse_snap_list(output) == [
        {
            "name": "core20",
            "version": "20230101",
            "location": None,
            "source": "snap",
        },
        {
            "name": "firefox",
            "version": "110.0",
            "location": None,
            "source": "snap",
        },
    ]


def test_parse_snap_list_skips_malformed_lines():
    output = "Name\tVersion\n\nonlyname\nfirefox\t110.0\n"

    assert parse_snap_list(output) == [
        {
            "name": "firefox",
            "version": "110.0",
            "location": None,
            "source": "snap",
        }
    ]


def test_executables_on_path_uses_which_results():
    def fake_which(name):
        if name == "fio":
            return "/usr/bin/fio"
        if name == "7z":
            return None
        return None

    assert executables_on_path(["fio", "7z"], fake_which) == [
        {
            "name": "fio",
            "version": None,
            "location": "/usr/bin/fio",
            "source": "PATH",
        }
    ]


def test_empty_output_returns_empty_list_for_every_parser():
    assert parse_dpkg_query("") == []
    assert parse_rpm_qa("") == []
    assert parse_flatpak_list("") == []
    assert parse_snap_list("") == []


def test_rpm_empty_version_kept_and_crlf_handled():
    assert parse_rpm_qa("fio\t\r\nbash\t5.2-1\r\n") == [
        {"name": "fio", "version": None, "location": None, "source": "rpm"},
        {"name": "bash", "version": "5.2-1", "location": None, "source": "rpm"},
    ]


def test_snap_list_real_space_separated_output():
    output = ("Name      Version    Rev    Tracking       Publisher   Notes\n"
              "firefox   131.0.2    5091   latest/stable  mozilla**   -\n")
    assert [(r["name"], r["version"]) for r in parse_snap_list(output)] == [("firefox", "131.0.2")]
