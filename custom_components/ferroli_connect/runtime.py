"""Cloud MQTT runtime. Configuration and topic names are validated, never guessed."""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections.abc import Callable

import aiomqtt
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FerroliApi
from .codec import decode_boiler_telemetry, parse_thermostat_points, query_packet
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_BOILER_READBACK_TIMEOUT = 8


def _render(template: str, product: str, device: str, client_id: str) -> str:
    result = (template.replace("{product_code}", product).replace("{device_code}", device)
              .replace("{app_client_id}", client_id))
    if not result or "{" in result or "}" in result:
        raise ValueError("MQTT topic template has unsupported placeholders")
    return result


class FerroliRuntime:
    def __init__(self, hass, entry, on_update: Callable[[dict], None]) -> None:
        self.hass = hass
        self.entry = entry
        self.on_update = on_update
        self.state: dict = {}
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._message_id = 0
        self._client = None
        self._thermostat_task: asyncio.Task | None = None
        self._api = FerroliApi(async_get_clientsession(hass), entry.data["token"])
        self._thermostat_control_lock = asyncio.Lock()
        self._telemetry_condition = asyncio.Condition()
        self._telemetry_generation = 0
        self._last_boiler_update: dict = {}
        self._boiler_control_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._api.current_user()
        mqtt_token, config = await self._api.mqtt_config()
        device = self.entry.data["device"]
        if not all(device.get(key) for key in ("product_code", "device_code")):
            raise ValueError("Selected device lacks product_code/device_code")
        topics = config.get("topic", {})
        one = topics.get("1", {})
        two = topics.get("2", {})
        status_tpl, message_tpl, publish_tpl = one.get("DEV_REALT"), one.get("FROM_DEV"), two.get("TO_DEV")
        if not all(isinstance(v, str) and v for v in (status_tpl, message_tpl, publish_tpl)):
            raise ValueError("MQTT config is missing required dynamic topic templates")
        product, code = device["product_code"], device["device_code"]
        mqtt_user = f"{mqtt_token['user_slug']}$of${mqtt_token['app_code']}"
        self._client_id = f"{int(time.time() * 1000)}_{mqtt_user}"
        self._topics = {_render(status_tpl, product, code, self._client_id),
                        _render(message_tpl, product, code, self._client_id)}
        self._publish_topic = _render(publish_tpl, product, code, self._client_id)
        self._broker = {
            "hostname": config["domain"], "port": int(config["ssl_port"]),
            "username": mqtt_user,
            "password": mqtt_token["token"].encode("utf-8"),
            "tls_context": ssl.create_default_context(),
            "identifier": self._client_id,
        }
        children = device.get("child_devices", [])
        if not isinstance(children, list):
            children = []
        if (len(children) == 1 and isinstance(children[0], dict) and
                isinstance(children[0].get("sdid"), str) and children[0]["sdid"] and
                device.get("device_code")):
            self._thermostat_sdid = children[0]["sdid"]
            self._thermostat_task = asyncio.create_task(
                self._poll_thermostat(), name=f"ferroli-thermostat-{self.entry.entry_id}")
            self._thermostat_task.add_done_callback(self._log_unexpected_task_exit)
        self._task = asyncio.create_task(self._listen(), name=f"ferroli-{self.entry.entry_id}")
        self._task.add_done_callback(self._log_unexpected_task_exit)

    @staticmethod
    def _log_unexpected_task_exit(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.error("Ferroli background task stopped unexpectedly (%s)",
                          type(error).__name__)

    async def _listen(self) -> None:
        delay = 1
        while not self._stopped:
            try:
                async with aiomqtt.Client(**self._broker) as client:
                    self._client = client
                    _LOGGER.info("Ferroli MQTT connected")
                    for topic in self._topics:
                        await client.subscribe(topic, qos=2)
                    _LOGGER.info("Ferroli MQTT status subscriptions acknowledged")
                    await client.publish(self._publish_topic,
                                         query_packet(msg_id=self.next_message_id()),
                                         qos=1)
                    _LOGGER.info("Ferroli status query published (packet type 1)")
                    delay = 1
                    async for message in client.messages:
                        payload = bytes(message.payload)
                        packet_type = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
                        _LOGGER.debug("Ferroli MQTT packet received: type=%s bytes=%d",
                                      packet_type, len(payload))
                        try:
                            update = await self._record_boiler_packet(payload)
                        except ValueError:
                            _LOGGER.debug("Ignoring unrecognized Ferroli MQTT packet: type=%s bytes=%d",
                                          packet_type, len(payload))
                            continue
                        _LOGGER.debug("Ferroli telemetry decoded: fields=%s", tuple(update))
                        self.on_update(dict(self.state))
                    self._client = None
            except asyncio.CancelledError:
                raise
            except (aiomqtt.MqttError, OSError, TimeoutError) as err:
                self._client = None
                if self._stopped:
                    break
                _LOGGER.warning("Ferroli MQTT connection failed (%s); retrying",
                                type(err).__name__)
                try:
                    renewed, _ = await self._api.mqtt_config()
                    if not all(renewed.get(key) for key in ("token", "user_slug", "app_code")):
                        raise ValueError("MQTT token response is incomplete")
                    self._broker["password"] = renewed["token"].encode("utf-8")
                except Exception:
                    # Keep retrying with the last valid token during transient cloud outages.
                    pass
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _poll_thermostat(self) -> None:
        delay = 1
        while not self._stopped:
            try:
                result = await self._api.thermostat_points(
                    str(self.entry.data["device"]["device_code"]), self._thermostat_sdid)
                update = parse_thermostat_points(result)
                for key in ("room_temperature", "room_target_temperature",
                            "room_base_target_temperature", "room_override_target_temperature",
                            "room_mode", "room_manual_target_temperature"):
                    if key in update:
                        self.state[key] = update[key]
                    else:
                        self.state.pop(key, None)
                self.on_update(dict(self.state))
                delay = 60
            except asyncio.CancelledError:
                raise
            except Exception as err:
                for key in ("room_temperature", "room_target_temperature",
                            "room_base_target_temperature", "room_override_target_temperature",
                            "room_mode", "room_manual_target_temperature"):
                    self.state.pop(key, None)
                self.on_update(dict(self.state))
                _LOGGER.warning("Ferroli thermostat poll failed (%s)", type(err).__name__)
                delay = min(max(delay * 2, 2), 60)
            await asyncio.sleep(delay)

    async def publish(self, payload: bytes) -> None:
        if self._client is None:
            raise RuntimeError("Ferroli MQTT is not connected")
        await self._client.publish(self._publish_topic, payload, qos=1)

    async def _record_boiler_packet(self, payload: bytes) -> dict:
        """Update telemetry and notify only for a decoded type-3 response."""
        update = decode_boiler_telemetry(payload)
        self.state.update(update)
        async with self._telemetry_condition:
            self._telemetry_generation += 1
            self._last_boiler_update = dict(update)
            self._telemetry_condition.notify_all()
        return update

    async def control_boiler_target(self, payload: bytes, *, state_key: str,
                                    expected_temperature: int) -> None:
        """Issue one target write and require a fresh MQTT response after QUERY."""
        if not self.entry.options.get("confirm_control", False):
            raise RuntimeError("Ferroli control is not confirmed in integration options")
        async with self._boiler_control_lock:
            if self._client is None:
                raise RuntimeError("Ferroli MQTT is not connected")
            await self.publish(payload)
            async with self._telemetry_condition:
                generation = self._telemetry_generation
            query_id = self.next_message_id()
            await self.publish(query_packet(msg_id=query_id))

            def confirmed() -> bool:
                return (self._telemetry_generation > generation and
                        self._last_boiler_update.get(state_key) == expected_temperature)

            try:
                async with self._telemetry_condition:
                    await asyncio.wait_for(
                        self._telemetry_condition.wait_for(confirmed),
                        timeout=_BOILER_READBACK_TIMEOUT)
            except TimeoutError:
                raise RuntimeError("Boiler target write was not confirmed by fresh MQTT readback") from None

    async def control_thermostat(self, payload: bytes, *, expected_mode: int,
                                 expected_temperature: float | None = None) -> None:
        """Publish one explicit command, then require REST readback without optimistic state."""
        async with self._thermostat_control_lock:
            await self.publish(payload)
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(1)
                points = parse_thermostat_points(await self._api.thermostat_points(
                    str(self.entry.data["device"]["device_code"]), self._thermostat_sdid))
                confirmed = points.get("room_mode") == expected_mode
                if expected_temperature is not None:
                    confirmed = confirmed and points.get("room_manual_target_temperature") == expected_temperature
                if confirmed:
                    for key in ("room_temperature", "room_target_temperature", "room_base_target_temperature",
                                "room_override_target_temperature", "room_mode",
                                "room_manual_target_temperature"):
                        self.state.pop(key, None)
                    self.state.update(points)
                    self.on_update(dict(self.state))
                    return
            raise RuntimeError("Thermostat command was published but readback did not confirm it")

    def next_message_id(self) -> int:
        self._message_id = self._message_id % 255 + 1
        return self._message_id

    async def close(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._thermostat_task:
            self._thermostat_task.cancel()
            await asyncio.gather(self._thermostat_task, return_exceptions=True)
        self._client = None
