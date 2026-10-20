"""Tests for GPU list parsing (fake Win32_VideoController output)."""

from unittest.mock import patch

from app import system_info
from app.system_info import parse_gpu_lines

# Real "Name|PNPDeviceID" lines from the development laptop (2026-10-17)
THIS_LAPTOP = "\n".join([
    r"Intel(R) Graphics|PCI\VEN_8086&DEV_7D67&SUBSYS_81021B61&REV_06\3&11583659&0&10",
    r"Microsoft Remote Display Adapter|SWD\REMOTEDISPLAYENUM\RDPIDD_INDIRECTDISPLAY&SESSIONID_0006",
    r"NVIDIA GeForce RTX 5090 Laptop GPU|PCI\VEN_10DE&DEV_2C18&SUBSYS_810D1B61&REV_A1\0F284EE5CD2DB04800",
])


def test_keeps_integrated_and_discrete_drops_virtual():
    assert parse_gpu_lines(THIS_LAPTOP) == ["Intel(R) Graphics", "NVIDIA GeForce RTX 5090 Laptop GPU"]


def test_basic_display_driver_and_blank_lines():
    text = "\n".join(["", r"Microsoft Basic Display Adapter|ROOT\BASICDISPLAY\0000",
                      r"AMD Radeon RX 7900 XTX|PCI\VEN_1002"])
    assert parse_gpu_lines(text) == ["AMD Radeon RX 7900 XTX"]


def test_missing_pnp_id_is_kept_and_duplicates_collapsed():
    assert parse_gpu_lines("GPU A|\n" + r"GPU A|PCI\X") == ["GPU A"]


def test_summary_lists_all_gpus():
    with patch.object(system_info, "get_gpu_names", return_value=["Intel(R) Graphics", "NVIDIA RTX"]), \
            patch.object(system_info, "get_gpu_name", return_value="Intel(R) Graphics"):
        assert system_info.get_system_summary("win32")["gpu_name"] == "Intel(R) Graphics, NVIDIA RTX"


def test_summary_falls_back_to_single_name():
    with patch.object(system_info, "get_gpu_names", return_value=None), \
            patch.object(system_info, "get_gpu_name", return_value="Some GPU"):
        assert system_info.get_system_summary("win32")["gpu_name"] == "Some GPU"
