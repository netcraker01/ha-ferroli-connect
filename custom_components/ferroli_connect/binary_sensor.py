"""Read-only boiler activity sensors."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FerroliHeatingActive(hass, entry)])


class FerroliHeatingActive(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Heating active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self._attr_unique_id = f"{entry.data['device'].get('slug')}_heating_active"
        device = entry.data["device"]
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(device.get("slug")))},
            "name": "Calefacción",
            "manufacturer": "Ferroli",
        }
        self._attr_is_on = None
        self._attr_available = False

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update))
        self._handle_update(self.runtime.state)

    @callback
    def _handle_update(self, state):
        valid = state.get("heating_active")
        self._attr_available = isinstance(valid, bool)
        self._attr_is_on = valid if isinstance(valid, bool) else None
        self.async_write_ha_state()
