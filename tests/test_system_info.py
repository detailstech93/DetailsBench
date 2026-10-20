

def test_linux_specs_without_powershell():
    """First real Linux run (WSL2, 2026-10-20): the specs came from PowerShell only, so Linux showed none."""
    import subprocess
    from app import system_info as si
    files = {"/proc/cpuinfo": "processor\t: 0\nmodel name\t: Intel(R) Core(TM) Ultra 9 275HX\n",
             "/etc/os-release": 'PRETTY_NAME="Ubuntu 24.04.5 LTS"\n',
             "/proc/version": "Linux version 6.6.87.2-microsoft-standard-WSL2"}

    def run(command, **kw):
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, stdout="NVIDIA GeForce RTX 5090 Laptop GPU\n")
        raise FileNotFoundError(command[0])            # no lspci (WSL)
    s = si.linux_summary(read=lambda p: files.get(p, ""), run=run)
    assert s["cpu_brand"] == "Intel(R) Core(TM) Ultra 9 275HX"
    assert s["gpu_name"] == "NVIDIA GeForce RTX 5090 Laptop GPU"
    assert s["operating_system"].startswith("Ubuntu 24.04.5 LTS")
    assert si.parse_lspci_gpus("01:00.0 VGA compatible controller: NVIDIA Corporation AD103 [GeForce RTX 4080]\n"
                               "00:02.0 Display controller: Intel Corporation Arc\n") == [
        "NVIDIA Corporation AD103 [GeForce RTX 4080]", "Intel Corporation Arc"]
