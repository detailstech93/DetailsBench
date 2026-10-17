"""Tests for app.telemetry.lhm."""

import math

from app.telemetry.lhm import LhmSensor, LhmParse, MAX_DEPTH, parse_lhm_data, pick


def _example_tree():
    return {
        "id": 0,
        "Text": "Sensor",
        "Children": [
            {
                "id": 1,
                "Text": "DESKTOP",
                "Children": [
                    {
                        "id": 2,
                        "Text": "AMD Ryzen 9",
                        "HardwareId": "/amdcpu/0",
                        "Children": [
                            {
                                "id": 3,
                                "Text": "Temperatures",
                                "Children": [
                                    {
                                        "id": 4,
                                        "Text": "Core (Tctl/Tdie)",
                                        "SensorId": "/amdcpu/0/temperature/2",
                                        "Type": "Temperature",
                                        "Value": "65.3 °C",
                                        "RawValue": 65.25,
                                    }
                                ],
                            },
                            {
                                "id": 5,
                                "Text": "Powers",
                                "Children": [
                                    {
                                        "id": 6,
                                        "Text": "Package",
                                        "SensorId": "/amdcpu/0/power/0",
                                        "Type": "Power",
                                        "RawValue": 88.0,
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "id": 7,
                        "Text": "NVIDIA GeForce RTX 4090",
                        "HardwareId": "/gpu-nvidia/0",
                        "Children": [
                            {
                                "id": 8,
                                "Text": "Clocks",
                                "Children": [
                                    {
                                        "id": 9,
                                        "Text": "GPU Core",
                                        "SensorId": "/gpu-nvidia/0/clock/0",
                                        "Type": "Clock",
                                        "RawValue": 2520.0,
                                    }
                                ],
                            },
                            {
                                "id": 10,
                                "Text": "Temperatures",
                                "Children": [
                                    {
                                        "id": 11,
                                        "Text": "GPU Core",
                                        "SensorId": "/gpu-nvidia/0/temperature/0",
                                        "Type": "Temperature",
                                        "RawValue": 61.0,
                                    },
                                    {
                                        "id": 12,
                                        "Text": "GPU Hot Spot",
                                        "SensorId": "/gpu-nvidia/0/temperature/1",
                                        "Type": "Temperature",
                                        "RawValue": None,
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }
        ],
    }


def test_parse_example_counts_and_order():
    result = parse_lhm_data(_example_tree())

    assert isinstance(result, LhmParse)
    assert result.error is None
    assert result.skipped == 1
    assert [s.sensor_id for s in result.sensors] == [
        "/amdcpu/0/temperature/2",
        "/amdcpu/0/power/0",
        "/gpu-nvidia/0/clock/0",
        "/gpu-nvidia/0/temperature/0",
    ]


def test_parse_example_sensor_fields():
    result = parse_lhm_data(_example_tree())

    cpu_temp = result.sensors[0]
    assert cpu_temp.hardware_id == "/amdcpu/0"
    assert cpu_temp.hardware == "AMD Ryzen 9"
    assert cpu_temp.name == "Core (Tctl/Tdie)"
    assert cpu_temp.sensor_type == "Temperature"
    assert cpu_temp.value == 65.25
    assert cpu_temp.unit == "C"

    cpu_power = result.sensors[1]
    assert cpu_power.hardware_id == "/amdcpu/0"
    assert cpu_power.hardware == "AMD Ryzen 9"
    assert cpu_power.name == "Package"
    assert cpu_power.sensor_type == "Power"
    assert cpu_power.value == 88.0
    assert cpu_power.unit == "W"

    gpu_clock = result.sensors[2]
    assert gpu_clock.hardware_id == "/gpu-nvidia/0"
    assert gpu_clock.hardware == "NVIDIA GeForce RTX 4090"
    assert gpu_clock.name == "GPU Core"
    assert gpu_clock.sensor_type == "Clock"
    assert gpu_clock.value == 2520.0
    assert gpu_clock.unit == "MHz"

    gpu_temp = result.sensors[3]
    assert gpu_temp.hardware_id == "/gpu-nvidia/0"
    assert gpu_temp.hardware == "NVIDIA GeForce RTX 4090"
    assert gpu_temp.name == "GPU Core"
    assert gpu_temp.sensor_type == "Temperature"
    assert gpu_temp.value == 61.0
    assert gpu_temp.unit == "C"


def test_parse_non_dict_input():
    for bad in (None, "data", 42, [], (1, 2)):
        result = parse_lhm_data(bad)
        assert result.sensors == []
        assert result.skipped == 0
        assert result.error == "unexpected data.json content"


def test_parse_empty_tree():
    result = parse_lhm_data({})
    assert result.sensors == []
    assert result.skipped == 0
    assert result.error == "no sensors in data.json"


def test_parse_children_not_list_is_treated_empty():
    data = {
        "id": 0,
        "Text": "root",
        "Children": "not-a-list",
    }

    result = parse_lhm_data(data)

    assert result.sensors == []
    assert result.skipped == 0
    assert result.error == "no sensors in data.json"


def test_parse_sensor_with_children_not_list():
    data = {
        "id": 0,
        "Text": "root",
        "SensorId": "/root/sensor/0",
        "Type": "Temperature",
        "RawValue": 21.5,
        "Children": "bad",
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.skipped == 0
    assert len(result.sensors) == 1
    assert result.sensors[0].sensor_id == "/root/sensor/0"
    assert result.sensors[0].value == 21.5
    assert result.sensors[0].unit == "C"


def test_parse_invalid_raw_values_are_skipped():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {"id": 1, "Text": "bool", "SensorId": "/bool", "Type": "Temperature", "RawValue": True},
            {"id": 2, "Text": "string", "SensorId": "/string", "Type": "Temperature", "RawValue": "65.0"},
            {"id": 3, "Text": "nan", "SensorId": "/nan", "Type": "Temperature", "RawValue": float("nan")},
            {"id": 4, "Text": "inf", "SensorId": "/inf", "Type": "Temperature", "RawValue": float("inf")},
            {"id": 5, "Text": "missing", "SensorId": "/missing", "Type": "Temperature"},
        ],
    }

    result = parse_lhm_data(data)

    assert result.sensors == []
    assert result.skipped == 5
    assert result.error == "no sensors in data.json"


def test_parse_valid_and_invalid_raw_values():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {"id": 1, "Text": "good", "SensorId": "/good", "Type": "Load", "RawValue": 42},
            {"id": 2, "Text": "bad", "SensorId": "/bad", "Type": "Load", "RawValue": "42"},
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.skipped == 1
    assert len(result.sensors) == 1
    assert result.sensors[0].sensor_id == "/good"
    assert result.sensors[0].value == 42.0
    assert result.sensors[0].unit == "%"


def test_parse_nearest_hardware_ancestor_wins():
    data = {
        "id": 0,
        "Text": "outer",
        "HardwareId": "/outer/0",
        "Children": [
            {
                "id": 1,
                "Text": "inner",
                "HardwareId": "/inner/0",
                "Children": [
                    {
                        "id": 2,
                        "Text": "inner sensor",
                        "SensorId": "/inner/0/sensor/0",
                        "Type": "Temperature",
                        "RawValue": 10.0,
                    }
                ],
            },
            {
                "id": 3,
                "Text": "outer sensor",
                "SensorId": "/outer/0/sensor/0",
                "Type": "Temperature",
                "RawValue": 20.0,
            },
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.sensors[0].hardware_id == "/inner/0"
    assert result.sensors[0].hardware == "inner"
    assert result.sensors[1].hardware_id == "/outer/0"
    assert result.sensors[1].hardware == "outer"


def test_parse_sensor_without_hardware_context():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {
                "id": 1,
                "Text": "group",
                "Children": [
                    {
                        "id": 2,
                        "Text": "sensor",
                        "SensorId": "/sensor/0",
                        "Type": "Voltage",
                        "RawValue": 1.2,
                    }
                ],
            }
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.sensors[0].hardware_id is None
    assert result.sensors[0].hardware is None
    assert result.sensors[0].unit == "V"


def test_parse_unknown_sensor_type_has_empty_unit():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {
                "id": 1,
                "Text": "sensor",
                "SensorId": "/sensor/0",
                "Type": "Mystery",
                "RawValue": 3.0,
            }
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.sensors[0].sensor_type == "Mystery"
    assert result.sensors[0].unit == ""


def test_parse_deep_chain_sets_error_but_keeps_earlier_sensors():
    deep = {"id": 40, "Text": "level40"}
    for level in range(39, 0, -1):
        deep = {"id": level, "Text": "level" + str(level), "Children": [deep]}

    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {
                "id": 1,
                "Text": "early sensor",
                "SensorId": "/early/0",
                "Type": "Temperature",
                "RawValue": 1.0,
            },
            deep,
        ],
    }

    result = parse_lhm_data(data)

    assert result.error == "data.json nested deeper than 32 levels"
    assert len(result.sensors) == 1
    assert result.sensors[0].sensor_id == "/early/0"


def test_parse_deep_chain_without_sensors_keeps_deep_error():
    deep = {"id": 40, "Text": "level40"}
    for level in range(39, 0, -1):
        deep = {"id": level, "Text": "level" + str(level), "Children": [deep]}

    data = {"id": 0, "Text": "root", "Children": [deep]}

    result = parse_lhm_data(data)

    assert result.error == "data.json nested deeper than 32 levels"
    assert result.sensors == []
    assert result.skipped == 0


def test_pick_by_type():
    sensors = parse_lhm_data(_example_tree()).sensors

    temps = pick(sensors, "Temperature")
    assert [s.sensor_id for s in temps] == [
        "/amdcpu/0/temperature/2",
        "/gpu-nvidia/0/temperature/0",
    ]

    powers = pick(sensors, "Power")
    assert [s.sensor_id for s in powers] == ["/amdcpu/0/power/0"]

    clocks = pick(sensors, "Clock")
    assert [s.sensor_id for s in clocks] == ["/gpu-nvidia/0/clock/0"]


def test_pick_by_type_and_hardware_prefix():
    sensors = parse_lhm_data(_example_tree()).sensors

    cpu_temps = pick(sensors, "Temperature", hardware_prefix="/amdcpu")
    assert [s.sensor_id for s in cpu_temps] == ["/amdcpu/0/temperature/2"]

    gpu_temps = pick(sensors, "Temperature", hardware_prefix="/gpu-nvidia")
    assert [s.sensor_id for s in gpu_temps] == ["/gpu-nvidia/0/temperature/0"]

    no_match = pick(sensors, "Temperature", hardware_prefix="/missing")
    assert no_match == []


def test_pick_by_name_contains_case_insensitive():
    sensors = parse_lhm_data(_example_tree()).sensors

    cores = pick(sensors, "Temperature", name_contains="CORE")
    assert [s.sensor_id for s in cores] == [
        "/amdcpu/0/temperature/2",
        "/gpu-nvidia/0/temperature/0",
    ]

    hot = pick(sensors, "Temperature", name_contains="hot spot")
    assert hot == []

    gpu_core = pick(sensors, "Clock", name_contains="gpu core")
    assert [s.sensor_id for s in gpu_core] == ["/gpu-nvidia/0/clock/0"]


def test_pick_combines_filters_and_keeps_order():
    sensors = parse_lhm_data(_example_tree()).sensors

    selected = pick(
        sensors,
        "Temperature",
        hardware_prefix="/gpu-nvidia",
        name_contains="core",
    )

    assert [s.sensor_id for s in selected] == ["/gpu-nvidia/0/temperature/0"]


def test_pick_ignores_non_matching_type_even_if_other_filters_match():
    sensors = parse_lhm_data(_example_tree()).sensors

    selected = pick(
        sensors,
        "Power",
        hardware_prefix="/gpu-nvidia",
        name_contains="core",
    )

    assert selected == []


def test_parse_missing_text_becomes_empty_name():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {
                "id": 1,
                "SensorId": "/sensor/0",
                "Type": "Temperature",
                "RawValue": 5.0,
            }
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.sensors[0].name == ""
    assert result.sensors[0].value == 5.0


def test_parse_non_dict_nodes_are_ignored():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            "not-a-node",
            42,
            None,
            {
                "id": 1,
                "Text": "sensor",
                "SensorId": "/sensor/0",
                "Type": "Temperature",
                "RawValue": 9.0,
            },
        ],
    }

    result = parse_lhm_data(data)

    assert result.error is None
    assert result.skipped == 0
    assert len(result.sensors) == 1
    assert result.sensors[0].sensor_id == "/sensor/0"


def test_parse_sensor_requires_string_sensor_id_and_type():
    data = {
        "id": 0,
        "Text": "root",
        "Children": [
            {"id": 1, "Text": "bad id", "SensorId": 1, "Type": "Temperature", "RawValue": 1.0},
            {"id": 2, "Text": "bad type", "SensorId": "/sensor/0", "Type": 2, "RawValue": 1.0},
            {"id": 3, "Text": "missing type", "SensorId": "/sensor/1", "RawValue": 1.0},
            {"id": 4, "Text": "missing id", "Type": "Temperature", "RawValue": 1.0},
        ],
    }

    result = parse_lhm_data(data)

    assert result.sensors == []
    assert result.skipped == 0
    assert result.error == "no sensors in data.json"
