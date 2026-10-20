"""System information gathering module."""

import subprocess
import platform
import logging
import sys
from typing import List, Optional, Tuple

# Configure basic logging for hardware detection issues
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_cpu_brand() -> Optional[str]:
    """Get CPU brand/model from Windows System Information or WMI."""
    try:
        # Try PowerShell command for accurate info
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_Processor).Name'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            # Parse PowerShell output - look for actual values after property name
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                # Skip empty lines and headers
                if not line or line.startswith('Name'):
                    continue
                # Look for actual CPU model strings (typically longer than 5 chars)
                if len(line) > 5 and not line.startswith('@{'):
                    return line
        
    except FileNotFoundError:               # no PowerShell (Linux): the processor's name is in /proc/cpuinfo
        name = parse_cpuinfo_model(_read("/proc/cpuinfo"))
        if name:
            return name
    except Exception as e:
        logger.warning(f"PowerShell CPU brand detection failed: {e}")

    # Fallback to platform module for basic info
    try:
        processor = platform.processor()
        if processor and processor != 'unknown':
            return processor
    except Exception as e:
        logger.warning(f"Platform CPU detection failed: {e}")
    
    return None


def get_cpu_model() -> Optional[str]:
    """Get detailed CPU model information."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_Processor).Name'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            for line in output.split('\n'):
                line = line.strip()
                # Skip headers and empty lines
                if line and not line.startswith('Name') and len(line) > 5:
                    return line
        
    except Exception as e:
        logger.warning(f"PowerShell CPU model detection failed: {e}")
    
    return get_cpu_brand()


def get_core_count() -> Optional[int]:
    """Get number of physical cores."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_Processor).NumberOfCores'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            # Parse the numeric value from output
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                # Skip headers and empty lines
                if not line or line.startswith('NumberOfCores'):
                    continue
                # Try to extract numeric value
                try:
                    return int(float(line))  # Handle both int and float strings
                except ValueError:
                    continue
    
    except Exception as e:
        logger.warning(f"PowerShell core count detection failed: {e}")
    
    # Fallback to platform module
    try:
        import multiprocessing
        return multiprocessing.cpu_count()
    except Exception as e:
        logger.warning(f"Multiprocessing core count fallback failed: {e}")
    
    return None


def get_thread_count() -> Optional[int]:
    """Get number of threads."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_Processor).NumberOfLogicalProcessors'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            # Parse the numeric value from output
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                # Skip headers and empty lines
                if not line or line.startswith('NumberOfLogicalProcessors'):
                    continue
                # Try to extract numeric value
                try:
                    return int(float(line))  # Handle both int and float strings
                except ValueError:
                    continue
    
    except Exception as e:
        logger.warning(f"PowerShell thread count detection failed: {e}")
    
    # Fallback to platform module
    try:
        import multiprocessing
        return multiprocessing.cpu_count()  # This returns logical cores
    except Exception as e:
        logger.warning(f"Multiprocessing thread count fallback failed: {e}")
    
    return None


def get_ram_size() -> Optional[Tuple[int, str]]:
    """Get RAM size in GB and type."""
    try:
        # Use psutil for reliable RAM detection (preferred method)
        import psutil
        mem = psutil.virtual_memory()
        gb = round(mem.total / (1024**3), 1)  # Convert to GB with 1 decimal place
        return (gb, "GB")
    
    except ImportError:
        logger.warning("psutil not available for RAM detection")
    except Exception as e:
        logger.warning(f"psutil RAM detection failed: {e}")
    
    # Fallback to PowerShell
    try:
        result = subprocess.run(
            ['powershell', '-Command', "(Get-WmiObject -Class Win32_ComputerSystem).TotalPhysicalMemory / 1GB"],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                # Skip headers and empty lines
                if not line or line.startswith('TotalPhysicalMemory'):
                    continue
                # Try to extract numeric value
                try:
                    gb = float(line)
                    return (round(gb, 1), "GB")
                except ValueError:
                    continue
        
    except Exception as e:
        logger.warning(f"PowerShell RAM detection failed: {e}")
    
    return None


def get_gpu_name() -> Optional[str]:
    """Get GPU name from Windows System Information or DirectX."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_VideoController).Name'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            lines = output.split('\n')
            for line in lines:
                line = line.strip()
                # Skip headers and empty lines, return first valid GPU name
                if not line or line.startswith('Name'):
                    continue
                if len(line) > 5 and not line.startswith('@{'):
                    return line
    
    except Exception as e:
        logger.warning(f"PowerShell GPU detection failed: {e}")
    
    # Fallback to DirectX method
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(dxdiag /disp | Select-String "Display Device").Line'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            # Extract GPU name from DirectX output
            for line in output.split('\n'):
                line = line.strip()
                if ':' in line and len(line) > 10:
                    parts = line.split(':')
                    return parts[-1].strip()
    
    except Exception as e:
        logger.warning(f"DirectX GPU detection failed: {e}")
    
    return None


