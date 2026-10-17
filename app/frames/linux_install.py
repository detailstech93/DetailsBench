"""Build the command that installs MangoHud on Linux (Phase 6).

PresentMon is Windows-only; on Linux the frame tool is MangoHud from the distribution's own
package manager. This module only BUILDS the argv (pkexec + package manager); the panel shows
it, asks the user, and runs it so the OS shows its own password prompt. A MangoHud package was
checked to exist only for Debian on x64 and arm64 (VERIFIED_ARCH); other pairs get a note.

Drafted by the local agent, reviewed.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

PACKAGE_MANAGERS = (
    ("apt-get", "debian"),
    ("dnf", "fedora"),
    ("pacman", "arch"),
    ("zypper", "suse"),
)

INSTALL_ARGS = {
    "apt-get": ["install", "-y", "mangohud"],
    "dnf": ["install", "-y", "mangohud"],
    "pacman": ["-S", "--noconfirm", "--needed", "mangohud"],
    "zypper": ["--non-interactive", "install", "mangohud"],
}

FAMILY_IDS = {
    "debian": {"debian", "ubuntu", "linuxmint", "pop", "raspbian", "elementary", "zorin", "kali"},
    "fedora": {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"},
    "arch": {"arch", "manjaro", "endeavouros", "cachyos", "garuda", "steamos"},
    "suse": {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "suse", "sles"},
}

VERIFIED_ARCH = {
    ("debian", "x64"),
    ("debian", "arm64"),
}


@dataclass
class InstallPlan:
    family: Optional[str] = None
    package_manager: Optional[str] = None
    command: List[str] = field(default_factory=list)
    needs_update_first: bool = False
    update_command: List[str] = field(default_factory=list)
    verified: bool = False
    note: Optional[str] = None
    error: Optional[str] = None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    elif not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    return value.strip().lower()


def _clean_arch(value: object) -> str:
    arch = _clean_text(value)
    if not arch:
        return "unknown"
    return arch


def distro_family(os_release: dict) -> Optional[str]:
    if not isinstance(os_release, dict):
        os_release = {}

    distro_id = _clean_text(os_release.get("ID"))
    id_like = _clean_text(os_release.get("ID_LIKE"))
    id_like_words = id_like.split()

    if distro_id:
        for _package_manager, family in PACKAGE_MANAGERS:
            if distro_id in FAMILY_IDS.get(family, set()):
                return family

    for _package_manager, family in PACKAGE_MANAGERS:
        family_ids = FAMILY_IDS.get(family, set())
        for word in id_like_words:
            if word in family_ids or word == family:
                return family

    return None


def _safe_which(which: Callable[[str], Optional[str]], name: str) -> Optional[str]:
    if which is None:
        return None

    try:
        path = which(name)
    except Exception:
        return None

    if path is None:
        return None

    if isinstance(path, (bytes, bytearray)):
        path = path.decode("utf-8", "ignore")
    elif not isinstance(path, str):
        try:
            path = str(path)
        except Exception:
            return None

    path = path.strip()
    return path if path else None


def mangohud_install_plan(
    os_release: dict,
    which: Callable[[str], Optional[str]],
    native_arch: str,
) -> InstallPlan:
    arch = _clean_arch(native_arch)

    existing = _safe_which(which, "mangohud")
    if existing:
        family = distro_family(os_release)
        return InstallPlan(
            family=family,
            note=f"MangoHud already installed: {existing}",
        )

    family = distro_family(os_release)
    if family is None:
        distro_id = _clean_text(os_release.get("ID") if isinstance(os_release, dict) else None)
        if not distro_id:
            distro_id = "unknown"
        return InstallPlan(error=f"unsupported distribution: {distro_id}")

    package_manager = None
    for name, fam in PACKAGE_MANAGERS:
        if fam == family:
            package_manager = name
            break

    if package_manager is None:
        return InstallPlan(
            family=family,
            error=f"package manager for {family} not found",
        )

    package_manager_path = _safe_which(which, package_manager)
    if not package_manager_path:
        return InstallPlan(
            family=family,
            package_manager=package_manager,
            error=f"package manager {package_manager} not found",
        )

    pkexec_path = _safe_which(which, "pkexec")
    if not pkexec_path:
        return InstallPlan(
            family=family,
            package_manager=package_manager,
            error="pkexec not found; install MangoHud with your package manager",
        )

    command = [pkexec_path, package_manager_path] + list(INSTALL_ARGS.get(package_manager, []))
    plan = InstallPlan(
        family=family,
        package_manager=package_manager,
        command=command,
    )

    if package_manager == "apt-get":
        plan.needs_update_first = True
        plan.update_command = [pkexec_path, package_manager_path, "update"]

    verified = (family, arch) in VERIFIED_ARCH
    plan.verified = verified

    if not verified:
        plan.note = f"MangoHud availability for {family} on {arch} is not verified"

    return plan
