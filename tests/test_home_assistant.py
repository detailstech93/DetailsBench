"""Whole-device power from a smart plug through Home Assistant (owner, 2026-10-27)."""

import io
import json
import urllib.error

import pytest

from app.library import view
from app.paths import load_settings
from app.telemetry import home_assistant as ha

STATES = [
    {"entity_id": "sensor.desk_plug_power", "state": "87.4", "attributes": {"unit_of_measurement": "W",
                                                                            "friendly_name": "Desk plug Power"}},
    {"entity_id": "sensor.heater_power", "state": "1.2", "attributes": {"unit_of_measurement": "kW",
                                                                        "friendly_name": "Air heater"}},
    {"entity_id": "sensor.nas_power", "state": "unavailable", "attributes": {"unit_of_measurement": "W"}},
    {"entity_id": "sensor.desk_plug_energy", "state": "12", "attributes": {"unit_of_measurement": "kWh"}},
    {"entity_id": "switch.desk_plug", "state": "on", "attributes": {}},
]


def opener(seen):
    def open_(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization"), timeout))
        if req.full_url.endswith("/api/states"):
            body = STATES
        else:
            body = next((s for s in STATES if req.full_url.endswith("/" + s["entity_id"])), None)
            if body is None:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(json.dumps(body).encode())
    return open_


def test_every_power_sensor_is_listed_by_name_in_watts():
    seen = []
    sensors = ha.power_sensors("http://ha.local:8123/", "secret", opener(seen))
    assert sensors == [("sensor.heater_power", "Air heater", 1200.0),
                       ("sensor.desk_plug_power", "Desk plug Power", 87.4),
                       ("sensor.nas_power", "sensor.nas_power", None)]
    assert seen == [("http://ha.local:8123/api/states", "Bearer secret", ha.TIMEOUT)]
    with pytest.raises(ValueError):
        ha.power_sensors("ha.local:8123", "secret", opener([]))


def test_the_plug_is_read_and_a_missing_one_is_said_without_the_token():
    src = ha.PlugSource("http://ha.local:8123", "secret", "sensor.desk_plug_power", opener([]))
    assert src.available()[0] and src.read() == {"plug_power": (87.4, "W")}
    ok, why = ha.PlugSource("http://ha.local:8123", "secret", "sensor.gone", opener([])).available()
    assert not ok and "sensor.gone" in why and "secret" not in why
    ok, why = ha.PlugSource("http://ha.local:8123", "secret", "sensor.nas_power", opener([])).available()
    assert not ok and "no power value" in why


def test_dialog_lists_the_plugs_and_saves_the_choice(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.ui.plug_dialog import PlugDialog
    dialog = PlugDialog(tmp_path, fetch=lambda url, token: ha.power_sensors(url, token, opener([])))
    dialog.url.setText("http://ha.local:8123")
    dialog.token.setText("secret")
    dialog.load()
    assert dialog.plug.count() == 3 and "Desk plug Power: 87 W" in dialog.plug.itemText(1)
    dialog.plug.setCurrentIndex(1)
    dialog.save()
    assert ha.settings(tmp_path) == ("http://ha.local:8123", "secret", "sensor.desk_plug_power")
    PlugDialog(tmp_path).turn_off()
    assert load_settings(tmp_path)[0][ha.ENTITY_KEY] == ""


def test_part_results_show_the_whole_device_power():
    pkg = {"attribution": {"part": "system"}, "test": {},
           "result": {"metrics": {"cpu_stability": {"mean": 99.0}, "tel_plug_power_avg": {"mean": 310.2},
                                  "tel_plug_power_max": {"mean": 355.0}}}}
    assert view.stress_text(pkg) == "CPU 99.0% stable; whole device 310 W avg / 355 W max"
