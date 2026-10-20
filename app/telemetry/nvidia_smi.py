"""Parse NVIDIA GPU telemetry from `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` (Phase 7).

A missing or unsupported value ("[N/A]", "[Not Supported]") is None, never 0. Checked against a
real line from an RTX 5090 Laptop GPU (driver 617.42) on 2026-10-17. memory.used / memory.total (MiB, owner
2026-10-20: a full graphics card memory explains a slow AI test) checked on the same PC on 2026-10-20:
"0, NVIDIA GeForce RTX 5090 Laptop GPU, 45, 1590, 14001, 23.22, 0, 822, 24463". A line without the two memory
fields (an older reading) is still read.

Drafted by the local agent, reviewed.
"""

import csv
import math
from dataclasses import dataclass, field
from typing import Optional

QUERY_FIELDS = (
    "index",
    "name",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
    "power.draw",
    "utilization.gpu",
    "memory.used",
    "memory.total",
)
BASE_FIELDS = 7                 # the fields before memory.used


def query_args() -> list:
    return [
        "--query-gpu=" + ",".join(QUERY_FIELDS),
        "--format=csv,noheader,nounits",
    ]


@dataclass
class GpuReading:
    index: Optional[int] = None
    name: Optional[str] = None
    temperature_c: Optional[float] = None
    sm_clock_mhz: Optional[float] = None
    mem_clock_mhz: Optional[float] = None
    power_w: Optional[float] = None
    utilization_pct: Optional[float] = None
    vram_used_mib: Optional[float] = None
    vram_total_mib: Optional[float] = None


@dataclass
class NvidiaSmiParse:
    gpus: list = field(default_factory=list)
    error: Optional[str] = None


def parse_value(text: Optional[str]) -> Optional[float]:
    if not isinstance(text, str):
        return None

    value_text = text.strip()
    if (
        value_text == ""
        or value_text == "[N/A]"
        or value_text == "N/A"
        or value_text == "[Not Supported]"
        or value_text == "[Unknown Error]"
        or value_text.startswith("[")
    ):
        return None

    try:
        value = float(value_text)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value):
        return None

    return value


def parse_query_output(text: Optional[str]) -> NvidiaSmiParse:
    if not isinstance(text, str) or not text.strip():
        return NvidiaSmiParse(gpus=[], error="no output from nvidia-smi")

    gpus = []
    remembered_error = None

    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue

        if (
            stripped_line.startswith("NVIDIA-SMI has failed")
            or stripped_line.startswith("No devices were found")
        ):
            return NvidiaSmiParse(gpus=[], error=stripped_line)

        try:
            row = next(csv.reader([line]), None)
        except Exception:
            row = None

        if row is None or len(row) not in (BASE_FIELDS, len(QUERY_FIELDS)):
            remembered_error = remembered_error or f"unexpected line: {stripped_line}"
            continue

        fields = [value.strip() for value in row]

        index_text = fields[0]
        index = int(index_text) if index_text.isdigit() else None

        name = fields[1] if fields[1] else None

        gpus.append(
            GpuReading(
                index=index,
                name=name,
                temperature_c=parse_value(fields[2]),
                sm_clock_mhz=parse_value(fields[3]),
                mem_clock_mhz=parse_value(fields[4]),
                power_w=parse_value(fields[5]),
                utilization_pct=parse_value(fields[6]),
                vram_used_mib=parse_value(fields[7]) if len(fields) > 8 else None,
                vram_total_mib=parse_value(fields[8]) if len(fields) > 8 else None,
            )
        )

    if not gpus:
        return NvidiaSmiParse(gpus=[], error=remembered_error or "no GPU lines")

    return NvidiaSmiParse(gpus=gpus, error=None)
