"""Benchmark management system for DetailsBench.

Detection is data-driven: every benchmark suite is described by a
BenchmarkDefinition record in data/benchmark_definitions.py.  A single
generic discovery routine reads those definitions — no install paths,
executable names, or version numbers are hardcoded in this module.
"""

import ntpath
import os
import re
import csv
import io
import json
import subprocess
import logging
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass
from enum import Enum
import tempfile
try:
    import winreg
    HAS_REGISTRY = True
except ImportError:                     # Linux: no registry; every registry read finds nothing
    class _NoRegistry:
        HKEY_LOCAL_MACHINE = HKEY_CURRENT_USER = KEY_READ = KEY_WOW64_64KEY = KEY_WOW64_32KEY = 0

        @staticmethod
        def OpenKey(*args, **kwargs):
            raise OSError("no Windows registry on this system")

    winreg = _NoRegistry()
    HAS_REGISTRY = False
import time
from datetime import datetime
import pefile

from app.environment.arch import CANONICAL_ARCHES, normalize_arch
from data.benchmark_definitions import (
    BENCHMARK_DEFINITIONS,
    DEFINITIONS_BY_ID,
    BenchmarkDefinition,
    _derive_official_url,
    get_default_page_version,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class BenchmarkStatus(Enum):
    """Benchmark readiness status."""
    READY_AUTO = "ready_auto"
    READY_MANUAL = "ready_manual"
    NOT_INSTALLED = "not_installed"
    MISSING_PREREQS = "missing_prereqs"
    UNSUPPORTED = "unsupported"
    LICENSE_REQUIRED = "license_required"
    COMPATIBILITY_UNVERIFIED = "compatibility_unverified"
    SCAN_ERROR = "scan_error"


class BenchmarkType(Enum):
    """Benchmark type classification."""
    CPU = "cpu"
    GPU = "gpu"
    MEMORY = "memory"
    WEB = "web"
    SYNTHETIC = "synthetic"


# ──────────────────────────────────────────────────────────────────────────────
# BenchmarkInfo — enriched with detection metadata
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkInfo:
    """Information about a detected benchmark."""
    name: str
    display_name: str
    type: BenchmarkType
    status: BenchmarkStatus
    executable_path: Optional[str] = None
    version: Optional[str] = None
    architecture: Optional[str] = None
    reason: Optional[str] = None
    official_url: Optional[str] = None
    prerequisites: Optional[List[str]] = None
    detection_method: Optional[str] = None
    appx_package: Optional[dict] = None
    additional_installs: Optional[List[dict]] = None
    file_exists: Optional[bool] = True  # None for web benchmarks (not applicable)
    scan_diagnostics: Optional[List[str]] = None  # per-scan channel failure notes
    compatibility: Optional[dict] = None  # CompatibilityResult.to_dict() when a profile was given

    def is_runnable(self) -> bool:
        return self.status in (BenchmarkStatus.READY_AUTO, BenchmarkStatus.READY_MANUAL)

    def is_automatable(self) -> bool:
        return self.status == BenchmarkStatus.READY_AUTO


# ──────────────────────────────────────────────────────────────────────────────
# Version helpers
# ──────────────────────────────────────────────────────────────────────────────

def normalize_version(version_str: Optional[str]) -> Optional[str]:
    """Normalize version string (commas→dots, strip, rejoin)."""
    if version_str is None:
        return None
    version_str = version_str.strip()
    if not version_str or version_str.lower() == "unknown":
        return version_str
    cleaned = version_str.replace(',', '.')
    parts = [p.strip() for p in cleaned.split('.') if p.strip()]
    return '.'.join(parts) if parts else version_str


def parse_version_tuple(v_str: Optional[str]) -> Tuple[int, ...]:
    """Parse version string into tuple of integers for comparison."""
    if not v_str:
        return (0,)
    tokens = []
    for part in re.split(r'[^\d]+', v_str):
        if part.isdigit():
            tokens.append(int(part))
    return tuple(tokens) if tokens else (0,)


# ──────────────────────────────────────────────────────────────────────────────
# PE metadata readers (static — never execute the binary)
# ──────────────────────────────────────────────────────────────────────────────

_ARCH_MAP = {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64", 0x01C0: "arm"}
_CANONICAL_ARCHES = CANONICAL_ARCHES
_normalize_arch = normalize_arch   # shared with app/environment


def get_pe_architecture(exe_path: str) -> str:
    """Determine executable architecture from PE header."""
    if not exe_path or not os.path.isfile(exe_path):
        return "unknown"
    try:
        pe = pefile.PE(exe_path, fast_load=True)
        try:
            machine = pe.FILE_HEADER.Machine
            return _normalize_arch(_ARCH_MAP.get(machine))
        finally:
            pe.close()
    except Exception as e:
        logger.warning(f"Failed to read PE architecture for {exe_path}: {e}")
        return "unknown"


def get_pe_version(exe_path: str) -> Optional[str]:
    """Read version metadata (VersionInfo) from PE file statically."""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    # 1. pefile
    try:
        pe = pefile.PE(exe_path, fast_load=True)
        try:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']
            ])
            if hasattr(pe, 'FileInfo'):
                for file_info_list in pe.FileInfo:
                    for entry in file_info_list:
                        if hasattr(entry, 'StringTable'):
                            for st in entry.StringTable:
                                entries = {
                                    k.decode('utf-8', errors='ignore'):
                                    v.decode('utf-8', errors='ignore')
                                    for k, v in st.entries.items()
                                }
                                for key in ('ProductVersion', 'FileVersion'):
                                    if key in entries and entries[key].strip():
                                        ver = normalize_version(entries[key])
                                        if ver:
                                            return ver
            if hasattr(pe, 'VS_FIXEDFILEINFO'):
                for ffi in pe.VS_FIXEDFILEINFO:
                    file_ver = (
                        f"{ffi.FileVersionMS >> 16}.{ffi.FileVersionMS & 0xFFFF}"
                        f".{ffi.FileVersionLS >> 16}.{ffi.FileVersionLS & 0xFFFF}"
                    )
                    return normalize_version(file_ver)
        finally:
            pe.close()
    except Exception as e:
        logger.debug(f"pefile version extraction failed for {exe_path}: {e}")
    # 2. PowerShell fallback
    try:
        for prop in ('ProductVersion', 'FileVersion'):
            cmd = ['powershell', '-NoProfile', '-Command',
                   f"(Get-ItemProperty -LiteralPath '{exe_path}').VersionInfo.{prop}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return normalize_version(res.stdout.strip())
    except Exception as e:
        logger.debug(f"PowerShell version extraction failed for {exe_path}: {e}")
    return None


def get_pe_product_name(exe_path: str) -> Optional[str]:
    """Read ProductName string from PE VersionInfo."""
    if not exe_path or not os.path.isfile(exe_path):
        return None
    try:
        pe = pefile.PE(exe_path, fast_load=True)
        try:
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']
            ])
            if hasattr(pe, 'FileInfo'):
                for file_info_list in pe.FileInfo:
                    for entry in file_info_list:
                        if hasattr(entry, 'StringTable'):
                            for st in entry.StringTable:
                                for k, v in st.entries.items():
                                    if k.decode('utf-8', errors='ignore') == 'ProductName':
                                        val = v.decode('utf-8', errors='ignore').strip()
                                        if val:
                                            return val
        finally:
            pe.close()
    except Exception as e:
        logger.debug(f"pefile ProductName extraction failed for {exe_path}: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Comparability helpers
# ──────────────────────────────────────────────────────────────────────────────

KNOWN_GEEKBENCH_MAJORS: Set[int] = {4, 5, 6, 7}


def can_compare_geekbench_scores(major_a: int, major_b: int) -> Tuple[bool, str]:
    """Cross-major Geekbench score comparisons are blocked by default."""
    if major_a != major_b:
        return False, (
            f"Score comparison blocked: Geekbench {major_a} and Geekbench {major_b} "
            f"use different scoring baselines and are not comparable."
        )
    return True, "Comparable (same major version)"


def can_compare_benchmarks(bench_name_a: str, bench_name_b: str) -> Tuple[bool, str]:
    """Check if two benchmark results can be compared."""
    a = bench_name_a.lower().strip()
    b = bench_name_b.lower().strip()
    m_a = re.match(r'^geekbench_(\d+)$', a)
    m_b = re.match(r'^geekbench_(\d+)$', b)
    if m_a and m_b:
        return can_compare_geekbench_scores(int(m_a.group(1)), int(m_b.group(1)))
    if a != b:
        return False, f"Cannot compare results from different benchmarks: '{bench_name_a}' vs '{bench_name_b}'"
    return True, "Comparable"


def assert_can_compare_benchmarks(bench_name_a: str, bench_name_b: str) -> None:
    """Validate comparability, raising ValueError if blocked."""
    comparable, reason = can_compare_benchmarks(bench_name_a, bench_name_b)
    if not comparable:
        raise ValueError(reason)


# ──────────────────────────────────────────────────────────────────────────────
# Geekbench-specific validation (called from generic discovery)
# ──────────────────────────────────────────────────────────────────────────────

def is_valid_geekbench_exe(exe_path: str) -> Tuple[bool, Optional[int]]:
    """Verify exe_path is a valid main Geekbench GUI binary.

    Rejects helpers and sibling products (AI, ML).
    Returns (is_valid, major_version).
    """
    if not exe_path or not os.path.isfile(exe_path):
        return False, None
    fname = os.path.basename(exe_path)
    lower_fname = fname.lower()
    # Reject helpers
    if lower_fname in ('geekbench_x86_64.exe', 'geekbench_avx2.exe'):
        return False, None
    if re.match(r'^geekbench\d+\.exe$', lower_fname):
        return False, None
    # Must match "Geekbench <N>.exe"
    m = re.match(r'^Geekbench\s+(\d+)\.exe$', fname, re.IGNORECASE)
    if not m:
        return False, None
    major = int(m.group(1))
    # Check ProductName metadata
    prod_name = get_pe_product_name(exe_path)
    if prod_name:
        if re.search(r'Geekbench\s+(AI|ML)\b', prod_name, re.IGNORECASE):
            return False, None
        pm = re.match(r'^Geekbench\s*(\d+)?$', prod_name, re.IGNORECASE)
        if not pm:
            return False, None
        if pm.group(1):
            major = int(pm.group(1))
    return True, major


# ──────────────────────────────────────────────────────────────────────────────
# Process helpers — snapshot-based confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _get_process_snapshot() -> List[Tuple[str, Optional[str]]]:
    """Take a snapshot of running processes.

    Returns a list of (image_name, executable_path_or_None) tuples.
    image_name is the bare filename (e.g. 'Geekbench 6.exe').
    executable_path is the full path where readable, else None.
    """
    snapshot: List[Tuple[str, Optional[str]]] = []
    try:
        import psutil
        # One pass; exe is None where access is denied (e.g. elevated processes)
        for proc in psutil.process_iter(['name', 'exe']):
            image = proc.info.get('name')
            if image:
                snapshot.append((image, proc.info.get('exe') or None))
    except Exception as e:
        logger.debug(f"Process snapshot error: {e}")
    return snapshot


def _has_new_process(
    before: List[Tuple[str, Optional[str]]],
    after: List[Tuple[str, Optional[str]]],
    target_exe_name: str,
) -> bool:
    """Check whether a NEW process matching target_exe_name appears.

    Compares the full image name (case-insensitive).  Returns True only if
    the count of matching processes strictly increased, meaning a new
    instance was spawned.
    """
    target_lower = target_exe_name.lower()
    before_count = sum(1 for img, _ in before if img.lower() == target_lower)
    after_count = sum(1 for img, _ in after if img.lower() == target_lower)
    return after_count > before_count


def is_process_running(process_name_substr: str) -> bool:
    """Legacy: check if any currently running process name contains the substring.

    Kept for backward compatibility; new code should use snapshot-based
    confirmation via _get_process_snapshot / _has_new_process.
    """
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        if res.returncode == 0 and res.stdout:
            reader = csv.reader(io.StringIO(res.stdout))
            target = process_name_substr.lower()
            for row in reader:
                if row and target in row[0].lower():
                    return True
    except Exception as e:
        logger.debug(f"Process check error: {e}")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Assisted launch — snapshot-based process confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _launch_with_elevation_fallback(
    exe_path: str,
    benchmark_info: BenchmarkInfo,
    target_exe_name: str,
    timeout: int = 5,
) -> Dict[str, Any]:
    """Try a normal launch; if WinError 740, retry via elevation (runas).

    Takes a process snapshot BEFORE launching and confirms a NEW matching
    process appears afterwards (full image-name match, not substring).

    Returns a dict with at least ``status`` and ``message`` keys.
    ``status`` is one of: ``launched``, ``unconfirmed``, ``cancelled``, ``error``.
    """
    elevation_used = False

    # --- Snapshot BEFORE launch ----------------------------------------
    before = _get_process_snapshot()

    # --- Attempt 1: normal launch via subprocess.Popen -----------------
    try:
        subprocess.Popen([exe_path])
    except OSError as exc:
        if exc.winerror == 740:
            logger.info(
                "Normal launch of %s failed (WinError 740); "
                "retrying with elevation (runas)",
                benchmark_info.display_name,
            )
            elevation_used = True
            try:
                os.startfile(exe_path, "runas")
            except OSError as elev_exc:
                if elev_exc.winerror == 1223:
                    return {
                        'status': 'cancelled',
                        'message': f"Launch of {benchmark_info.display_name} cancelled by user (UAC prompt declined)",
                        'elevation_used': elevation_used,
                    }
                return {
                    'status': 'error',
                    'message': f"Failed to launch {benchmark_info.display_name} with elevation: {elev_exc}",
                    'elevation_used': elevation_used,
                }
        else:
            return {
                'status': 'error',
                'message': f"Failed to launch {benchmark_info.display_name}: {exc}",
                'elevation_used': elevation_used,
            }

    # --- Poll for new process (accounts for UAC wait if elevation used) ---
    poll_timeout = 30 if elevation_used else timeout
    start_time = datetime.now()
    while (datetime.now() - start_time).total_seconds() < poll_timeout:
        time.sleep(0.5)
        after = _get_process_snapshot()
        if _has_new_process(before, after, target_exe_name):
            return {
                'status': 'launched',
                'message': f"{benchmark_info.display_name} launched successfully",
                'elevation_used': elevation_used,
            }

    return {
        'status': 'unconfirmed',
        'message': f"{benchmark_info.display_name} launch unconfirmed; new process not seen within {poll_timeout}s",
        'elevation_used': elevation_used,
    }


def launch_assisted_benchmark(
    benchmark_info: BenchmarkInfo, confirm: bool = False, timeout: int = 5
) -> Dict[str, Any]:
    """Launch benchmark GUI with explicit user confirmation.

    Uses snapshot-based process confirmation: takes a before-snapshot,
    launches, then verifies a NEW matching process appears (full image
    name match, not substring).
    """
    if not confirm:
        raise ValueError(
            f"Explicit user confirmation is required to launch {benchmark_info.display_name}."
        )

    name = benchmark_info.name.lower()

    # Cinebench — launch via Start Menu AppID (no exe path, no elevation)
    if name == 'cinebench':
        appx = benchmark_info.appx_package or {}
        app_id = appx.get('app_id') or 'MAXONComputerGmbH.Cinebench_rsne5bsk8s7tj!Cinebench'
        if not re.match(r'^[^!]+![^!]+$', app_id):
            raise RuntimeError(
                f"Cinebench AppID '{app_id}' does not match expected "
                "PackageFamilyName!AppId format; refusing to launch."
            )
        before = _get_process_snapshot()
        try:
            subprocess.Popen(['explorer.exe', f'shell:AppsFolder\\{app_id}'])
        except Exception as e:
            return {'status': 'error', 'message': f"Failed to invoke shell:AppsFolder for Cinebench: {e}"}
        start_time = datetime.now()
        while (datetime.now() - start_time).total_seconds() < timeout:
            time.sleep(0.5)
            after = _get_process_snapshot()
            if _has_new_process(before, after, 'cinebench.exe'):
                return {'status': 'launched', 'message': 'Cinebench launched successfully'}
        return {'status': 'unconfirmed', 'message': 'Cinebench launch unconfirmed; please launch it from the Start menu'}

    # Geekbench — main GUI executable only (uses elevation fallback)
    if name.startswith('geekbench'):
        exe_path = benchmark_info.executable_path
        if not exe_path or not os.path.isfile(exe_path):
            return {'status': 'error', 'message': f"Geekbench executable not found at {exe_path}"}
        exe_name = ntpath.basename(exe_path).lower()     # ntpath: splits on both \ and /
        if not re.match(r'^geekbench\s+\d+\.exe$', exe_name):
            raise RuntimeError(
                f"Safety check failed: Refusing to launch non-main binary '{exe_name}'. "
                "Only main GUI executables ('Geekbench <major>.exe') may be launched."
            )
        return _launch_with_elevation_fallback(
            exe_path, benchmark_info, os.path.basename(exe_path), timeout
        )

    # Generic manual benchmark (uses elevation fallback)
    if benchmark_info.executable_path and os.path.isfile(benchmark_info.executable_path):
        return _launch_with_elevation_fallback(
            benchmark_info.executable_path, benchmark_info,
            os.path.basename(benchmark_info.executable_path), timeout
        )

    return {
        'status': 'manual',
        'message': f"{benchmark_info.display_name} requires manual execution from the browser or Start menu",
    }


# ──────────────────────────────────────────────────────────────────────────────
# BenchmarkManager — data-driven discovery
# ──────────────────────────────────────────────────────────────────────────────

def app_tools_roots() -> List[str]:
    """The app's own tools folder: tools installed by its Install button from a vendor's zip (app/install/direct.py)
    are found there like ones under Program Files."""
    try:
        from app.install.direct import tools_root
        root = str(tools_root())
    except Exception:
        return []
    return [root] if os.path.isdir(root) else []


class BenchmarkManager:
    """Manages benchmark detection, compatibility, and execution.

    All suite-specific knowledge lives in data/benchmark_definitions.py.
    This class reads those definitions and runs a single generic discovery
    routine — no hardcoded install paths, exe names, or version numbers.
    """

    def __init__(self):
        self.benchmarks: List[BenchmarkInfo] = []
        self.browser_info: Dict[str, dict] = {}
        self.system_capabilities: Dict[str, bool] = {}

    # ── System capabilities ──────────────────────────────────────────────────

    def _detect_system_capabilities(self) -> Dict[str, bool]:
        """Detect system capabilities for benchmark compatibility."""
        capabilities: Dict[str, bool] = {
            'wsl_available': False,
            'directx12_supported': False,
            'opengl_supported': True,
            'vulkan_supported': False,
            'hyperthreading_enabled': False,
        }
        try:
            result = subprocess.run(['wsl', '--list'], capture_output=True, text=True, timeout=3)
            capabilities['wsl_available'] = result.returncode == 0
        except Exception:
            pass
        try:
            result = subprocess.run(['vulkaninfo', '--summary'], capture_output=True, text=True, timeout=3)
            capabilities['vulkan_supported'] = result.returncode == 0
        except Exception:
            pass
        try:
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 "(Get-WmiObject -Class Win32_Processor).NumberOfLogicalProcessors -gt "
                 "(Get-WmiObject -Class Win32_Processor).NumberOfCores"],
                capture_output=True, text=True, timeout=5,
            )
            capabilities['hyperthreading_enabled'] = result.returncode == 0 and 'True' in result.stdout
        except Exception:
            pass
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = os.path.join(tmp, 'dxdiag_output.txt')
                subprocess.run(['dxdiag', '/t', report], capture_output=True, text=True, timeout=5)
                if os.path.exists(report):
                    with open(report, 'r', encoding='utf-8', errors='ignore') as f:
                        capabilities['directx12_supported'] = 'Direct3D 12' in f.read()
        except Exception:
            pass
        self.system_capabilities = capabilities
        return capabilities

    # ── System app query (Appx + StartApps) ──────────────────────────────────

    def _query_system_apps(self) -> Dict[str, Any]:
        """Query Appx packages and StartApps in a single PowerShell call."""
        ps_script = r"""
$res = @{}
try {
    $cb = Get-AppxPackage | Where-Object { $_.Name -like '*Cinebench*' } |
          Select-Object Name, Version, @{Name='Architecture';Expression={$_.Architecture.ToString()}}, PackageFamilyName
    $res['appx_cinebench'] = if ($cb) { @($cb)[0] } else { $null }
} catch { $res['appx_cinebench'] = $null }
try {
    $sa = Get-StartApps | Where-Object {
        $_.Name -like '*Cinebench*' -or $_.Name -like '*Geekbench*' -or
        $_.Name -like '*3DMark*' -or $_.Name -like '*glmark*'
    } | Select-Object Name, AppID
    $res['start_apps'] = if ($sa) { @($sa) } else { @() }
} catch { $res['start_apps'] = @() }
$res | ConvertTo-Json -Depth 3
"""
        try:
            proc = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_script],
                capture_output=True, text=True, timeout=12,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                # PowerShell 5.1 ConvertTo-Json unwraps a one-element array
                # into a bare object (and an empty one into null).
                start_apps = data.get('start_apps')
                if isinstance(start_apps, dict):
                    data['start_apps'] = [start_apps]
                elif not isinstance(start_apps, list):
                    data['start_apps'] = []
                return data
        except FileNotFoundError:           # no PowerShell (Linux): no Store apps there, nothing to warn about
            pass
        except Exception as e:
            logger.warning(f"Combined PowerShell query failed: {e}")
        return {'appx_cinebench': None, 'start_apps': []}

    # ── Generic registry search ──────────────────────────────────────────────

    def _search_registry_uninstall(
        self, definition: BenchmarkDefinition
    ) -> Tuple[List[str], set, bool]:
        """Search Uninstall keys for display names matching the definition.

        Returns (install_location_hints, matched_display_names, found_any).
        """
        hints: list = []
        matched_names: set = set()          # must be a set — .add() is called below
        found_any = False
        # Guard: empty pattern list => regex '' matches everything => skip
        exclusion_re = re.compile(
            '|'.join(definition.exclusion_patterns), re.IGNORECASE
        ) if definition.exclusion_patterns else None
        name_re = re.compile(
            '|'.join(definition.name_patterns), re.IGNORECASE
        ) if definition.name_patterns else None

        roots = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
             winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
             winreg.KEY_WOW64_32KEY),
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
             0),
        ]
        for hkey, subkey, flag in roots:
            try:
                with winreg.OpenKey(hkey, subkey, 0, winreg.KEY_READ | flag) as k:
                    sub_count, _, _ = winreg.QueryInfoKey(k)
                    for i in range(sub_count):
                        try:
                            sname = winreg.EnumKey(k, i)
                            with winreg.OpenKey(k, sname) as sk:
                                try:
                                    disp_val, _ = winreg.QueryValueEx(sk, "DisplayName")
                                except OSError:
                                    disp_val = ""
                                test_str = disp_val or sname
                                # Exclusion check
                                if exclusion_re and exclusion_re.search(test_str):
                                    continue
                                # Match check
                                if name_re and name_re.search(test_str):
                                    found_any = True
                                    matched_names.add(disp_val.strip() if disp_val else sname)
                                    for val_name in ("InstallLocation", "InstallSource"):
                                        try:
                                            loc, _ = winreg.QueryValueEx(sk, val_name)
                                            if loc and os.path.isdir(loc):
                                                norm_loc = os.path.normpath(loc)
                                                if norm_loc not in hints:
                                                    hints.append(norm_loc)
                                        except OSError:
                                            pass
                        except OSError:
                            pass
            except OSError:
                pass
        return hints, matched_names, found_any

    # ── Generic filesystem scan ──────────────────────────────────────────────

    def _scan_filesystem(
        self,
        definition: BenchmarkDefinition,
        extra_roots: List[str],
    ) -> Tuple[List[str], set]:
        """Scan Program Files directories for folders matching the definition.

        Returns (candidate_exe_paths, detection_methods).
        """
        candidates: list = []
        methods: set = set()               # must be a set — .add() is called below
        exclusion_re = re.compile(
            '|'.join(definition.exclusion_patterns), re.IGNORECASE
        ) if definition.exclusion_patterns else None
        name_re = re.compile(
            '|'.join(definition.name_patterns), re.IGNORECASE
        ) if definition.name_patterns else None

        search_roots = []
        for env_name, label in [('ProgramFiles(x86)', 'Program Files (x86)'),
                                ('ProgramFiles', 'Program Files')]:
            val = os.environ.get(env_name)
            if val and os.path.isdir(val):
                search_roots.append(val)
            else:
                logger.debug(
                    "Skipping %s root: %s env var %s",
                    label,
                    "missing" if val is None else "not a directory",
                    env_name,
                )
        search_roots = search_roots + extra_roots + app_tools_roots()

        for base in search_roots:
            if not os.path.isdir(base):
                continue
            try:
                for item in os.listdir(base):
                    if item.lower() == 'windowsapps':
                        continue
                    if exclusion_re and exclusion_re.search(item):
                        continue
                    if name_re and name_re.search(item):
                        folder_path = os.path.join(base, item)
                        if os.path.isdir(folder_path):
                            methods.add("filesystem pattern")
                            try:
                                for f in os.listdir(folder_path):
                                    if f.lower().endswith('.exe'):
                                        full_p = os.path.normpath(os.path.join(folder_path, f))
                                        if full_p not in candidates:
                                            candidates.append(full_p)
                            except OSError:
                                pass
            except OSError:
                pass
        return candidates, methods

    # ── Generic Appx detection ───────────────────────────────────────────────

    def _detect_appx(
        self, definition: BenchmarkDefinition, sys_apps_data: dict
    ) -> Optional[BenchmarkInfo]:
        """Detect an Appx package matching the definition."""
        cb_pkg = sys_apps_data.get('appx_cinebench')
        start_apps = sys_apps_data.get('start_apps', [])
        if not cb_pkg:
            return None
        pkg_name = cb_pkg.get('Name', '')
        if definition.id.lower() not in pkg_name.lower():
            return None
        raw_version = str(cb_pkg.get('Version', 'Unknown'))
        version_str = normalize_version(raw_version)
        arch_str = _normalize_arch(str(cb_pkg.get('Architecture', 'unknown')))
        pkg_family = cb_pkg.get('PackageFamilyName', '')
        # Resolve AppID
        app_id = None
        for sa in start_apps:
            if definition.id.lower() in sa.get('Name', '').lower():
                app_id = sa.get('AppID')
                break
        if not app_id and pkg_family:
            app_id = f"{pkg_family}!{definition.display_name}"
        store_meta = {
            'name': cb_pkg.get('Name'),
            'version': version_str,
            'architecture': arch_str,
            'package_family_name': pkg_family,
            'app_id': app_id,
        }
        methods = ["Appx"]
        if app_id:
            methods.append("Start Menu")
        reason = f"Store package version {version_str}, architecture {arch_str}; launch it from the Start menu"
        return BenchmarkInfo(
            name=definition.id,
            display_name=definition.display_name,
            type=self._category_to_type(definition.category),
            status=BenchmarkStatus.READY_MANUAL,
            executable_path=None,
            version=version_str,
            architecture=arch_str,
            official_url=definition.official_url,
            reason=reason,
            prerequisites=['Windows 10/11'],
            detection_method=', '.join(methods),
            appx_package=store_meta,
            file_exists=True,
        )

    def _is_browser_compatible(self, browser_name: str, version: str, min_version: str = '100') -> bool:
        try:
            m = re.search(r'(\d+)', version)
            if not m:
                return False
            return int(m.group(1)) >= int(min_version)
        except Exception:
            return False

    # ── Category → BenchmarkType mapping ─────────────────────────────────────

    @staticmethod
    def _category_to_type(category: str) -> BenchmarkType:
        mapping = {
            'cpu': BenchmarkType.CPU,
            'gpu': BenchmarkType.GPU,
            'memory': BenchmarkType.MEMORY,
            'web': BenchmarkType.WEB,
            'synthetic': BenchmarkType.SYNTHETIC,
        }
        return mapping.get(category, BenchmarkType.SYNTHETIC)

    # ─────────────────────────────────────────────────────────────────────────
    #  Generic discovery routine — reads BenchmarkDefinition and runs channels
    #  Each channel is isolated: a failure is recorded in diagnostics and the
    #  scan continues with remaining channels.  scan() never raises to the GUI.
    # ─────────────────────────────────────────────────────────────────────────

    def _discover_from_definition(
        self,
        definition: BenchmarkDefinition,
        sys_apps_data: Optional[dict] = None,
    ) -> List[BenchmarkInfo]:
        """Discover installations for a single benchmark definition.

        Returns a list of BenchmarkInfo (one per major version for suites that
        split by major, or one entry total).
        """
        if sys_apps_data is None:
            sys_apps_data = self._query_system_apps()

        channel_errors: list = []   # diagnostics: "channel <name> failed: <error>"

        # ── glmark2 special handling: not verified for native Windows ────────
        if definition.id == 'glmark2':
            return self._discover_glmark2(channel_errors)

        # Web benchmarks — browser-based, no install to find
        if 'browser' in definition.discovery_channels:
            return self._discover_web(definition, channel_errors)

        # Appx-first (Cinebench Store package)
        if 'appx' in definition.discovery_channels:
            try:
                appx_result = self._detect_appx(definition, sys_apps_data)
                if appx_result:
                    return [appx_result]
            except Exception as exc:
                logger.error("channel appx failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel appx failed: {exc}")

        # ── Collect candidates via enabled channels (each isolated) ─────────
        all_candidates: List[Dict[str, Any]] = []  # {'path', 'method'}
        methods_found: set = set()
        registry_hints: list = []

        # 1. Registry
        if 'registry' in definition.discovery_channels:
            try:
                registry_hints, matched_names, found = self._search_registry_uninstall(definition)
                if found:
                    methods_found.add("registry")
                # Scan install locations from registry hints
                for loc in registry_hints:
                    try:
                        for f in os.listdir(loc):
                            if f.lower().endswith('.exe'):
                                full_p = os.path.normpath(os.path.join(loc, f))
                                if full_p not in [c['path'] for c in all_candidates]:
                                    all_candidates.append({'path': full_p, 'method': 'registry'})
                    except OSError:
                        pass
            except Exception as exc:
                logger.error("channel registry failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel registry failed: {exc}")

        # 2. Filesystem
        if 'filesystem' in definition.discovery_channels:
            try:
                fs_candidates, fs_methods = self._scan_filesystem(definition, registry_hints)
                methods_found.update(fs_methods)
                for p in fs_candidates:
                    if p not in [c['path'] for c in all_candidates]:
                        all_candidates.append({'path': p, 'method': 'filesystem pattern'})
            except Exception as exc:
                logger.error("channel filesystem failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel filesystem failed: {exc}")

        # 3. Start Menu
        if 'start_menu' in definition.discovery_channels:
            try:
                sm_dirs = [
                    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
                    os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
                ]
                term_lower = definition.id.lower()
                name_re = re.compile('|'.join(definition.name_patterns), re.IGNORECASE) if definition.name_patterns else None
                for d in sm_dirs:
                    if not os.path.isdir(d):
                        continue
                    for root, _dirs, files in os.walk(d):
                        for f in files:
                            if not f.lower().endswith('.lnk'):
                                continue
                            if name_re and name_re.search(f):
                                methods_found.add("Start Menu")
                                lnk_path = os.path.join(root, f)
                                try:
                                    cmd = [
                                        "powershell", "-NoProfile", "-Command",
                                        f"$sh = New-Object -ComObject WScript.Shell; $sh.CreateShortcut('{lnk_path}').TargetPath"
                                    ]
                                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                                    target = res.stdout.strip()
                                    if target and os.path.isfile(target):
                                        norm_target = os.path.normpath(target)
                                        if norm_target not in [c['path'] for c in all_candidates]:
                                            all_candidates.append({'path': norm_target, 'method': 'Start Menu'})
                                except Exception:
                                    pass
                # Also check Get-StartApps
                for sa in sys_apps_data.get('start_apps', []):
                    sa_name = sa.get('Name', '')
                    if term_lower in sa_name.lower():
                        methods_found.add("Start Menu")
                        appid = sa.get('AppID', '')
                        guid_x86 = '{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}'
                        guid_x64 = '{6D809377-6AF0-444B-8957-A3773F02200E}'
                        pf_x86 = os.environ.get('ProgramFiles(x86)')
                        pf = os.environ.get('ProgramFiles')
                        if pf_x86 is None or pf is None:
                            logger.debug(
                                "Skipping Start Menu GUID resolution for %s: "
                                "ProgramFiles env var(s) missing",
                                definition.id,
                            )
                        else:
                            resolved = appid.replace(guid_x86, pf_x86)
                            resolved = resolved.replace(guid_x64, pf)
                            if os.path.isfile(resolved):
                                norm_res = os.path.normpath(resolved)
                                if norm_res not in [c['path'] for c in all_candidates]:
                                    all_candidates.append({'path': norm_res, 'method': 'Start Menu'})
            except Exception as exc:
                logger.error("channel start_menu failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel start_menu failed: {exc}")

        # 4. App Paths
        if 'app_paths' in definition.discovery_channels and HAS_REGISTRY:
            try:
                term_lower = definition.id.lower()
                for hkey, flag in [
                    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                    (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                    (winreg.HKEY_CURRENT_USER, 0),
                ]:
                    with winreg.OpenKey(hkey, r"Software\Microsoft\Windows\CurrentVersion\App Paths",
                                       0, winreg.KEY_READ | flag) as k:
                        sub_count, _, _ = winreg.QueryInfoKey(k)
                        for i in range(sub_count):
                            sname = winreg.EnumKey(k, i)
                            if term_lower in sname.lower():
                                with winreg.OpenKey(k, sname) as sk:
                                    val, _ = winreg.QueryValueEx(sk, "")
                                    if val and os.path.isfile(val):
                                        norm_val = os.path.normpath(val)
                                        if norm_val not in [c['path'] for c in all_candidates]:
                                            methods_found.add("App Paths")
                                            all_candidates.append({'path': norm_val, 'method': 'App Paths'})
            except Exception as exc:
                logger.error("channel app_paths failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel app_paths failed: {exc}")

        if 'steam' in definition.discovery_channels and definition.steam_apps:
            try:
                from app.simulation.script import find_steam_game
                for app_id, rel in definition.steam_apps:
                    folder = find_steam_game(app_id)
                    if folder and os.path.isfile(os.path.join(folder, rel)):
                        all_candidates.append({'path': os.path.normpath(os.path.join(folder, rel)),
                                               'method': f'Steam library (app {app_id})'})
            except Exception as exc:
                logger.error("channel steam failed for %s: %s", definition.id, exc)
                channel_errors.append(f"channel steam failed: {exc}")

        # ── Filter candidates: verify file still exists, read metadata ────────
        exclusion_re = re.compile(
            '|'.join(definition.exclusion_patterns), re.IGNORECASE
        ) if definition.exclusion_patterns else None

        valid_installs: List[Dict[str, Any]] = []
        for cand in all_candidates:
            p = cand['path']
            if not os.path.isfile(p):
                continue
            fname = os.path.basename(p)
            # Exclusion filter on filename
            if exclusion_re and exclusion_re.search(fname):
                continue
            # Geekbench-specific validation
            if definition.id == 'geekbench':
                is_valid, major = is_valid_geekbench_exe(p)
                if not is_valid:
                    continue
            arch = get_pe_architecture(p)
            ver = get_pe_version(p) or "Unknown"
            valid_installs.append({
                'path': p,
                'version': ver,
                'version_tuple': parse_version_tuple(ver),
                'architecture': arch,
            })

        # ── Build result(s) ──────────────────────────────────────────────────
        if not valid_installs:
            # Not installed
            return [BenchmarkInfo(
                name=definition.id,
                display_name=definition.display_name,
                type=self._category_to_type(definition.category),
                status=BenchmarkStatus.NOT_INSTALLED,
                executable_path=None,
                version=None,
                architecture=None,
                official_url=definition.official_url,
                reason=f"{definition.display_name} not found on this system",
                prerequisites=['Windows 10/11'],
                detection_method='not_found',
                file_exists=False,
                scan_diagnostics=channel_errors if channel_errors else None,
            )]

        # For Geekbench: split by major version
        if definition.id == 'geekbench' and definition.major_version_policy == 'separate_per_major':
            by_major: Dict[int, List[Dict]] = {}
            for inst in valid_installs:
                fname = os.path.basename(inst['path'])
                m = re.match(r'^Geekbench\s+(\d+)\.exe$', fname, re.IGNORECASE)
                major = int(m.group(1)) if m else 0
                by_major.setdefault(major, []).append(inst)

            results: List[BenchmarkInfo] = []
            for major in sorted(by_major):
                installs = sorted(by_major[major], key=lambda x: x['version_tuple'], reverse=True)
                primary = installs[0]
                additional = installs[1:] if len(installs) > 1 else None
                is_known = major in KNOWN_GEEKBENCH_MAJORS
                reason_parts = [
                    "Windows GUI version requires manual execution; "
                    "automation/CLI requires a Pro license; "
                    "not verified for this version; License: not verified"
                ]
                if not is_known:
                    reason_parts.append(
                        "newly seen version: score comparability with other majors unknown"
                    )
                det_methods_str = ", ".join(sorted(methods_found)) if methods_found else "filesystem pattern"
                additional_info = None
                if additional:
                    additional_info = [
                        {'path': i['path'], 'version': i['version'], 'architecture': i['architecture']}
                        for i in additional
                    ]
                results.append(BenchmarkInfo(
                    name=f"geekbench_{major}",
                    display_name=f"Geekbench {major}",
                    type=BenchmarkType.CPU,
                    status=BenchmarkStatus.READY_MANUAL,
                    executable_path=primary['path'],
                    version=primary['version'],
                    architecture=primary['architecture'],
                    reason="; ".join(reason_parts),
                    official_url=definition.official_url,
                    prerequisites=['Windows 10/11'],
                    detection_method=det_methods_str,
                    additional_installs=additional_info,
                    file_exists=os.path.isfile(primary['path']),
                    scan_diagnostics=channel_errors if channel_errors else None,
                ))
            if 6 not in by_major:   # owner, 2026-10-20: Geekbench 6 stays listed (with its Install button)
                results.append(BenchmarkInfo(
                    name="geekbench_6", display_name="Geekbench 6", type=BenchmarkType.CPU,
                    status=BenchmarkStatus.NOT_INSTALLED, reason="Geekbench 6 not found on this system",
                    official_url=definition.official_url, prerequisites=['Windows 10/11'],
                    detection_method='not_found', file_exists=False,
                    scan_diagnostics=channel_errors if channel_errors else None))
            return results

        # Single-series or single-install: report highest version as primary
        valid_installs.sort(key=lambda x: x['version_tuple'], reverse=True)
        primary = valid_installs[0]
        additional = valid_installs[1:] if len(valid_installs) > 1 else None
        det_methods_str = ", ".join(sorted(methods_found)) if methods_found else "filesystem pattern"
        additional_info = None
        if additional:
            additional_info = [
                {'path': i['path'], 'version': i['version'], 'architecture': i['architecture']}
                for i in additional
            ]
        return [BenchmarkInfo(
            name=definition.id,
            display_name=definition.display_name,
            type=self._category_to_type(definition.category),
            status=BenchmarkStatus.READY_MANUAL,
            executable_path=primary['path'],
            version=primary['version'],
            architecture=primary['architecture'],
            reason=f"Detected via {det_methods_str}",
            official_url=definition.official_url,
            prerequisites=['Windows 10/11'],
            detection_method=det_methods_str,
            additional_installs=additional_info,
            file_exists=os.path.isfile(primary['path']),
            scan_diagnostics=channel_errors if channel_errors else None,
        )]

    # ── glmark2: WSL-only detection ─────────────────────────────────────────

    def _discover_glmark2(self, channel_errors: list) -> List[BenchmarkInfo]:
        """glmark2 is not verified for native Windows; only usable via WSL."""
        wsl_present = False
        try:
            result = subprocess.run(
                ['wsl', '--list', '--quiet'],
                capture_output=True, text=True, timeout=5,
            )
            wsl_present = (result.returncode == 0)
        except FileNotFoundError:
            pass  # wsl.exe not found at all
        except subprocess.TimeoutExpired:
            pass  # wsl hung — treat as absent
        except Exception as exc:
            channel_errors.append(f"channel wsl_check failed: {exc}")

        # Unsupported either way: this app cannot launch programs inside WSL,
        # so installing glmark2 in WSL would not make it runnable from here.
        wsl_note = (
            'WSL is present on this system; glmark2 can only be run manually inside a WSL distro.'
            if wsl_present else
            'WSL is not present on this system.'
        )
        return [BenchmarkInfo(
            name='glmark2',
            display_name='glmark2',
            type=BenchmarkType.GPU,
            status=BenchmarkStatus.UNSUPPORTED,
            executable_path=None,
            version=None,
            architecture=None,
            official_url='https://github.com/glmark2/glmark2',
            reason=(
                'glmark2 is not verified for native Windows; '
                'it can only be used through WSL, which this app does not launch. '
                + wsl_note
            ),
            prerequisites=['WSL', 'Linux glmark2 package'],
            detection_method='wsl_check',
            file_exists=None,
            scan_diagnostics=channel_errors if channel_errors else None,
        )]

    # ── Web benchmark discovery (with browser detection via App Paths) ────────

    def _discover_web(self, definition: BenchmarkDefinition,
                      channel_errors: list) -> List[BenchmarkInfo]:
        """Detect a web benchmark by checking browser availability.

        Uses _derive_official_url() so the official_url comes from the
        configured page versions (single source of truth).

        Browser detection is isolated: a crash is recorded in channel_errors
        and the benchmark falls back to NOT_INSTALLED rather than aborting
        the entire scan.
        """
        try:
            browser_info = self._detect_browsers()
        except Exception as exc:
            logger.error("channel browser_detection failed for %s: %s", definition.id, exc)
            channel_errors.append(f"channel browser_detection failed: {exc}")
            page_url = _derive_official_url(definition)
            default_ver = get_default_page_version(definition)
            version_str = default_ver["version"] if default_ver else None
            return [BenchmarkInfo(
                name=definition.id,
                display_name=definition.display_name,
                type=self._category_to_type(definition.category),
                status=BenchmarkStatus.SCAN_ERROR,
                executable_path=None,
                version=version_str,
                architecture=None,
                official_url=page_url,
                reason=f'browser check failed: {exc}',
                prerequisites=['Modern web browser'],
                detection_method='error',
                file_exists=None,
                scan_diagnostics=channel_errors if channel_errors else None,
            )]
        compatible: list = []
        for bname, bdata in browser_info.items():
            if bdata['version'] and self._is_browser_compatible(bname, bdata['version']):
                compatible.append(f"{bname} {bdata['version']}")

        # Derive official_url from page versions (single source of truth)
        page_url = _derive_official_url(definition)

        # Store the default page version string in BenchmarkInfo.version
        default_ver = get_default_page_version(definition)
        version_str = default_ver["version"] if default_ver else None

        if compatible:
            # Build enhanced reason text that notes fallback browsers
            fallback_notes = []
            for bname, bdata in browser_info.items():
                if bdata.get('found_by_fallback'):
                    fallback_notes.append(f"{bname} found by fallback path, unverified")
            reason_parts = [f'Compatible browsers detected: {", ".join(compatible)} (manual execution required)']
            if fallback_notes:
                reason_parts.append('; '.join(fallback_notes))
            final_reason = '; '.join(reason_parts)

            # Merge fallback notes into scan_diagnostics
            diag = list(channel_errors) if channel_errors else []
            diag.extend(fallback_notes)

            return [BenchmarkInfo(
                name=definition.id,
                display_name=definition.display_name,
                type=self._category_to_type(definition.category),
                status=BenchmarkStatus.READY_MANUAL,
                executable_path=None,
                version=version_str,
                architecture=None,
                official_url=page_url,
                reason=final_reason,
                prerequisites=['Modern web browser'],
                detection_method='browser_detection',
                file_exists=None,   # not applicable for web benchmarks
                scan_diagnostics=diag if diag else None,
            )]
        return [BenchmarkInfo(
            name=definition.id,
            display_name=definition.display_name,
            type=self._category_to_type(definition.category),
            status=BenchmarkStatus.NOT_INSTALLED,
            executable_path=None,
            version=version_str,
            architecture=None,
            official_url=page_url,
            reason='No compatible browser found',
            prerequisites=['Modern web browser'],
            detection_method='not_found',
            file_exists=None,   # not applicable for web benchmarks
            scan_diagnostics=channel_errors if channel_errors else None,
        )]

    # ── Browser detection: App Paths registry + fallback folder search ───────

    def _detect_browsers(self) -> Dict[str, dict]:
        """Detect installed web browsers via App Paths registry, then fallback paths.

        Fallback paths are built from os.environ (ProgramFiles, ProgramFiles(x86),
        LOCALAPPDATA) — no literal hardcoded paths.
        Browsers found by fallback are labeled "found by fallback, unverified".
        """
        self.browser_info = {}
        # Browser names to search for in App Paths registry
        browser_keys = {
            'Chrome': ['chrome.exe'],
            'Edge': ['msedge.exe'],
            'Firefox': ['firefox.exe'],
            'Opera': ['opera.exe'],
        }
        # Fallback sub-paths built from os.environ (no literal paths)
        pf = os.environ.get('ProgramFiles', '')
        pf86 = os.environ.get('ProgramFiles(x86)', '')
        localappdata = os.environ.get('LOCALAPPDATA', '')
        fallback_subpaths = {
            'Chrome': [
                r'Google\Chrome\Application\chrome.exe',
            ],
            'Edge': [
                r'Microsoft\Edge\Application\msedge.exe',
            ],
            'Firefox': [
                r'Mozilla Firefox\firefox.exe',
            ],
            'Opera': [
                r'Opera\opera.exe',
                r'Opera\launcher.exe',
            ],
        }

        for browser_name, exe_names in browser_keys.items():
            found = False
            # 1. Try App Paths registry
            for hkey, flag in [
                (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                (winreg.HKEY_CURRENT_USER, 0),
            ]:
                if found:
                    break
                try:
                    with winreg.OpenKey(
                        hkey,
                        r"Software\Microsoft\Windows\CurrentVersion\App Paths",
                        0, winreg.KEY_READ | flag,
                    ) as k:
                        sub_count, _, _ = winreg.QueryInfoKey(k)
                        for i in range(sub_count):
                            try:
                                sname = winreg.EnumKey(k, i)
                            except OSError:
                                continue
                            if any(en.lower() == sname.lower() for en in exe_names):
                                try:
                                    with winreg.OpenKey(k, sname) as sk:
                                        val, _ = winreg.QueryValueEx(sk, "")
                                        if val and os.path.isfile(val):
                                            version = get_pe_version(val) or "Unknown"
                                            arch = get_pe_architecture(val)
                                            self.browser_info[browser_name] = {
                                                'path': os.path.normpath(val),
                                                'version': version,
                                                'architecture': arch,
                                            }
                                            found = True
                                            break
                                except OSError:
                                    pass
                except OSError:
                    pass

            # 2. Fallback: paths built from os.environ
            if not found:
                for sub in fallback_subpaths.get(browser_name, []):
                    for base in (pf86, pf, localappdata):
                        if not base:
                            continue
                        exe_path = ntpath.join(base, sub)
                        if os.path.exists(exe_path):
                            version = get_pe_version(exe_path) or "Unknown"
                            arch = get_pe_architecture(exe_path)
                            self.browser_info[browser_name] = {
                                'path': exe_path,
                                'version': version,
                                'architecture': arch,
                                'found_by_fallback': True,
                            }
                            found = True
                            break
                    if found:
                        break

        return self.browser_info

    # ── Legacy alias methods (backward compat for tests / UI) ────────────────

    def _detect_geekbench(self, sys_apps_data: Optional[dict] = None) -> List[BenchmarkInfo]:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('geekbench')
        if defn:
            return self._discover_from_definition(defn, sys_apps_data)
        return [BenchmarkInfo('geekbench', 'Geekbench', BenchmarkType.CPU,
                              BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')]

    def _detect_cinebench(self, sys_apps_data: Optional[dict] = None) -> BenchmarkInfo:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('cinebench')
        if defn:
            results = self._discover_from_definition(defn, sys_apps_data)
            return results[0] if results else BenchmarkInfo(
                'cinebench', 'Cinebench', BenchmarkType.CPU,
                BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')
        return BenchmarkInfo('cinebench', 'Cinebench', BenchmarkType.CPU,
                             BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')

    def _detect_d3dmark(self) -> BenchmarkInfo:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('d3dmark')
        if defn:
            results = self._discover_from_definition(defn)
            return results[0] if results else BenchmarkInfo(
                'd3dmark', '3DMark', BenchmarkType.GPU,
                BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')
        return BenchmarkInfo('d3dmark', '3DMark', BenchmarkType.GPU,
                             BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')

    def _detect_glmark2(self) -> BenchmarkInfo:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('glmark2')
        if defn:
            results = self._discover_from_definition(defn)
            return results[0] if results else BenchmarkInfo(
                'glmark2', 'glmark2', BenchmarkType.GPU,
                BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')
        return BenchmarkInfo('glmark2', 'glmark2', BenchmarkType.GPU,
                             BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')

    def _detect_speedometer(self) -> BenchmarkInfo:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('speedometer')
        if defn:
            results = self._discover_from_definition(defn)
            return results[0] if results else BenchmarkInfo(
                'speedometer', 'Speedometer', BenchmarkType.WEB,
                BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')
        return BenchmarkInfo('speedometer', 'Speedometer', BenchmarkType.WEB,
                             BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')

    def _detect_jetstream(self) -> BenchmarkInfo:
        """Legacy alias — delegates to data-driven discovery."""
        defn = DEFINITIONS_BY_ID.get('jetstream')
        if defn:
            results = self._discover_from_definition(defn)
            return results[0] if results else BenchmarkInfo(
                'jetstream', 'JetStream', BenchmarkType.WEB,
                BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')
        return BenchmarkInfo('jetstream', 'JetStream', BenchmarkType.WEB,
                             BenchmarkStatus.NOT_INSTALLED, reason='Definition not found')

    # ── Public scan / detect ─────────────────────────────────────────────────

    def scan(self, profile=None) -> List[BenchmarkInfo]:
        """Scan system for system capabilities and benchmarks.

        With an EnvironmentProfile, each entry also gets a compatibility
        result, and a proven incompatibility turns a found benchmark into
        UNSUPPORTED. Never raises to the GUI — errors go to diagnostics.
        """
        try:
            self._detect_system_capabilities()
        except Exception as exc:
            logger.error("System capability detection failed: %s", exc)
        benchmarks = self.detect_benchmarks()
        if profile is not None:
            self.apply_compatibility(profile)
        return benchmarks

    def apply_compatibility(self, profile) -> None:
        from app.environment.compatibility import INCOMPATIBLE, check_compatibility
        for bm in self.benchmarks:
            definition = DEFINITIONS_BY_ID.get(bm.name.split('_')[0])
            if definition is None:
                continue
            try:
                result = check_compatibility(definition, profile)
            except Exception as exc:
                logger.error("compatibility check failed for %s: %s", bm.name, exc)
                bm.scan_diagnostics = (bm.scan_diagnostics or []) + [f"compatibility check failed: {exc}"]
                continue
            bm.compatibility = result.to_dict()
            if (result.status == INCOMPATIBLE
                    and bm.status not in (BenchmarkStatus.NOT_INSTALLED, BenchmarkStatus.SCAN_ERROR)):
                bm.status = BenchmarkStatus.UNSUPPORTED
                bm.reason = "Unsupported on this system: " + "; ".join(result.reasons)

    def detect_benchmarks(self) -> List[BenchmarkInfo]:
        """Detect all available benchmarks on the system."""
        self.benchmarks = []
        try:
            sys_apps = self._query_system_apps()
        except Exception:
            sys_apps = {'appx_cinebench': None, 'start_apps': []}
        for definition in BENCHMARK_DEFINITIONS:
            try:
                results = self._discover_from_definition(definition, sys_apps)
            except Exception as exc:
                logger.error(
                    "Discovery failed for %s: %s — returning NOT_INSTALLED fallback",
                    definition.id, exc,
                )
                results = [BenchmarkInfo(
                    name=definition.id,
                    display_name=definition.display_name,
                    type=self._category_to_type(definition.category),
                    status=BenchmarkStatus.NOT_INSTALLED,
                    executable_path=None,
                    version=None,
                    architecture=None,
                    official_url=definition.official_url,
                    reason=f"Discovery error: {exc}",
                    prerequisites=['Windows 10/11'],
                    detection_method='error',
                    file_exists=False,
                    scan_diagnostics=[f"discovery failed: {exc}"],
                )]
            self.benchmarks.extend(results)
        return self.benchmarks

    def get_runnable_benchmarks(self) -> List[BenchmarkInfo]:
        return [bm for bm in self.benchmarks if bm.is_runnable()]

    def get_automatable_benchmarks(self) -> List[BenchmarkInfo]:
        return [bm for bm in self.benchmarks if bm.is_automatable()]


# ──────────────────────────────────────────────────────────────────────────────
# BenchmarkExecutor (unchanged)
# ──────────────────────────────────────────────────────────────────────────────

class BenchmarkExecutor:
    """Executes benchmarks and collects results."""

    def __init__(self, db: Any = None):
        self.db = db
        self.manager = BenchmarkManager()

    def run_benchmark(self, benchmark_info: BenchmarkInfo, session_id: int,
                      repetitions: int = 3, warmup_runs: int = 1,
                      timeout_seconds: int = 600) -> Dict[str, Any]:
        result_data = {
            'benchmark_name': benchmark_info.name,
            'benchmark_type': benchmark_info.type.value,
            'status': 'pending',
            'results': [],
            'errors': [],
        }
        try:
            if not benchmark_info.is_runnable():
                result_data['status'] = 'skipped'
                result_data['error'] = f"Benchmark not runnable: {benchmark_info.reason}"
                return result_data
            for i in range(warmup_runs):
                logger.info(f"Running warmup {i+1}/{warmup_runs} for {benchmark_info.display_name}")
                self._run_single_execution(benchmark_info, timeout_seconds)
            for i in range(repetitions):
                logger.info(f"Running measurement {i+1}/{repetitions} for {benchmark_info.display_name}")
                execution_result = self._run_single_execution(benchmark_info, timeout_seconds)
                result_data['results'].append(execution_result)
            if result_data['results']:
                valid_results = [r for r in result_data['results'] if r.get('score') is not None]
                if valid_results:
                    result_data['average_score'] = sum(r['score'] for r in valid_results) / len(valid_results)
                    result_data['status'] = 'completed'
                else:
                    result_data['status'] = 'completed' if all('error' not in r for r in result_data['results']) else 'failed'
            else:
                result_data['status'] = 'failed'
        except Exception as e:
            logger.error(f"Error running {benchmark_info.display_name}: {e}")
            result_data['status'] = 'failed'
            result_data['error'] = str(e)
        return result_data

    def _run_single_execution(self, benchmark_info: BenchmarkInfo, timeout_seconds: int) -> Dict[str, Any]:
        if not benchmark_info.executable_path and benchmark_info.type == BenchmarkType.WEB:
            # Nothing is launched here (the UI opens the page via _WebLaunchWorker),
            # so never report a launch status: a running browser proves nothing.
            return {'score': None, 'unit': 'points', 'execution_method': 'manual',
                    'timestamp': datetime.now().isoformat()}
        if benchmark_info.name.startswith('geekbench'):
            return self._run_geekbench(benchmark_info.executable_path, timeout_seconds)
        elif benchmark_info.name == 'cinebench':
            return self._run_cinebench(benchmark_info.executable_path, timeout_seconds)
        # 3DMark and glmark2 are never started from here: their command lines were never checked on a real
        # run (a verified tool spec in data/tools is the way to automate them).
        return {'score': None, 'unit': 'points', 'execution_method': 'manual',
                'timestamp': datetime.now().isoformat()}

    def _run_geekbench(self, exe_path: Optional[str], timeout_seconds: int) -> Dict[str, Any]:
        return {'score': None, 'unit': 'points', 'execution_method': 'manual',
                'timestamp': datetime.now().isoformat(),
                'message': 'Geekbench requires manual execution. Windows GUI version does not support CLI automation without a Pro license. Please run the benchmark and enter the score.'}

    def _run_cinebench(self, exe_path: Optional[str], timeout_seconds: int) -> Dict[str, Any]:
        return {'score': None, 'unit': 'points', 'execution_method': 'manual',
                'timestamp': datetime.now().isoformat(),
                'message': 'Cinebench requires manual execution. Please run the benchmark and enter the score.'}
