"""Read-only boiler water temperature sensors."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


SENSORS = (
    ("current_temperature", "heating_water_temperature", "Heating water temperature"),
    ("target_temperature", "heating_water_target", "Heating water target"),
    ("dhw_current_temperature", "domestic_hot_water_temperature", "Domestic hot water temperature"),
    ("dhw_target_temperature", "domestic_hot_water_target", "Domestic hot water target"),
    ("room_temperature", "room_temperature", "Room temperature"),
    ("room_target_temperature", "room_target_temperature", "Room target"),
    ("room_base_target_temperature", "room_base_target_temperature", "Room base target"),
    ("room_override_target_temperature", "room_override_target_temperature", "Room saved override target"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FerroliWaterSensor(hass, entry, *description)
                        for description in SENSORS
                        if description[0] != "room_override_active"])


class FerroliWaterSensor(SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_has_entity_name = True

    def __init__(self, hass, entry, state_key, unique_suffix, name):
        self.hass = hass
        self.entry = entry
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self.state_key = state_key
        self._attr_name = name
        if state_key in ("current_temperature", "dhw_current_temperature", "room_temperature"):
            self._attr_state_class = SensorStateClass.MEASUREMENT
        device = entry.data["device"]
        self._attr_unique_id = f"{device.get('slug')}_{unique_suffix}"
        if state_key.startswith("dhw_"):
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{device.get('slug')}_dhw")},
                "name": "Agua caliente sanitaria (ACS)",
                "manufacturer": "Ferroli",
            }
        else:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, str(device.get("slug")))},
                "name": "Calefacción",
                "manufacturer": "Ferroli",
            }
        self._attr_native_value = None
        self._attr_available = False

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update))
        self._handle_update(self.runtime.state)

    @callback
    def _handle_update(self, state):
        value = state.get(self.state_key)
        self._attr_available = isinstance(value, (int, float)) and not isinstance(value, bool)
        self._attr_native_value = value if self._attr_available else None
        self.async_write_ha_state()
