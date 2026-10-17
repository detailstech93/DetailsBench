"""Parse LibreHardwareMonitor's data.json (its local web server) into sensors (Phase 7).

Field names follow LibreHardwareMonitor's HttpServer.cs (SensorId, Type, Text, RawValue,
HardwareId, Children). RawValue is used, never the localized display text "Value".
Not yet checked against a running LibreHardwareMonitor on this machine.

Drafted by the local agent, reviewed.
"""

import math
from dataclasses import dataclass, field
from typing import Optional

UNITS = {
    "Temperature": "C",
    "Clock": "MHz",
    "Power": "W",
    "Load": "%",
    "Voltage": "V",
    "Fan": "RPM",
    "Frequency": "Hz",
    "Current": "A",
    "Energy": "mWh",
    "Throughput": "B/s",
    "Data": "GB",
    "SmallData": "MB",
    "Control": "%",
    "Level": "%",
    "Factor": "",
    "Flow": "L/h",
    "Noise": "dBA",
    "TimeSpan": "s",
    "Humidity": "%",
    "Conductivity": "uS/cm",
}

MAX_DEPTH = 32


@dataclass
class LhmSensor:
    sensor_id: str
    hardware_id: Optional[str]
    hardware: Optional[str]
    name: str
    sensor_type: str
    value: float
    unit: str


@dataclass
class LhmParse:
    sensors: list = field(default_factory=list)
    skipped: int = 0
    error: Optional[str] = None


def _to_finite_float(value):
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isfinite(result):
        return result
    return None


def parse_lhm_data(data):
    result = LhmParse()

    if not isinstance(data, dict):
        result.error = "unexpected data.json content"
        return result

    sensors = []
    skipped = 0
    cut = False

    stack = [(data, 1, None, None)]

    while stack:
        node, depth, hardware_id, hardware = stack.pop()

        if not isinstance(node, dict):
            continue

        sensor_id = node.get("SensorId")
        sensor_type = node.get("Type")

        if isinstance(sensor_id, str) and isinstance(sensor_type, str):
            value = _to_finite_float(node.get("RawValue"))
            if value is None:
                skipped += 1
            else:
                name = node.get("Text")
                if not isinstance(name, str):
                    name = ""

                sensors.append(
                    LhmSensor(
                        sensor_id=sensor_id,
                        hardware_id=hardware_id,
                        hardware=hardware,
                        name=name,
                        sensor_type=sensor_type,
                        value=value,
                        unit=UNITS.get(sensor_type, ""),
                    )
                )

        child_hardware_id = hardware_id
        child_hardware = hardware

        node_hardware_id = node.get("HardwareId")
        if isinstance(node_hardware_id, str):
            child_hardware_id = node_hardware_id
            node_text = node.get("Text")
            child_hardware = node_text if isinstance(node_text, str) else None

        children = node.get("Children")
        if isinstance(children, list):
            child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                if children:
                    cut = True
            else:
                for child in reversed(children):
                    stack.append((child, child_depth, child_hardware_id, child_hardware))

    result.sensors = sensors
    result.skipped = skipped

    if cut:
        result.error = "data.json nested deeper than 32 levels"
    elif not sensors:
        result.error = "no sensors in data.json"

    return result


def pick(sensors, sensor_type, hardware_prefix=None, name_contains=None):
    if not isinstance(sensors, list):
        return []

    selected = []

    for sensor in sensors:
        if not isinstance(sensor, LhmSensor):
            continue

        if sensor.sensor_type != sensor_type:
            continue

        if hardware_prefix is not None:
            if not isinstance(hardware_prefix, str):
                continue
            if not isinstance(sensor.hardware_id, str):
                continue
            if not sensor.hardware_id.startswith(hardware_prefix):
                continue

        if name_contains is not None:
            if not isinstance(name_contains, str):
                continue
            if not isinstance(sensor.name, str):
                continue
            if name_contains.lower() not in sensor.name.lower():
                continue

        selected.append(sensor)

    return selected
