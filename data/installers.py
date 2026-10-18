"""How a missing test is installed with one click on the Tests screen (app/install).

    INSTALLERS = {test id: {"winget": package id, "hosts": [hosts the installer may come from]}
                           | {"steam": app id}}

- winget: Microsoft's Windows Package Manager. `winget show` gives the installer URL and its SHA256; the app
  shows them and asks before anything is downloaded, refuses a URL whose host is not the vendor's (listed in
  "hosts"), and winget checks the SHA256 itself before it runs the installer (the installer asks for UAC when
  it needs it). Package ids were checked with `winget show` on 2026-10-18: each installer URL is on the
  vendor's own site.
- steam: Steam's own install dialog (steam://install/<appid>), where the owner sees the size and confirms.
A test with no entry keeps its "Official download page" button.
"""

from typing import Dict

INSTALLERS: Dict[str, dict] = {
    # 7-Zip 26.04: https://www.7-zip.org/a/7z2604-x64.msi
    "sevenzip_benchmark": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"]},
    "sevenzip_fm_benchmark": {"winget": "7zip.7zip", "hosts": ["www.7-zip.org", "7-zip.org"]},
    # DiskSpd 2.3 (Microsoft): https://github.com/microsoft/diskspd/releases/download/v2.3/DiskSpd.ZIP
    "diskspd": {"winget": "Microsoft.DiskSpd", "hosts": ["github.com"], "path_prefix": "/microsoft/diskspd/"},
    # fio 3.43: https://github.com/axboe/fio/releases/download/fio-3.43/fio-3.43-x64.msi
    "fio": {"winget": "fio.fio", "hosts": ["github.com"], "path_prefix": "/axboe/fio/"},
    # PerformanceTest 11.1: https://www.passmark.com/downloads/PerformanceTest_Windows_x86-64.exe
    "passmark_performancetest": {"winget": "PassMark.PerformanceTest", "hosts": ["www.passmark.com"]},
    # 3DMark on Steam (the library scan may find only the free demo)
    "d3dmark": {"steam": 223850},
}
