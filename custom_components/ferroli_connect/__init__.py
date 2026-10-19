"""Ferroli Connect integration."""
import logging

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .runtime import FerroliRuntime

_LOGGER = logging.getLogger(__name__)


def _legacy_thermostat_child(entry: ConfigEntry) -> dict | None:
    """Return the sole well-formed child eligible for legacy cleanup."""
    device_data = entry.data.get("device")
    if not isinstance(device_data, dict):
        return None
    children = device_data.get("child_devices")
    if not isinstance(children, list) or len(children) != 1:
        return None
    child = children[0]
    if not isinstance(child, dict) or not isinstance(child.get("sdid"), str) or not child["sdid"]:
        return None
    return child


def _async_remove_legacy_thermostat(hass: HomeAssistant, entry: ConfigEntry) -> None:
    child = _legacy_thermostat_child(entry)
    if child is None:
        _LOGGER.debug("Skipping legacy thermostat cleanup: child metadata is not unique and valid")
        return
    identifier = (DOMAIN, child["sdid"])
    device_registry = dr.async_get(hass)
    candidates = [device for device in device_registry.devices.values()
                  if device.identifiers == {identifier}]
    if len(candidates) != 1:
        _LOGGER.debug("Skipping legacy thermostat cleanup: exact identifier candidates=%d",
                      len(candidates))
        return
    device = candidates[0]
    if not (device.name == "Termostato" or device.name_by_user == "Termostato"):
        _LOGGER.debug("Skipping legacy thermostat cleanup: legacy name guard did not match")
        return
    if device.config_entry_id not in (None, entry.entry_id):
        _LOGGER.debug("Skipping legacy thermostat cleanup: device has another config-entry owner")
        return
    entity_registry = er.async_get(hass)
    if any(entity.device_id == device.id for entity in entity_registry.entities.values()):
        _LOGGER.debug("Skipping legacy thermostat cleanup: device has linked entities")
        return
    device_registry.async_remove_device(device.id)
    _LOGGER.info("Removed empty legacy Termostato registry device after successful setup")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    def updated(state):
        async_dispatcher_send(hass, f"{DOMAIN}_{entry.entry_id}_update", state)

    runtime = FerroliRuntime(hass, entry, updated)
    try:
        await runtime.start()
    except PermissionError as err:
        await runtime.close()
        await hass.config_entries.async_start_reauth(entry)
        raise ConfigEntryAuthFailed("Ferroli authentication expired") from err
    except Exception:
        await runtime.close()
        raise
    if runtime._api.token != entry.data["token"]:
        hass.config_entries.async_update_entry(entry, data={**entry.data, "token": runtime._api.token})
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await runtime.close()
        raise
    _async_remove_legacy_thermostat(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = hass.data[DOMAIN].pop(entry.entry_id, None)
        if runtime:
            await runtime.close()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
