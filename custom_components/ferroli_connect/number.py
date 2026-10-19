"""Explicit, readback-confirmed boiler water target controls."""
import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .codec import dhw_target_command, target_command
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    if not entry.options.get("confirm_control", False):
        return
    async_add_entities([
        FerroliWaterTarget(hass, entry, "target_temperature", "min_temperature",
                           "max_temperature", "heating_water_target_setpoint", "Heating water setpoint",
                           target_command, False),
        FerroliWaterTarget(hass, entry, "dhw_target_temperature", "dhw_min_temperature",
                           "dhw_max_temperature", "domestic_hot_water_target_setpoint",
                           "Domestic hot water setpoint", dhw_target_command, True),
    ])


class FerroliWaterTarget(NumberEntity):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_has_entity_name = True

    def __init__(self, hass, entry, state_key, min_key, max_key, suffix, name, encoder, dhw):
        self.hass, self.entry = hass, entry
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self.state_key, self.min_key, self.max_key = state_key, min_key, max_key
        self.encoder, self.dhw = encoder, dhw
        self._attr_name = name
        self._attr_unique_id = f"{entry.data['device'].get('slug')}_{suffix}"
        if dhw:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{entry.data['device'].get('slug')}_dhw")},
                "name": "Agua caliente sanitaria (ACS)", "manufacturer": "Ferroli",
            }
        else:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, str(entry.data['device'].get('slug')))},
                "name": "Calefacción", "manufacturer": "Ferroli",
            }
        self._attr_available = False

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update))
        self._handle_update(self.runtime.state)

    @callback
    def _handle_update(self, state):
        value, minimum, maximum = (state.get(self.state_key), state.get(self.min_key),
                                   state.get(self.max_key))
        valid = all(isinstance(item, (int, float)) and not isinstance(item, bool)
                    and math.isfinite(item) for item in (value, minimum, maximum))
        self._attr_available = bool(valid and minimum > 0 and maximum >= minimum)
        self._attr_native_value = value if self._attr_available else None
        if self._attr_available:
            self._attr_native_min_value = minimum
            self._attr_native_max_value = maximum
        self.async_write_ha_state()

    async def async_set_native_value(self, value):
        if not self.entry.options.get("confirm_control", False):
            raise RuntimeError("Ferroli control is not confirmed in integration options")
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not float(value).is_integer()):
            raise ValueError("Target must be a finite whole number of Celsius")
        state = self.runtime.state
        minimum, maximum = state.get(self.min_key), state.get(self.max_key)
        if (not self._attr_available or not isinstance(minimum, (int, float)) or
                not isinstance(maximum, (int, float)) or not minimum <= value <= maximum):
            raise ValueError("Target must be within the live boiler temperature limits")
        if state.get(self.state_key) == int(value):
            return
        await self.runtime.control_boiler_target(
            self.encoder(int(value)), state_key=self.state_key,
            expected_temperature=int(value))
