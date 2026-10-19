"""UI setup flow."""
from __future__ import annotations

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant import config_entries
from homeassistant.core import callback

from .api import FerroliApi
from .const import CONF_DEVICE, DOMAIN


class FerroliConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_reauth(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors = {}
        if user_input:
            api = FerroliApi(async_get_clientsession(self.hass))
            try:
                await api.login(user_input["username"], user_input["password"])
                return self.async_update_reload_and_abort(entry, data_updates={
                    "username": user_input["username"], "token": api.token})
            except Exception:
                errors["base"] = "invalid_auth"
        return self.async_show_form(step_id="reauth", data_schema=vol.Schema({
            vol.Required("username", default=entry.data.get("username", "")): str,
            vol.Required("password"): str}), errors=errors)

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            api = FerroliApi(async_get_clientsession(self.hass))
            try:
                user = await api.login(user_input["username"], user_input["password"])
                devices = await api.devices()
                rows = devices.get("results", []) if isinstance(devices, dict) else []
                if not rows:
                    errors["base"] = "no_devices"
                else:
                    self._api_data = {"username": user_input["username"], "token": api.token,
                                      "user_slug": user["slug"], "devices": rows}
                    return await self.async_step_device()
            except (ClientError, TimeoutError, PermissionError, ValueError, KeyError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("username"): str, vol.Required("password"): str}), errors=errors)

    async def async_step_device(self, user_input=None):
        devices = self._api_data["devices"]
        options = {str(item.get("slug")): f"{item.get('name', 'Ferroli device')} ({item.get('type', 'device')})"
                   for item in devices if item.get("slug")}
        if user_input:
            device = next(row for row in devices if str(row.get("slug")) == user_input[CONF_DEVICE])
            return self.async_create_entry(title=device.get("name", "Ferroli Connect"),
                                           data={"username": self._api_data["username"],
                                                 "token": self._api_data["token"],
                                                 "user_slug": self._api_data["user_slug"],
                                                 CONF_DEVICE: device})
        return self.async_show_form(step_id="device", data_schema=vol.Schema({
            vol.Required(CONF_DEVICE): vol.In(options)}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FerroliOptionsFlow()


class FerroliOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("confirm_control", default=False): bool}))
