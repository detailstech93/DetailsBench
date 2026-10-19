"""How a missing test is installed with one click on the Tests screen (app/install), per OS and architecture.

    INSTALLERS = {test id: {"windows": {"winget": package id, "hosts": [...], "arch": [...]},
                            "linux": {"packages": {distro family: package name}}}}

{"arch": [...], "zip": {...}} under an OS: the vendor's own zip or tar.gz, its URL, size and SHA-256 pinned
(app/install/direct.py); optional "then" names what the same button downloads next (app/ui/install_flow.py).

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
        # with Get-AuthenticodeSignature). Builds exist for Windows x64 and Linux x64 only (the download folder,
        # 2026-10-18; the Linux binary is an x86-64 ELF). app/install/direct.py installs it, then the same button
        # downloads Blender and the scenes with the launcher (app/pro/blender_benchmark.py, "then").
        "windows": {"arch": ["x64"], "zip": {
            "product": "Blender Benchmark Launcher CLI", "version": "3.3.0",
            "url": "https://download.blender.org/release/BlenderBenchmark2.0/launcher/"
                   "benchmark-launcher-cli-3.3.0-windows.zip",
            "hosts": ["download.blender.org"], "size": 7755878,
            "sha256": "3f00bb68915800f14ddad6877083544aa25c56aac252b77b161bf7962ee67dde",
            "folder": "benchmark-launcher-cli-3.3.0", "exe": "benchmark-launcher-cli.exe",
            "then": "blender_content"}},
        "linux": {"arch": ["x64"], "zip": {
            "product": "Blender Benchmark Launcher CLI", "version": "3.3.0",
            "url": "https://download.blender.org/release/BlenderBenchmark2.0/launcher/"
                   "benchmark-launcher-cli-3.3.0-linux.tar.gz",
            "hosts": ["download.blender.org"], "size": 7803498,
            "sha256": "a15e062179e7bf5cd20d58081f3c84cf87d56df3f596ffb0a11383638139324a",
            "folder": "benchmark-launcher-cli-3.3.0", "exe": "benchmark-launcher-cli",
            "then": "blender_content"}},
    },
    "vray_benchmark": {
        # V-Ray 6 Benchmark 6.00.02 (Chaos, 2025-07-02): one program file, no login needed, from Chaos's download
        # page (chaos.com/benchmark-download). Only the "latest" link works without an account (a link to one
        # version asks to sign in), so a newer release is refused by its SHA-256 until it is pinned here. Size
        # and SHA-256 read on 2026-10-18; the Windows file is signed "CHAOS SOFTWARE EOOD" (checked with
        # Get-AuthenticodeSignature), the Linux one is an x86-64 ELF. No ARM build.
        "windows": {"arch": ["x64"], "zip": {
            "product": "V-Ray 6 Benchmark (command line)", "version": "6.00.02",
            "url": "https://download.chaos.com/api/v3/builds/latest/download?product=V-Ray+Benchmark"
                   "&build-type=official&tags=windows-x64-cli",
            "hosts": ["download.chaos.com"], "size": 653056928,
            "sha256": "5b4b1d14a315682f6c5a1c3689a9fc372f06d56dd8e5ad06b4807d1d6ca6468f",
            "folder": "vray-benchmark-6.00.02", "exe": "vray-benchmark-6.00.02-cli.exe", "single": True,
            "note": "Before its first run the app shows Chaos's license agreement and asks you to accept it."}},
        "linux": {"arch": ["x64"], "zip": {
            "product": "V-Ray 6 Benchmark", "version": "6.00.02",
            "url": "https://download.chaos.com/api/v3/builds/latest/download?product=V-Ray+Benchmark"
                   "&build-type=official&tags=linux-x64",
            "hosts": ["download.chaos.com"], "size": 755338045,
            "sha256": "27a3bd029b9af851676740fb607869845ea4ca69e6dcb3e475accd634a95df6d",
            "folder": "vray-benchmark-6.00.02", "exe": "vray-benchmark-6.00.02", "single": True,
            "note": "Before its first run the app shows Chaos's license agreement and asks you to accept it."}},
    },
    "cinebench_2026": {
        # Cinebench 2026 (Maxon, free): the zips linked from maxon.net/en/downloads/cinebench-downloads, read on
        # 2026-10-19. Maxon publishes no SHA-256; its download server sends Content-MD5, pinned here with the size
        # (a newer upload under the same address is refused until it is pinned again). Cinebench.exe is at the
        # zip's root (the zip's own listing, read with range requests: x64 29,410 files / 4.1 GB unpacked,
        # ARM64 29,358 files / 1.8 GB). Windows x64 and ARM64 only: Maxon has no Linux build (macOS is a dmg).
        "windows": {"arch": ["x64", "arm64"], "zip_by_arch": {
            "x64": {
                "product": "Cinebench 2026 (Windows x64)", "version": "2026",
                "url": "https://mx-app-blob-prod.maxon.net/mx-package-production/website/windows/maxon/cinebench/"
                       "Cinebench2026_win_x86_64.zip",
                "hosts": ["mx-app-blob-prod.maxon.net"], "size": 2697130801,
                "md5": "593594ea7dc8385db9772daeb99ab1ad",
                "folder": "cinebench-2026-x64", "exe": "Cinebench.exe",
                "max_files": 30000, "max_unpacked": 4500 * 1024 ** 2,
                "note": "About 4.1 GB once extracted. Cinebench may show Maxon's license agreement on its first "
                        "run; accept it there once."},
            "arm64": {
                "product": "Cinebench 2026 (Windows ARM64)", "version": "2026",
                "url": "https://mx-app-blob-prod.maxon.net/mx-package-production/website/windows/maxon/cinebench/"
                       "Cinebench2026_win_arm64.zip",
                "hosts": ["mx-app-blob-prod.maxon.net"], "size": 783373346,
                "md5": "fa53a16db96f17b03d023b44e80ebe28",
                "folder": "cinebench-2026-arm64", "exe": "Cinebench.exe",
                "max_files": 30000, "max_unpacked": 2000 * 1024 ** 2,
                "note": "About 1.8 GB once extracted. Cinebench may show Maxon's license agreement on its first "
                        "run; accept it there once."},
        }},
    },
    # 3DMark is on Steam (the library scan may find only the free demo); Windows only
    "d3dmark": {"windows": {"steam": 223850}},
}

# the GPU run of Blender Benchmark uses the same launcher
INSTALLERS["blender_benchmark_gpu"] = INSTALLERS["blender_benchmark"]
# the V-Ray GPU runs use the same program
INSTALLERS["vray_benchmark_gpu"] = INSTALLERS["vray_benchmark_rtx"] = INSTALLERS["vray_benchmark"]
# the single-thread run of Cinebench 2026 uses the same program
INSTALLERS["cinebench_2026_single"] = INSTALLERS["cinebench_2026"]
# PassMark's automatic tests (app/pro/passmark.py) use the same program
for _id in ("passmark_cpu", "passmark_2d", "passmark_3d", "passmark_memory"):
    INSTALLERS[_id] = INSTALLERS["passmark_performancetest"]
