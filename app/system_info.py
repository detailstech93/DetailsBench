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


def get_gpu_names() -> Optional[List[str]]:
    """All display adapters (Win32_VideoController), e.g. integrated + discrete.

    Returns None when they cannot be read (non-Windows or query failure).
    """
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return names or None
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


def get_system_summary() -> dict:
    """Get a summary of all system information."""
    ram_info = get_ram_size()
    return {
        'cpu_brand': get_cpu_brand(),
        'cpu_model': get_cpu_model(),
        'core_count': get_core_count(),
        'thread_count': get_thread_count(),
        'ram_size_gb': ram_info[0] if ram_info else None,
        'gpu_name': get_gpu_name(),
        'operating_system': get_operating_system()
    }

