from app.frames.linux_install import (
    InstallPlan,
    distro_family,
    mangohud_install_plan,
)


def make_which(paths):
    def which(name):
        return paths.get(name)

    return which


def test_ubuntu_x64_is_verified_apt_plan():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "ubuntu"}, which, "x64")

    assert isinstance(plan, InstallPlan)
    assert plan.family == "debian"
    assert plan.package_manager == "apt-get"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.needs_update_first is True
    assert plan.update_command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "update",
    ]
    assert plan.verified is True
    assert plan.note is None
    assert plan.error is None


def test_debian_arm64_is_verified_apt_plan():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "debian"}, which, "arm64")

    assert plan.family == "debian"
    assert plan.package_manager == "apt-get"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.needs_update_first is True
    assert plan.update_command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "update",
    ]
    assert plan.verified is True
    assert plan.note is None
    assert plan.error is None


def test_fedora_x64_builds_dnf_plan_with_unverified_note():
    which = make_which(
        {
            "dnf": "/usr/bin/dnf",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "fedora"}, which, "x64")

    assert plan.family == "fedora"
    assert plan.package_manager == "dnf"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/dnf",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.needs_update_first is False
    assert plan.update_command == []
    assert plan.verified is False
    assert plan.note == "MangoHud availability for fedora on x64 is not verified"
    assert plan.error is None


def test_linux_mint_maps_to_debian_family():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan(
        {"ID": "linuxmint", "ID_LIKE": "ubuntu debian"},
        which,
        "x64",
    )

    assert plan.family == "debian"
    assert plan.package_manager == "apt-get"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.verified is True
    assert plan.error is None


def test_arch_builds_pacman_plan():
    which = make_which(
        {
            "pacman": "/usr/bin/pacman",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "arch"}, which, "x64")

    assert plan.family == "arch"
    assert plan.package_manager == "pacman"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/pacman",
        "-S",
        "--noconfirm",
        "--needed",
        "mangohud",
    ]
    assert plan.needs_update_first is False
    assert plan.update_command == []
    assert plan.verified is False
    assert plan.note == "MangoHud availability for arch on x64 is not verified"
    assert plan.error is None


def test_opensuse_tumbleweed_builds_zypper_plan():
    which = make_which(
        {
            "zypper": "/usr/bin/zypper",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan(
        {"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"},
        which,
        "x64",
    )

    assert plan.family == "suse"
    assert plan.package_manager == "zypper"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/zypper",
        "--non-interactive",
        "install",
        "mangohud",
    ]
    assert plan.needs_update_first is False
    assert plan.update_command == []
    assert plan.verified is False
    assert plan.note == "MangoHud availability for suse on x64 is not verified"
    assert plan.error is None


def test_unknown_distro_reports_error():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "unknown"}, which, "x64")

    assert plan.family is None
    assert plan.package_manager is None
    assert plan.command == []
    assert plan.update_command == []
    assert plan.needs_update_first is False
    assert plan.verified is False
    assert plan.note is None
    assert plan.error == "unsupported distribution: unknown"


def test_missing_pkexec_reports_error():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
        }
    )

    plan = mangohud_install_plan({"ID": "ubuntu"}, which, "x64")

    assert plan.family == "debian"
    assert plan.package_manager == "apt-get"
    assert plan.command == []
    assert plan.update_command == []
    assert plan.needs_update_first is False
    assert plan.verified is False
    assert plan.note is None
    assert plan.error == "pkexec not found; install MangoHud with your package manager"


def test_missing_package_manager_reports_error():
    which = make_which(
        {
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "fedora"}, which, "x64")

    assert plan.family == "fedora"
    assert plan.package_manager == "dnf"
    assert plan.command == []
    assert plan.update_command == []
    assert plan.needs_update_first is False
    assert plan.verified is False
    assert plan.note is None
    assert plan.error == "package manager dnf not found"


def test_already_installed_short_circuits_plan():
    which = make_which(
        {
            "mangohud": "/usr/bin/mangohud",
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "ubuntu"}, which, "x64")

    assert plan.family == "debian"
    assert plan.package_manager is None
    assert plan.command == []
    assert plan.update_command == []
    assert plan.needs_update_first is False
    assert plan.verified is False
    assert plan.note == "MangoHud already installed: /usr/bin/mangohud"
    assert plan.error is None


def test_fedora_arm64_has_unverified_note():
    which = make_which(
        {
            "dnf": "/usr/bin/dnf",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "fedora"}, which, "arm64")

    assert plan.family == "fedora"
    assert plan.package_manager == "dnf"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/dnf",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.verified is False
    assert plan.note == "MangoHud availability for fedora on arm64 is not verified"
    assert plan.error is None


def test_x86_arch_still_builds_command_but_is_unverified():
    which = make_which(
        {
            "pacman": "/usr/bin/pacman",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "arch"}, which, "x86")

    assert plan.family == "arch"
    assert plan.package_manager == "pacman"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/pacman",
        "-S",
        "--noconfirm",
        "--needed",
        "mangohud",
    ]
    assert plan.verified is False
    assert plan.note == "MangoHud availability for arch on x86 is not verified"
    assert plan.error is None


def test_unknown_arch_still_builds_command_but_is_unverified():
    which = make_which(
        {
            "apt-get": "/usr/bin/apt-get",
            "pkexec": "/usr/bin/pkexec",
        }
    )

    plan = mangohud_install_plan({"ID": "ubuntu"}, which, "unknown")

    assert plan.family == "debian"
    assert plan.package_manager == "apt-get"
    assert plan.command == [
        "/usr/bin/pkexec",
        "/usr/bin/apt-get",
        "install",
        "-y",
        "mangohud",
    ]
    assert plan.verified is False
    assert plan.note == "MangoHud availability for debian on unknown is not verified"
    assert plan.error is None


def test_distro_family_prefers_id_over_id_like():
    assert distro_family({"ID": "ubuntu", "ID_LIKE": "fedora"}) == "debian"


def test_distro_family_uses_id_like_when_id_is_unknown():
    assert distro_family({"ID": "unknown", "ID_LIKE": "rhel fedora"}) == "fedora"


def test_distro_family_returns_none_when_no_family_matches():
    assert distro_family({"ID": "unknown", "ID_LIKE": "unknown"}) is None
