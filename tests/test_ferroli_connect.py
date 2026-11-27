"""Focused contract and safety tests; no cloud or MQTT is contacted."""
import ast
import asyncio
import json
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1] / "custom_components" / "ferroli_connect"


def test_manifest_and_api_contract():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["config_flow"] is True
    assert "aiomqtt>=2.5.0,<3" in manifest["requirements"]
    api = (ROOT / "api.py").read_text()
    flow = (ROOT / "config_flow.py").read_text()
    assert '"api/token"' in api and '"password": password' in api
    assert '"token": api.token' in flow and '"password": user_input' not in flow
    assert '"api/current_user"' in api and '"api/mqtt/token"' in api


def test_aiomqtt_v2_tls_constructor_contract():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    requirement = next(r for r in manifest["requirements"] if r.startswith("aiomqtt"))
    assert ">=2.5.0" in requirement and "<3" in requirement
    runtime = (ROOT / "runtime.py").read_text()
    assert 'from homeassistant.util.ssl import client_context' in runtime
    assert '"tls_context": client_context()' in runtime
    assert 'ssl.create_default_context' not in runtime
    assert 'client_context_no_verify' not in runtime
    assert '"ssl_context":' not in runtime


def test_setup_failure_closes_runtime_and_delegates_reauth_to_home_assistant():
    # Isolate the setup handler without importing Home Assistant or contacting MQTT.
    setup = ast.parse((ROOT / "__init__.py").read_text())
    function = next(node for node in setup.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry")
    auth_failed = type("ConfigEntryAuthFailed", (Exception,), {})
    not_ready = type("ConfigEntryNotReady", (Exception,), {})

    async def check(error, expected_type):
        runtime = types.SimpleNamespace(start=AsyncMock(side_effect=error), close=AsyncMock())
        namespace = {
            "HomeAssistant": object, "ConfigEntry": object,
            "FerroliRuntime": lambda *args: runtime, "ConfigEntryAuthFailed": auth_failed,
            "ConfigEntryNotReady": not_ready, "aiohttp": types.SimpleNamespace(ClientError=type("ClientError", (Exception,), {})),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "__init__.py"),
                     "exec"), namespace)
        # No config_entries manager: authentication failure must only raise for HA to handle.
        try:
            await namespace["async_setup_entry"](object(), object())
        except expected_type as err:
            if isinstance(error, (PermissionError, OSError, TimeoutError)):
                assert err.__cause__ is error
            else:
                assert err is error
        else:
            raise AssertionError("setup failure was swallowed")
        runtime.close.assert_awaited_once_with()

    asyncio.run(check(PermissionError("expired"), auth_failed))
    asyncio.run(check(OSError("offline"), not_ready))
    asyncio.run(check(TimeoutError("timeout"), not_ready))
    asyncio.run(check(RuntimeError("setup failed"), RuntimeError))


def test_climate_exposes_only_verified_thermostat_controls():
    climate = (ROOT / "climate.py").read_text()
    assert "_attr_available = False" in climate
    assert '"Room thermostat"' in climate
    assert "control_thermostat" in climate
    assert "relay_command" not in climate
    assert "DHW" not in climate
    assert "if mode in (1, 4)" in climate and 'elif mode == 0' in climate
    assert "ClimateEntityFeature.TARGET_TEMPERATURE" in climate
    assert "else ClimateEntityFeature(0)" in climate
    assert "thermostat_mode_command" in climate
    assert "NotImplementedError" not in climate
    assert "thermostat_temperature_command" in climate


def test_water_sensor_platform_is_distinct_and_read_only():
    assert '"sensor"' in (ROOT / "const.py").read_text()
    sensor = (ROOT / "sensor.py").read_text()
    assert "Heating water temperature" in sensor
    assert "Domestic hot water temperature" in sensor
    assert "Heating water target" not in sensor  # targets moved to number.py (v0.1.6+)
    assert "Domestic hot water target" not in sensor
    assert ".publish(" not in sensor
    number = (ROOT / "number.py").read_text()
    assert "Heating water target" in number
    assert "Domestic hot water target" in number
    climate = (ROOT / "climate.py").read_text()
    assert '_attr_name = "Room thermostat"' in climate
    assert 'if not self.entry.options.get("confirm_control", False)' in climate


