"""How a missing test is installed with one click on the Tests screen (app/install), per OS and architecture.

    INSTALLERS = {test id: {"windows": {"winget": package id, "hosts": [...], "arch": [...]},
                            "linux": {"packages": {distro family: package name}}}}

Windows: winget (Microsoft's Windows Package Manager). `winget show` gives the installer URL and its SHA256; the app
shows them and asks, refuses a URL whose host is not the vendor's ("hosts", optional "path_prefix"), and winget
checks the SHA256 before it runs the installer. "arch" lists the machine architectures winget has an installer
for, checked with `winget show --architecture <arch>` on 2026-10-18 (fio has no arm64 installer; PassMark none
for x86). Another architecture gets the official download page instead.

Linux: the distribution's own package manager through pkexec (the system asks for the password), the way MangoHud
is installed (app/frames/linux_install.py). Families: debian (apt-get), fedora (dnf), arch (pacman), suse
(zypper). Package names are the distributions' usual ones, NOT checked on a real Linux machine yet: the dialog
says so. A family missing from "packages" gets the official page.

{"steam": app id} under an OS installs through Steam (3DMark). Steam games are not listed here: a missing Steam game opens Steam's own install dialog (steam://install/<appid>)
on Windows and Linux. A test with no entry for this OS keeps its "Official download page" button.
"""

from typing import Dict

INSTALLERS: Dict[str, dict] = {
    "sevenzip_benchmark": {
        # 7-Zip 26.04: https://www.7-zip.org/a/7z2604-x64.msi (arm64: 7z2604-arm64.exe)
        "windows": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"], "arch": ["x64", "arm64", "x86"]},
        # the tool spec runs 7z or 7za: p7zip-full gives 7z (Debian/Ubuntu), p7zip gives 7za (Fedora)
        "linux": {"packages": {"debian": "p7zip-full", "fedora": "p7zip", "arch": "7zip", "suse": "7zip"}},
    },
    "sevenzip_fm_benchmark": {          # 7-Zip's window (7zFM.exe): Windows only
        "windows": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"], "arch": ["x64", "arm64", "x86"]},
    },
    "diskspd": {
        # DiskSpd 2.3 (Microsoft): one zip with amd64, x86 and arm64 builds
        "windows": {"winget": "Microsoft.DiskSpd", "hosts": ["github.com"], "path_prefix": "/microsoft/diskspd/",
                    "arch": ["x64", "arm64", "x86"]},
    },
    "fio": {
        # fio 3.43: https://github.com/axboe/fio/releases/download/fio-3.43/fio-3.43-x64.msi (x64 only)
        "windows": {"winget": "fio.fio", "hosts": ["github.com"], "path_prefix": "/axboe/fio/", "arch": ["x64"]},
        "linux": {"packages": {"debian": "fio", "fedora": "fio", "arch": "fio", "suse": "fio"}},
    },
    "passmark_performancetest": {
        # PerformanceTest 11.1: PerformanceTest_Windows_x86-64.exe / PerformanceTest_Windows_ARM.exe
        "windows": {"winget": "PassMark.PerformanceTest", "hosts": ["www.passmark.com"], "arch": ["x64", "arm64"]},
    },
    "blender_benchmark": {
        # Blender Benchmark Launcher CLI 3.3.0: no winget package; the zip from Blender's download server, size and
        # SHA-256 pinned on 2026-10-18 (benchmark-launcher-cli.exe inside is signed "Blender Foundation", checked
        # with Get-AuthenticodeSignature). Windows x64 build only. app/install/direct.py installs it.
        "windows": {"arch": ["x64"], "zip": {
            "product": "Blender Benchmark Launcher CLI", "version": "3.3.0",
            "url": "https://download.blender.org/release/BlenderBenchmark2.0/launcher/"
                   "benchmark-launcher-cli-3.3.0-windows.zip",
            "hosts": ["download.blender.org"], "size": 7755878,
            "sha256": "3f00bb68915800f14ddad6877083544aa25c56aac252b77b161bf7962ee67dde",
            "folder": "benchmark-launcher-cli-3.3.0", "exe": "benchmark-launcher-cli.exe",
            "note": "Blender itself and the test scenes are downloaded later by the launcher, from Blender's "
                    "servers, when a benchmark is run."}},
    },
    # 3DMark is on Steam (the library scan may find only the free demo); Windows only
    "d3dmark": {"windows": {"steam": 223850}},
}
