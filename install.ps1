# DetailsBench: install or update on Windows with one command (PowerShell):
#
#   irm https://raw.githubusercontent.com/detailstech93/DetailsBench/master/install.ps1 | iex
#
# or double-click DetailsBench-Setup.bat (the same command), or from a copy of the project:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# What it does (nothing else):
#   1. installs Git and Python 3.12 when they are missing, with winget (Windows' own package manager; it takes
#      them from their official installers). It asks once first; Git's installer may show a UAC prompt.
#   2. downloads the app into %USERPROFILE%\DetailsBench (PCBM_DIR=... for another folder), or updates it there.
#   3. makes its own Python environment (.venv in that folder) and installs the Python libraries in it.
#   4. adds DetailsBench to the Start menu and the desktop, then starts it (PCBM_NO_START=1: do not start it).
# It changes no system setting. Remove everything: delete the folder and the two DetailsBench shortcuts.
#
# Everything is in Install-DetailsBench, so a download cut halfway runs nothing.

function Install-DetailsBench {
    $ErrorActionPreference = "Stop"
    $repo = "https://github.com/detailstech93/DetailsBench.git"
    $dir = if ($env:PCBM_DIR) { $env:PCBM_DIR } else { Join-Path $env:USERPROFILE "DetailsBench" }

    function Say($text) { Write-Host "==> $text" -ForegroundColor Cyan }
    function Fail($text) { Write-Host "DetailsBench install stopped: $text" -ForegroundColor Red; throw $text }
    function Refresh-Path {     # winget installs change PATH for new windows only: read it again for this one
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }
    function Find-Python {     # a real Python 3.10+, never the Microsoft Store stub in WindowsApps
        $candidates = @()
        if (Get-Command py -ErrorAction SilentlyContinue) {
            try { $candidates += (& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch {}
        }
        $candidates += Get-Command python -All -ErrorAction SilentlyContinue |
                       Where-Object { $_.Source -notlike "*\WindowsApps\*" } | ForEach-Object { $_.Source }
        $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                     "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
                       ForEach-Object { $_.FullName }
        foreach ($exe in $candidates) {
            if (-not $exe -or -not (Test-Path $exe)) { continue }
            & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $exe }
        }
        return $null
    }

    # ---- 1. Git and Python --------------------------------------------------------------------------------------
    $needGit = -not (Get-Command git -ErrorAction SilentlyContinue)
    $python = Find-Python
    if ($needGit -or -not $python) {
        $names = @(); if ($needGit) { $names += "Git" }; if (-not $python) { $names += "Python 3.12" }
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail ("missing: " + ($names -join ", ") + ". winget (App Installer) is not on this PC: install " +
                  "them from https://git-scm.com/download/win and https://www.python.org/downloads/windows/, then run this again.")
        }
        $answer = Read-Host ("Missing: " + ($names -join " and ") + ". Install with winget from the official source? [Y/n]")
        if ($answer -and $answer -notmatch '^(y|yes)$') { Fail "nothing was installed (answered no)." }
        $common = @("-e", "--source", "winget", "--accept-package-agreements", "--accept-source-agreements")
        if ($needGit) {
            Say "Installing Git"
            winget install --id Git.Git @common
            Refresh-Path
            if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git did not install (see winget's message above)." }
        }
        if (-not $python) {
            Say "Installing Python 3.12"
            winget install --id Python.Python.3.12 --scope user @common
            Refresh-Path
            $python = Find-Python
            if (-not $python) { Fail "Python did not install (see winget's message above)." }
        }
    }

    # ---- 2. the app ---------------------------------------------------------------------------------------------
    if (Test-Path (Join-Path $dir ".git")) {
        Say "Updating the app in $dir"
        git -C $dir pull -q --ff-only
        if ($LASTEXITCODE -ne 0) { Fail "could not update $dir (changed files in it?). Nothing was removed." }
    } elseif ((Test-Path $dir) -and (Get-ChildItem $dir -Force | Select-Object -First 1)) {
        Fail "$dir exists and is not this app: set PCBM_DIR to another folder."
    } else {
        Say "Downloading the app into $dir"
        git clone -q --depth 1 $repo $dir
        if ($LASTEXITCODE -ne 0) { Fail "could not download $repo." }
    }

    # ---- 3. Python environment ----------------------------------------------------------------------------------
    Say "Installing the Python libraries (in $dir\.venv, nothing system-wide)"
    $venv = Join-Path $dir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venv)) {
        & $python -m venv (Join-Path $dir ".venv")
        if ($LASTEXITCODE -ne 0) { Fail "could not make the Python environment." }
    }
    & $venv -m pip install --upgrade pip
    & $venv -m pip install -r (Join-Path $dir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "installing the Python libraries failed (see above). Run this again to retry." }

    # ---- 4. shortcuts -------------------------------------------------------------------------------------------
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))
    foreach ($place in $places) {
        $link = $shell.CreateShortcut((Join-Path $place "DetailsBench.lnk"))
        $link.TargetPath = Join-Path $dir "run.bat"
        $link.WorkingDirectory = $dir
        $link.Description = "Find, install, run and record PC benchmarks"
        $link.Save()
    }
    Say "Done. Start it from the Start menu or the desktop (DetailsBench)."
    if (-not $env:PCBM_NO_START) {
        Say "Starting DetailsBench"
        Start-Process -FilePath (Join-Path $dir "run.bat") -WorkingDirectory $dir
    }
}

Install-DetailsBench
