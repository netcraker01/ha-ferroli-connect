"""Small async client for the API contract observed in Ferroli Connect 1.5.2."""
from __future__ import annotations

import aiohttp
from urllib.parse import quote

from .const import APP_SLUG, BASE_URL


class FerroliApi:
    def __init__(self, session: aiohttp.ClientSession, token: str | None = None) -> None:
        self.session = session
        self.token: str | None = None
        self.token = token
        self.user: dict = {}

    async def request(self, method: str, path: str, *, params=None, data=None, auth=True):
        for attempt in range(2):
            headers = {"APPSLUG": APP_SLUG}
            if auth and self.token:
                headers["Authorization"] = f"JWT {self.token}"
            timeout = aiohttp.ClientTimeout(total=20)
            async with self.session.request(method, BASE_URL + path, headers=headers, timeout=timeout,
                                            params=params, data=data) as response:
                if response.status in (401, 403) and auth and attempt == 0:
                    await self.refresh()
                    continue
                if response.status in (401, 403):
                    raise PermissionError("Ferroli authentication failed")
                response.raise_for_status()
                return await response.json()
        raise PermissionError("Ferroli authentication failed")

    async def login(self, username: str, password: str):
        result = await self.request("POST", "api/token", data={"username": username,
                                    "password": password}, auth=False)
        self.token = result.get("token") or result.get("access")
        if not self.token:
            raise ValueError("Login response did not contain an access token")
        return await self.current_user()

    async def current_user(self):
        self.user = await self.request("GET", "api/current_user")
        return self.user

    async def devices(self):
        slug = self.user.get("slug")
        if not slug:
            raise ValueError("current_user response has no slug")
        return await self.request("GET", f"api/users/{slug}/devices",
                                  params={"page_size": 100, "page": 1, "app_slug": APP_SLUG})

    async def mqtt_config(self):
        params = {"app_slug": APP_SLUG}
        token = await self.request("GET", "api/mqtt/token", params=params)
        config = await self.request("GET", "api/mqtt/app", params=params)
        return token, config

    async def thermostat_points(self, wifi_box_code: str, sdid: str):
        """Read thermostat room-current and target points; never sends controls."""
        if not wifi_box_code or not sdid:
            raise ValueError("Thermostat identifiers are required")
        path = ("apps/topiot_ferroli_temperature_controller/thermostat_points/"
                f"{quote(str(wifi_box_code), safe='')}/{quote(str(sdid), safe='')}")
        return await self.request("GET", path)

    async def refresh(self):
        result = await self.request("POST", "api/token/refashion", data={"token": self.token}, auth=False)
        refreshed = result.get("token")
        if not refreshed:
            raise ValueError("Token refresh response has no token")
        self.token = refreshed
