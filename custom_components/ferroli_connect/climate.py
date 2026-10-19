"""Room thermostat control, separate from boiler water and relay controls."""
import time

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .codec import thermostat_mode_command, thermostat_temperature_command
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FerroliHeating(hass, entry)])


class FerroliHeating(ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = "Room thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature(0)
    _attr_available = False

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self._state = {}
        self._attr_hvac_mode = None
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            if entry.options.get("confirm_control", False) else ClimateEntityFeature(0))
        device = entry.data["device"]
        children = device.get("child_devices", [])
        self.sdid = children[0].get("sdid") if len(children) == 1 and isinstance(children[0], dict) else None
        self._attr_unique_id = str(device.get("slug")) + "_heating"
        self._attr_device_info = {"identifiers": {(DOMAIN, str(device.get("slug")))},
                                  "name": "Calefacción", "manufacturer": "Ferroli"}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update))
        self._handle_update(self.runtime.state)

    @callback
    def _handle_update(self, state):
        self._state = dict(state)
        mode = state.get("room_mode")
        self._attr_available = "room_temperature" in state
        self._attr_current_temperature = state.get("room_temperature")
        if mode in (1, 4):
            self._attr_target_temperature = state.get("room_manual_target_temperature")
        elif mode == 0:
            self._attr_target_temperature = state.get("room_target_temperature")
        else:
            self._attr_target_temperature = None
        self._attr_hvac_mode = HVACMode.OFF if mode == 4 else HVACMode.HEAT if mode == 1 else None
        self.async_write_ha_state()

    def _header(self):
        return {"msg_id": self.runtime.next_message_id(),
                "timezone_offset_hours": max(0, min(255, 12 - int(time.timezone / 3600)))}

    async def _control_allowed(self):
        if not self.entry.options.get("confirm_control", False):
            raise RuntimeError("Enable thermostat control in integration options first")
        if self.sdid is None:
            raise RuntimeError("A single thermostat child device is required")

    async def async_set_temperature(self, **kwargs):
        await self._control_allowed()
        if self._state.get("room_mode") not in (1, 4):
            raise RuntimeError("Temperature changes require known OFF or manual mode")
        if self._state.get("room_manual_target_temperature") is None:
            raise RuntimeError("A valid saved manual target is required before changing temperature")
        value = kwargs.get("temperature")
        await self.runtime.control_thermostat(
            thermostat_temperature_command(self.sdid, value, **self._header()),
            expected_mode=self._state["room_mode"], expected_temperature=float(value))

    async def async_set_hvac_mode(self, hvac_mode):
        await self._control_allowed()
        mode = self._state.get("room_mode")
        if hvac_mode == HVACMode.HEAT:
            if mode not in (1, 4) or self._state.get("room_manual_target_temperature") is None:
                raise RuntimeError("Manual heat requires known OFF/manual mode and saved target")
            wire_mode = 1
        elif hvac_mode == HVACMode.OFF:
            if mode not in (1, 4):
                raise RuntimeError("OFF can only be sent from known OFF/manual mode")
            wire_mode = 4
        else:
            raise ValueError("Only explicit manual heat and off are supported")
        await self.runtime.control_thermostat(
            thermostat_mode_command(self.sdid, wire_mode, **self._header()), expected_mode=wire_mode)