def test_verified_boiler_setpoint_writes_are_readback_gated():
    const = (ROOT / "const.py").read_text()
    number = (ROOT / "number.py").read_text()
    codec = (ROOT / "codec.py").read_text()
    runtime = (ROOT / "runtime.py").read_text()
    assert '"number"' in const
    assert "dhw_target_command" in codec and "target_command" in codec
    assert 'entry.options.get("confirm_boiler_setpoints", False)' in number
    assert number.count("FerroliWaterTargetNumber") >= 2
    method = runtime[runtime.index("async def control_boiler_setpoint"):runtime.index("def next_message_id")]
    assert "update.get(waiter[0]) == waiter[1]" in runtime
    assert "event.wait()" in method
    assert "Ferroli setpoint write was not confirmed by telemetry" in method
    assert method.index("event.wait()") < method.index("return")
    assert "self.state[" not in method


def test_config_flow_reauth_updates_entry_and_device_selection_is_unique():
    flow = (ROOT / "config_flow.py").read_text()
    assert "entry_data: Mapping[str, Any]" in flow
    assert "async_step_reauth_confirm" in flow
    assert "self._get_reauth_entry()" in flow
    assert "async_update_reload_and_abort" in flow
    assert "async_set_unique_id(str(device.get(\"slug\")))" in flow
    assert "_abort_if_unique_id_configured()" in flow


def test_translation_keys_match_and_only_confirmed_writes_are_described():
    translations = [json.loads((ROOT / path).read_text()) for path in
                    ("strings.json", "translations/en.json", "translations/es.json")]
    def keys(value):
        if isinstance(value, dict):
            return {key: keys(item) for key, item in value.items()}
        return None
    assert keys(translations[0]) == keys(translations[1]) == keys(translations[2])
    for translation in translations:
        assert "relay" not in translation["options"]["step"]["init"]["description"].lower()
        description = translation["options"]["step"]["init"]["description"].lower()
        assert ("thermostat" in description or "termostato" in description)
        assert "heating" in description or "calefacción" in description
        assert "domestic hot water" in description or "agua caliente sanitaria" in description
        assert "2026-11-09" in description
        assert "confirm_boiler_setpoints" in translation["options"]["step"]["init"]["data"]


def test_room_thermostat_api_is_read_only_and_sensors_are_separate():
    api = (ROOT / "api.py").read_text()
    assert '"GET", path' in api
    assert "thermostat_points" in api and "thermostat_points" in (ROOT / "runtime.py").read_text()
    assert "Room temperature" in (ROOT / "sensor.py").read_text()
    assert "Room target" in (ROOT / "sensor.py").read_text()
    assert "Room base target" in (ROOT / "sensor.py").read_text()
    runtime = (ROOT / "runtime.py").read_text()
    assert "control_thermostat" in runtime and "readback did not confirm" in runtime
    assert "Heating modulation" in (ROOT / "sensor.py").read_text()
    assert "PERCENTAGE" in (ROOT / "sensor.py").read_text()


def test_logical_heating_and_acs_devices_preserve_entity_ids():
    sensor = (ROOT / "sensor.py").read_text()
    climate = (ROOT / "climate.py").read_text()
    binary = (ROOT / "binary_sensor.py").read_text()
    assert '"name": "Calefacción"' in sensor and '"name": "Calefacción"' in climate
    assert '"name": "Calefacción"' in binary
    assert '"name": "Agua caliente sanitaria (ACS)"' in sensor
    assert 'f"{device.get(\'slug\')}_dhw"' in sensor
    assert 'f"{device.get(\'slug\')}_{unique_suffix}"' in sensor
    assert 'str(device.get("slug")) + "_heating"' in climate
    assert 'f"{entry.data[\'device\'].get(\'slug\')}_heating_active"' in binary
    assert '"via_device"' not in sensor


def test_legacy_thermostat_cleanup_is_guarded_and_runs_after_setup():
    setup = (ROOT / "__init__.py").read_text()
    assert "async_remove_device(device.id)" in setup
    assert "async_get_device(" not in setup
    assert "device_registry.devices.values()" in setup
    assert setup.index("async_forward_entry_setups(entry, platforms)") < setup.index(
        "_async_remove_legacy_thermostat(hass, entry)"
    )
    assert 'device_data.get("child_devices")' in setup
    assert 'len(children) != 1' in setup
    assert 'child.get("sdid")' in setup and 'isinstance(child.get("sdid"), str)' in setup
    assert 'device.identifiers == {identifier}' in setup
    assert 'if len(candidates) != 1' in setup
    assert 'device.name == "Termostato" or device.name_by_user == "Termostato"' in setup
    assert "device.config_entry_id not in (None, entry.entry_id)" in setup
    assert "entity.device_id == device.id" in setup


