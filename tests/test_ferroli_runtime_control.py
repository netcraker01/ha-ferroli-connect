"""Direct asyncio behavioral checks for boiler target readback; no HA/MQTT required."""
import asyncio
import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "ferroli_connect"
sys.path.insert(0, str(ROOT.parent.parent))


def _install_import_stubs():
    package = types.ModuleType("custom_components.ferroli_connect")
    package.__path__ = [str(ROOT)]
    sys.modules["custom_components"] = types.ModuleType("custom_components")
    sys.modules["custom_components.ferroli_connect"] = package

    aiomqtt = types.ModuleType("aiomqtt")
    aiomqtt.Client = object
    aiomqtt.MqttError = type("MqttError", (Exception,), {})
    sys.modules["aiomqtt"] = aiomqtt
    ha = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: None
    sys.modules.update({"homeassistant": ha, "homeassistant.helpers": helpers,
                        "homeassistant.helpers.aiohttp_client": aiohttp_client})
    api = types.ModuleType("custom_components.ferroli_connect.api")
    api.FerroliApi = type("FerroliApi", (), {})
    sys.modules[api.__name__] = api


class _Client:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload, qos):
        self.published.append((bytes(payload), qos))


def _runtime_module():
    _install_import_stubs()
    return importlib.import_module("custom_components.ferroli_connect.runtime")


def _runtime(runtime_module, stale_target=40):
    from custom_components.ferroli_connect.codec import parse_packet

    instance = object.__new__(runtime_module.FerroliRuntime)
    instance.entry = types.SimpleNamespace(options={"confirm_control": True})
    instance.state = {"target_temperature": stale_target}
    instance._boiler_control_lock = asyncio.Lock()
    instance._telemetry_condition = asyncio.Condition()
    instance._telemetry_generation = 0
    instance._last_boiler_update = {}
    instance._client = _Client()
    instance._publish_topic = "unit/topic"
    instance._message_id = 0
    return instance, parse_packet


async def _readback_checks():
    runtime_module = _runtime_module()
    from custom_components.ferroli_connect.codec import target_command, tlv
    runtime_module._BOILER_READBACK_TIMEOUT = 0.03
    expected = 42

    # Cached matching state and a non-response packet cannot confirm the write.
    runtime, parse_packet = _runtime(runtime_module, stale_target=expected)
    task = asyncio.create_task(runtime.control_boiler_target(
        target_command(expected, timestamp_ms=0), state_key="target_temperature",
        expected_temperature=expected))
    while len(runtime._client.published) < 2:
        await asyncio.sleep(0)
    assert [int.from_bytes(packet[:2], "big")
            for packet, _ in runtime._client.published] == [2, 1]
    control_response = target_command(expected, timestamp_ms=0)
    try:
        await runtime._record_boiler_packet(control_response)
    except ValueError:
        pass
    else:
        raise AssertionError("type-2 packet was accepted as a readback")
    try:
        await task
    except RuntimeError as error:
        assert "not confirmed" in str(error)
    else:
        raise AssertionError("stale target state confirmed write")
    assert len(runtime._client.published) == 2  # one control, one query; no retry

    # A fresh decoded type-3 update carrying the target key confirms the same write.
    runtime, parse_packet = _runtime(runtime_module, stale_target=expected)
    task = asyncio.create_task(runtime.control_boiler_target(
        target_command(expected, timestamp_ms=0), state_key="target_temperature",
        expected_temperature=expected))
    while len(runtime._client.published) < 2:
        await asyncio.sleep(0)
    prefix = bytes.fromhex("0003130101") + bytes(16) + b"\x0c"
    response = prefix + tlv(0x200B, (expected * 10).to_bytes(2, "big"))
    await runtime._record_boiler_packet(response)
    await task
    assert [int.from_bytes(packet[:2], "big")
            for packet, _ in runtime._client.published] == [2, 1]
    assert len(runtime._client.published) == 2


if __name__ == "__main__":
    asyncio.run(_readback_checks())
    print("Ferroli runtime readback checks passed")