def parse_gpu_lines(text: str) -> List[str]:
    """Parse "Name|PNPDeviceID" lines; keep physical adapters only.

    Virtual adapters (e.g. "Microsoft Remote Display Adapter", PNP id SWD\\...)
    are software devices, not GPUs; physical ones sit on a bus such as PCI\\.
    """
    names = []
    for line in text.splitlines():
        name, _, pnp = line.strip().partition("|")
        name, pnp = name.strip(), pnp.strip().upper()
        if not name or pnp.startswith(("SWD\\", "ROOT\\")):
            continue
        if name not in names:
            names.append(name)
    return names


def get_gpu_names() -> Optional[List[str]]:
    """All physical display adapters (Win32_VideoController), e.g. integrated + discrete.

    Returns None when they cannot be read (non-Windows or query failure).
    """
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_VideoController | '
             'ForEach-Object { $_.Name + "|" + $_.PNPDeviceID }'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return parse_gpu_lines(result.stdout) or None
    except Exception as e:
        logger.warning(f"GPU list detection failed: {e}")
    return None


def get_operating_system() -> str:
    """Get operating system information."""
    try:
        result = subprocess.run(
            ['powershell', '-Command', '(Get-WmiObject -Class Win32_OperatingSystem).Caption'],
            capture_output=True, text=True, timeout=10
        )
        
        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            for line in output.split('\n'):
                line = line.strip()
                # Skip headers and return OS caption
                if line and not line.startswith('Caption') and len(line) > 5:
                    return line
    
    except Exception as e:
        logger.warning(f"PowerShell OS detection failed: {e}")
    
    # Fallback to platform module
    try:
        os_info = platform.system()
        release = platform.release()
        version = platform.version()
        
        return f"{os_info} {release} ({version})"
    except Exception as e:
        logger.warning(f"Platform OS detection failed: {e}")
        return "Unknown"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def parse_cpuinfo_model(text: str) -> Optional[str]:
    """The processor's name from /proc/cpuinfo ("model name" on x86, "Model" on many ARM boards)."""
    for key in ("model name", "Model", "Hardware"):
        for line in text.splitlines():
            name, sep, value = line.partition(":")
            if sep and name.strip() == key and value.strip():
                return " ".join(value.split())
    return None


def parse_lspci_gpus(text: str) -> List[str]:
    """Display adapters from `lspci` lines ("... VGA compatible controller: NVIDIA Corporation ...")."""
    out = []
    for line in text.splitlines():
        for kind in ("VGA compatible controller:", "3D controller:", "Display controller:"):
            if kind in line:
                name = line.split(kind, 1)[1].strip()
                if name and name not in out:
                    out.append(name)
    return out


def _linux_gpus(run=subprocess.run) -> List[str]:
    """nvidia-smi names first (exact model), then lspci; WSL has no PCI list, only the NVIDIA tool."""
    names: List[str] = []
    for command, parse in ((["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                            lambda t: [l.strip() for l in t.splitlines() if l.strip()]),
                           (["lspci"], parse_lspci_gpus)):
        try:
            res = run(command, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            for name in parse(res.stdout or ""):
                if name not in names and not any(name in n or n in name for n in names):
                    names.append(name)
    return names


def linux_summary(read=_read, run=subprocess.run) -> dict:
    """The same summary on Linux (no PowerShell there): /proc/cpuinfo, psutil, nvidia-smi / lspci, os-release."""
    from app.environment.linux_parsers import is_wsl, parse_os_release
    import os
    try:
        import psutil
        cores, threads = psutil.cpu_count(logical=False), psutil.cpu_count()
    except Exception:
        cores = threads = os.cpu_count()
    ram = get_ram_size()
    cpu = parse_cpuinfo_model(read("/proc/cpuinfo")) or platform.processor() or None
    release = parse_os_release(read("/etc/os-release"))
    system = release.get("PRETTY_NAME") or f"Linux {platform.release()}"
    if is_wsl(read("/proc/version"), os.environ):
        system += " (WSL)"
    return {'cpu_brand': cpu, 'cpu_model': cpu, 'core_count': cores, 'thread_count': threads,
            'ram_size_gb': ram[0] if ram else None, 'gpu_name': ", ".join(_linux_gpus(run)) or None,
            'operating_system': system}


def get_system_summary(platform: Optional[str] = None) -> dict:
    """Get a summary of all system information."""
    if (platform or sys.platform).startswith("linux"):
        return linux_summary()
    ram_info = get_ram_size()
    return {
        'cpu_brand': get_cpu_brand(),
        'cpu_model': get_cpu_model(),
        'core_count': get_core_count(),
        'thread_count': get_thread_count(),
        'ram_size_gb': ram_info[0] if ram_info else None,
        # All physical GPUs (integrated + discrete); the single-name lookup is the fallback
        'gpu_name': ", ".join(get_gpu_names() or []) or get_gpu_name(),
        'operating_system': get_operating_system()
    }