def test_saved_override_sensor_is_read_only_and_cleared_on_poll_failure():
    sensor = (ROOT / "sensor.py").read_text()
    runtime = (ROOT / "runtime.py").read_text()
    assert '"room_override_target_temperature"' in sensor
    assert '"Room saved override target"' in sensor
    assert '.publish(' not in sensor
    assert runtime.count('"room_override_target_temperature"') >= 2


def test_legacy_thermostat_cleanup_behavior():
    class Registry:
        def __init__(self, devices):
            self.devices = {device.id: device for device in devices}
            self.removed = []

        def async_remove_device(self, device_id):
            self.removed.append(device_id)

    class Device:
        id = "legacy"
        identifiers = {("ferroli_connect", "child-sdid")}
        name = "Termostato"
        name_by_user = None
        config_entry_id = None

    class Entry:
        entry_id = "current-entry"
        data = {"device": {"child_devices": [{"sdid": "child-sdid"}]}}

    def invoke(device=None, entities=(), children=None, extra_devices=(), owner="current-entry"):
        device = device or Device()
        device.config_entry_id = owner
        registry = Registry([device, *extra_devices])
        entity_registry = types.SimpleNamespace(entities={str(i): entity for i, entity in enumerate(entities)})
        dr_module = types.ModuleType("homeassistant.helpers.device_registry")
        dr_module.async_get = lambda hass: registry
        er_module = types.ModuleType("homeassistant.helpers.entity_registry")
        er_module.async_get = lambda hass: entity_registry
        hass_helpers = types.ModuleType("homeassistant.helpers")
        hass_helpers.device_registry = dr_module
        hass_helpers.entity_registry = er_module
        dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
        dispatcher.async_dispatcher_send = lambda *args: None
        config_entries = types.ModuleType("homeassistant.config_entries")
        config_entries.ConfigEntry = type("ConfigEntry", (), {})
        exceptions = types.ModuleType("homeassistant.exceptions")
        exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
        exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = type("HomeAssistant", (), {})
        ha = types.ModuleType("homeassistant")
        modules = {
            "aiohttp": types.SimpleNamespace(ClientError=type("ClientError", (Exception,), {})),
            "homeassistant": ha,
            "homeassistant.helpers": hass_helpers,
            "homeassistant.helpers.dispatcher": dispatcher,
            "homeassistant.helpers.device_registry": dr_module,
            "homeassistant.helpers.entity_registry": er_module,
            "homeassistant.config_entries": config_entries,
            "homeassistant.exceptions": exceptions,
            "homeassistant.core": core,
        }
        saved = {name: sys.modules.get(name) for name in modules}
        sys.modules.update(modules)
        runtime_name = "custom_components.ferroli_connect.runtime"
        old_runtime = sys.modules.get(runtime_name)
        runtime_stub = types.ModuleType(runtime_name)
        runtime_stub.FerroliRuntime = type("FerroliRuntime", (), {})
        sys.modules[runtime_name] = runtime_stub
        try:
            sys.modules.pop("custom_components.ferroli_connect", None)
            integration = importlib.import_module("custom_components.ferroli_connect")
            entry = Entry()
            entry.entry_id = "current-entry"
            if children is not None:
                entry.data = {"device": {"child_devices": children}}
            integration._async_remove_legacy_thermostat(object(), entry)
            return registry.removed
        finally:
            sys.modules.pop("custom_components.ferroli_connect", None)
            if old_runtime is None:
                sys.modules.pop(runtime_name, None)
            else:
                sys.modules[runtime_name] = old_runtime
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    assert invoke() == ["legacy"]
    assert invoke(owner=None) == ["legacy"]
    assert invoke(children=[]) == []
    assert invoke(children=[{"sdid": "child-sdid"}, {"sdid": "other"}]) == []
    assert invoke(children=[{"sdid": ""}]) == []
    device = Device()
    device.identifiers = {("ferroli_connect", "child-sdid"), ("other", "id")}
    assert invoke(device=device) == []
    device = Device()
    device.name = "Calefacción"
    assert invoke(device=device) == []
    assert invoke(owner="different-entry") == []
    assert invoke(entities=[types.SimpleNamespace(device_id="legacy")]) == []
    duplicate = Device()
    duplicate.id = "duplicate"
    assert invoke(extra_devices=[duplicate]) == []
